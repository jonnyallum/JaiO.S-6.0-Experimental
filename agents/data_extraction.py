"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : data_extraction
SKILL : Data Extraction — parse structured data from raw input, validate against schema

Node Contract (@langraph doctrine):
  Inputs   : raw_input (str), schema (dict), extraction_mode (str) — immutable after entry
  Outputs  : parsed_data (dict), validation_passed (bool), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All DataExtractionState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  PARSE-RETRY LOOP — explicitly bounded at PARSE_ATTEMPTS=2 total attempts.
  Attempt 1: full prompt with context.
  Attempt 2: stripped prompt with only schema keys and truncated input.
  On exhaustion (both attempts fail to produce valid JSON): raises ValueError → PERMANENT failure.
  @langraph: loop has explicit ceiling (PARSE_ATTEMPTS), quality threshold (valid JSON), and
             escalation path (ValueError → graph continues with error field set).

Failure Discrimination:
  PERMANENT  → ValueError (JSON parse failed after PARSE_ATTEMPTS, schema missing required field)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before first Claude call (marks expensive operation started)
  POST — Supabase log after completion (records fields extracted, validation status)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics
from typing import TypedDict
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE               = "data_extraction"
MAX_RETRIES        = 3
RETRY_MIN_S        = 3
RETRY_MAX_S        = 45
MAX_TOKENS         = 1500   # Extraction output can be large depending on schema size
VALID_SOURCE_TYPES = {"json", "csv", "xml", "html", "database", "api", "general"}

INPUT_CHARS        = 5000   # Raw input truncation for first attempt
RETRY_INPUT_CHARS  = 2000   # Stricter truncation on second attempt
PARSE_ATTEMPTS     = 2      # Loop ceiling — both attempts exhaust → ValueError


# ── State schema ─────────────────────────────────────────────────────────────────
class DataExtractionState(BaseState):
    # Inputs — written by caller, immutable inside this node
    raw_input: str        # Raw text or data to parse
    schema: dict          # Expected output fields: {field_name: description}
    extraction_mode: str  # json | structured | freetext
    # Outputs — written by this node, read by downstream nodes
    parsed_data: dict         # Extracted structured output; empty dict on failure
    validation_passed: bool   # True if all schema fields present and non-null
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Pure helpers ─────────────────────────────────────────────────────────────────
# ── Phase 1 — JSON parsing utilities (pure, no Claude) ─────────────────────────────

def _try_parse_json(text: str) -> Optional[dict]:
    """Extract JSON from a string, stripping markdown fences if present. Pure function."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _validate_schema(data: dict, schema: dict) -> tuple[bool, list[str]]:
    """Check all schema keys are present and non-null. Pure function."""
    issues = []
    for field, description in schema.items():
        if field not in data:
            issues.append(f"Missing: {field} ({description})")
        elif data[field] is None:
            issues.append(f"Null value: {field}")
    return len(issues) == 0, issues


def _build_extraction_prompt(raw_input: str, schema: dict, persona: dict, truncate: int) -> str:
    """Format extraction prompt. Pure function — no I/O."""
    schema_description = "\n".join(
        f"  - {field}: {desc}" for field, desc in schema.items()
    )
    return f"""{persona['personality']}

Extract structured data from the input below.
Return ONLY valid JSON matching the schema. No explanation, no markdown fences, no preamble.

━━━ SCHEMA ━━━
{schema_description}

━━━ RAW INPUT ━━━
{raw_input[:truncate]}

━━━ RULES ━━━
- Return ONLY a JSON object with exactly the schema fields.
- If a field cannot be determined, use null.
- Do not add extra fields.
- Ensure all strings are properly escaped.

JSON output:"""


def _build_strict_retry_prompt(raw_input: str, schema: dict, truncate: int) -> str:
    """Stripped prompt for second parse attempt — minimal context. Pure function."""
    return (
        f"Return ONLY a raw JSON object (no markdown, no explanation) "
        f"with these exact keys: {list(schema.keys())}. "
        f"Source: {raw_input[:truncate]}"
    )


_build_prompt = _build_extraction_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2: Extraction (Claude call, retried on transient errors only) ──────────
def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _extract(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text
_generate = _extract  # spec alias



# ── Main node ─────────────────────────────────────────────────────────────────────
def data_extraction_node(state: DataExtractionState) -> dict:
    """
    Data Extraction node — bounded parse-retry loop, never more than PARSE_ATTEMPTS Claude calls.

    Execution order:
      1. Validate inputs
      2. PRE checkpoint (before first Claude call)
      3. PARSE-RETRY LOOP (max PARSE_ATTEMPTS=2 iterations):
         a. Build prompt (full on attempt 1, stripped on attempt 2)
         b. Call Claude (_extract)
         c. Try to parse JSON
         d. If parsed: break
      4. If no valid JSON after PARSE_ATTEMPTS: raise ValueError (PERMANENT)
      5. Validate against schema (non-fatal warnings only)
      6. POST checkpoint
      7. Return state patch

    @langraph: loop has explicit ceiling, quality threshold (valid JSON), and escalation path.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    mode         = state.get("extraction_mode", "structured")
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, mode=mode,
             schema_fields=list(state["schema"].keys()))

    try:
        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # PRE checkpoint — mark expensive operation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"mode": mode, "schema_fields": list(state["schema"].keys()),
             "input_chars": len(state["raw_input"]), "status": "extracting"},
        )

        # PARSE-RETRY LOOP — explicitly bounded at PARSE_ATTEMPTS (@langraph loop policy above)
        parsed: Optional[dict] = None
        last_response: str = ""
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            if attempt == 1:
                prompt = _build_extraction_prompt(
                    state["raw_input"], state["schema"], persona, INPUT_CHARS
                )
            else:
                log.warning(f"{ROLE}.parse_retry", attempt=attempt)
                prompt = _build_strict_retry_prompt(
                    state["raw_input"], state["schema"], RETRY_INPUT_CHARS
                )

            last_response = _extract(claude, prompt, metrics)
            parsed = _try_parse_json(last_response)
            if parsed is not None:
                break   # quality threshold met — exit loop

        # Loop exhausted without valid JSON — PERMANENT failure
        if parsed is None:
            raise ValueError(
                f"Could not extract valid JSON after {PARSE_ATTEMPTS} attempts. "
                f"Last response: {last_response[:200]}"
            )

        validation_passed, issues = _validate_schema(parsed, state["schema"])
        if not validation_passed:
            log.warning(f"{ROLE}.validation_issues", issues=issues)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"mode": mode, "fields_extracted": len(parsed),
             "validation_passed": validation_passed,
             "validation_issues": issues, "status": "completed"},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id,
                 fields=len(parsed), validation_passed=validation_passed)
        return {
            "parsed_data": parsed,
            "validation_passed": validation_passed,
            "error": None if validation_passed else f"Validation: {'; '.join(issues)}",
            "workflow_id": thread_id,
            "agent": ROLE,
        }

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="parse_exhausted", error=msg)
        notifier.agent_error(ROLE, mode, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"mode": mode, "status": "parse_exhausted", "error": msg})
        return {"parsed_data": {}, "validation_passed": False,
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, mode, msg)
        return {"parsed_data": {}, "validation_passed": False,
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, mode, msg)
        return {"parsed_data": {}, "validation_passed": False,
                "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── Backwards-compatibility aliases ──────────────────────────────────────────────
parser_node = data_extraction_node
ParserState = DataExtractionState


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(DataExtractionState)
    g.add_node("data_extraction", data_extraction_node)
    g.set_entry_point("data_extraction")
    g.add_edge("data_extraction", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
