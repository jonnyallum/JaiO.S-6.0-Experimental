"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : sales_conversion
SKILL : Sales Conversion — prospect profile + objections → close strategy, scripts, next actions

Node Contract (@langraph doctrine):
  Inputs   : prospect_name (str), company (str), deal_stage (str),
             context (str), objections (str) — immutable after entry
  Outputs  : close_strategy (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All SalesConversionState fields are thread-scoped only.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).

Failure Discrimination:
  PERMANENT  → ValueError (missing prospect_name, context)
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
  UNEXPECTED → Exception

Checkpoint Semantics:
  PRE  — before Claude call
  POST — after completion

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics
from typing import TypedDict

log = structlog.get_logger()

ROLE          = "sales_conversion"
MAX_RETRIES   = 3
RETRY_MIN_S   = 3
RETRY_MAX_S   = 45
MAX_TOKENS    = 1600
CONTEXT_CHARS = 2500

VALID_STAGES = {
    "cold",        # No prior contact
    "aware",       # Prospect knows us
    "engaged",     # Had a conversation
    "proposal",    # Proposal sent
    "negotiation", # Active back-and-forth
    "stalled",     # Gone quiet
}


class SalesConversionState(BaseState):
    prospect_name: str   # Decision-maker's name
    company: str         # Company or organisation
    deal_stage: str      # cold | aware | engaged | proposal | negotiation | stalled
    context: str         # What they need, budget signals, pain points, past interactions
    objections: str      # Specific objections raised (price, timing, competition, trust)
    close_strategy: str  # Full close playbook; empty on failure


# ── Phase 1 — prompt construction (pure, no I/O) ───────────────────────────────────

def _build_close_prompt(state: "SalesConversionState", persona: dict) -> str:
    objections_block = (
        f"\n━━━ OBJECTIONS RAISED ━━━\n{state['objections']}"
        if state.get("objections", "").strip()
        else "\n(No specific objections provided — anticipate common ones for this stage.)"
    )
    return f"""{persona['personality']}

Build a specific close strategy for the prospect below.
Every tactic must be exact — word-for-word scripts, specific follow-up timing, concrete CTAs.
No generic sales advice. This must be ready to execute today. Max 650 words.

━━━ PROSPECT ━━━
Name    : {state['prospect_name']}
Company : {state.get('company') or 'Unknown'}
Stage   : {state['deal_stage'].upper()}

━━━ CONTEXT ━━━
{state['context'][:CONTEXT_CHARS]}{objections_block}

━━━ DELIVER ━━━

## Close Strategy: {state['prospect_name']} @ {state.get('company', 'Unknown')}

### Situation Assessment
[One paragraph: where they are in the buying journey, what's blocking the close]

### Primary Close Tactic
[The single most effective move for this stage. Why it works for this prospect.]

### Objection Responses
[For each objection listed: exact reframe script, one-two sentences, no waffle]

### Follow-Up Sequence
| Day | Action | Message hook | Expected outcome |
|---|---|---|---|
[3-5 steps. Specific timing, specific channel (call/email/LinkedIn)]

### The CTA
[Exact wording of the call to action to send/say next]

### Deal Killers to Avoid
[2-3 mistakes that would lose this deal at this stage]

### Success Signal
[What the prospect will say/do when they're ready to close]"""


_build_prompt = _build_close_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2 — Claude call (TRANSIENT errors retried) ────────────────────────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def sales_conversion_node(state: SalesConversionState) -> dict:
    thread_id     = state.get("workflow_id") or str(uuid.uuid4())
    prospect_name = state.get("prospect_name", "")
    persona       = get_persona(ROLE)
    notifier      = TelegramNotifier()
    state_logger  = SupabaseStateLogger()
    metrics       = CallMetrics(thread_id, ROLE)

    def _checkpoint(cid: str, payload: dict) -> None:
        state_logger.log_state(thread_id, cid, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, prospect=prospect_name)

    try:
        if not prospect_name.strip():
            raise ValueError("prospect_name is required.")
        if not state.get("context", "").strip():
            raise ValueError("context is required — describe the prospect's situation and needs.")
        deal_stage = state.get("deal_stage", "cold")
        if deal_stage not in VALID_STAGES:
            raise ValueError(
                f"Invalid deal_stage '{deal_stage}'. Must be one of: {', '.join(sorted(VALID_STAGES))}"
            )

        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_close_prompt(state, persona)

        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"prospect": prospect_name, "stage": deal_stage, "status": "generating"},
        )

        close_strategy = _generate(claude, prompt, metrics)
        metrics.log()
        metrics.persist()

        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"prospect": prospect_name, "status": "completed", "chars": len(close_strategy)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, chars=len(close_strategy))
        return {"close_strategy": close_strategy, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", error=msg)
        notifier.agent_error(ROLE, prospect_name, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"prospect": prospect_name, "status": "permanent_failure", "error": msg})
        return {"close_strategy": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", error=msg)
        notifier.agent_error(ROLE, prospect_name, msg)
        return {"close_strategy": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", error=msg)
        notifier.agent_error(ROLE, prospect_name, msg)
        return {"close_strategy": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}
