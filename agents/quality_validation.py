"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : quality_validation
SKILL : Quality Validation — score an artifact against its original request, PASS/FAIL gate

Node Contract (@langraph doctrine):
  Inputs   : artifact (str), artifact_type (str), original_query (str) — immutable after entry
  Outputs  : quality_score (int), quality_passed (bool), quality_feedback (str), error (str|None)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on FAIL [non-fatal]

Thread Memory (checkpoint-scoped):
  All QualityValidationState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (score parsing fails after fallback)
               No retry. Returns quality_score=0. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks expensive operation started)
  POST — Supabase log after completion (records score, pass/fail status)

Gate Alert:
  TelegramNotifier fires if quality_passed=False. Non-fatal — placed outside try block.

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

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
ROLE           = "quality_validation"
MAX_RETRIES    = 3
RETRY_MIN_S    = 3
RETRY_MAX_S    = 45
MAX_TOKENS     = 1000   # Validation feedback — structured, concise output
VALID_ARTIFACT_TYPES = {"intelligence_report", "security_audit", "code", "plan", "copy", "general"}

ARTIFACT_CHARS = 5000   # Artifact truncation limit
PASS_THRESHOLD = 7      # Scores >= 7 pass the quality gate


# ── State schema ─────────────────────────────────────────────────────────────────
class QualityValidationState(BaseState):
    # Inputs — written by caller, immutable inside this node
    artifact: str          # The output to review
    artifact_type: str     # intelligence_report | security_audit | code | plan | copy
    original_query: str    # The original request that produced the artifact
    # Outputs — written by this node, read by downstream nodes
    quality_score: int        # 1-10; 0 on failure
    quality_passed: bool      # True if quality_score >= PASS_THRESHOLD
    quality_feedback: str     # Structured feedback with improvement actions; empty on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Pure helpers ─────────────────────────────────────────────────────────────────
# ── Phase 1 — score parsing utilities (pure) ───────────────────────────────────────

def _parse_score(feedback: str) -> int:
    """
    Extract the overall score from Claude's structured response. Pure function.
    Scans for '**Overall Score:** X/10' pattern.
    Returns default 5 if not found (conservative — neither pass nor hard fail).
    """
    for line in feedback.split("\n"):
        if "Overall Score:" in line:
            try:
                score_part = line.split("Overall Score:")[1].strip()
                score = int(score_part.split("/")[0].strip())
                return max(1, min(10, score))
            except (ValueError, IndexError):
                pass
    return 5  # default: mid-score, conservative


def _build_validation_prompt(
    artifact: str,
    artifact_type: str,
    original_query: str,
    persona: dict,
    truncate: int,
    pass_threshold: int,
) -> str:
    """Format the quality assessment prompt. Pure function — no I/O."""
    return f"""{persona['personality']}

Evaluate this {artifact_type} against the original request. Be strict — we ship nothing below {pass_threshold}/10.

━━━ ORIGINAL REQUEST ━━━
{original_query}

━━━ ARTIFACT ({artifact_type}) ━━━
{artifact[:truncate]}

━━━ EVALUATION CRITERIA ━━━
Score each 1-10:
1. Completeness — fully addresses the request, no missing sections
2. Accuracy — factually correct and specific
3. Actionability — recommendations are concrete and implementable
4. Clarity — well-structured and free of fluff
5. Production Readiness — deliverable to client without changes

━━━ REQUIRED OUTPUT FORMAT ━━━
## Quality Assessment
**Overall Score:** X/10
**Status:** PASS / FAIL

### Dimension Scores
- Completeness: X/10
- Accuracy: X/10
- Actionability: X/10
- Clarity: X/10
- Production Readiness: X/10

### Issues Found
[Specific problems, or "None"]

### Required Improvements
[Numbered list to reach 9/10. If passing: "None"]

### Verdict
[One sentence]"""


_build_prompt = _build_validation_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2: Validation (Claude call, retried on transient errors only) ──────────
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
def _validate(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text
_generate = _validate  # spec alias



# ── Main node ─────────────────────────────────────────────────────────────────────
def quality_validation_node(state: QualityValidationState) -> dict:
    """
    Quality Validation node — single pass, no loop.

    Execution order:
      1. Build prompt (Phase 1 — pure function)
      2. PRE checkpoint (before Claude call)
      3. Validate (Phase 2 — Claude call)
      4. Parse score (_parse_score — pure function)
      5. POST checkpoint
      6. Gate alert if FAIL (non-fatal, outside try block)
      7. Return state patch

    @langraph: show me the checkpoint before you call production-ready.
    """
    thread_id     = state.get("workflow_id") or str(uuid.uuid4())
    artifact_type = state.get("artifact_type", "output")
    persona       = get_persona(ROLE)
    notifier      = TelegramNotifier()
    state_logger  = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, artifact_type=artifact_type,
             artifact_chars=len(state["artifact"]))

    quality_score    = 0
    quality_passed   = False
    quality_feedback = ""

    try:
        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # Build prompt (pure — no I/O)
        prompt = _build_validation_prompt(
            state["artifact"], artifact_type, state["original_query"],
            persona, ARTIFACT_CHARS, PASS_THRESHOLD,
        )

        # PRE checkpoint — mark expensive operation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"artifact_type": artifact_type, "artifact_chars": len(state["artifact"]),
             "status": "validating"},
        )

        # Phase 2 — validate (TRANSIENT failures retried by tenacity)
        quality_feedback = _validate(claude, prompt, metrics)

        # Parse score (pure function — no Claude re-call on parse failure)
        quality_score  = _parse_score(quality_feedback)
        quality_passed = quality_score >= PASS_THRESHOLD

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"artifact_type": artifact_type, "quality_score": quality_score,
             "quality_passed": quality_passed, "status": "completed"},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id,
                 score=quality_score, passed=quality_passed)

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="parse_error", error=msg)
        notifier.agent_error(ROLE, artifact_type, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"artifact_type": artifact_type, "status": "parse_error", "error": msg})
        return {"quality_score": 0, "quality_passed": False, "quality_feedback": "",
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, artifact_type, msg)
        return {"quality_score": 0, "quality_passed": False, "quality_feedback": "",
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, artifact_type, msg)
        return {"quality_score": 0, "quality_passed": False, "quality_feedback": "",
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── Gate alert — non-fatal, fires after successful validation only ────────────
    if not quality_passed:
        notifier.alert(
            f"⚠️ Quality gate FAILED\n"
            f"Score: {quality_score}/10 | Type: {artifact_type}\n"
            f"Workflow: <code>{thread_id[:8]}</code>"
        )

    return {
        "quality_score": quality_score,
        "quality_passed": quality_passed,
        "quality_feedback": quality_feedback,
        "error": None,
        "workflow_id": thread_id,
        "agent": ROLE,
    }


# ── Backwards-compatibility aliases ──────────────────────────────────────────────
qualityguard_node = quality_validation_node
QualityGuardState = QualityValidationState


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(QualityValidationState)
    g.add_node("quality_validation", quality_validation_node)
    g.set_entry_point("quality_validation")
    g.add_edge("quality_validation", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
