"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : monetisation_strategist
SKILL : Monetisation Strategy — business context + goals → revenue blueprint, pricing, funnel design

Node Contract (@langraph doctrine):
  Inputs   : client_name (str), business_context (str), current_revenue (str),
             goals (str), constraints (str) — immutable after entry
  Outputs  : strategy (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All MonetisationState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (missing client_name, business_context, goals)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks strategy generation started)
  POST — Supabase log after completion (records strategy size)

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
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

# ── Budget constants ──────────────────────────────────────────────────────────────
ROLE             = "monetisation_strategist"
MAX_RETRIES      = 3
RETRY_MIN_S      = 3
RETRY_MAX_S      = 45
MAX_TOKENS       = 1800   # Revenue blueprints need depth — pricing tables, funnel steps
VALID_BUSINESS_MODELS = {"saas", "ecommerce", "marketplace", "agency", "content", "general"}

CONTEXT_CHARS    = 3000
GOALS_CHARS      = 1000


# ── State schema ─────────────────────────────────────────────────────────────────
class MonetisationState(BaseState):
    # Inputs — written by caller, immutable inside this node
    client_name: str        # Business or product name
    business_context: str   # Industry, current model, what they sell, audience
    current_revenue: str    # Current MRR/ARR or "pre-revenue" — context for realistic advice
    goals: str              # Revenue target, timeline, growth ambitions
    constraints: str        # Budget limits, team size, tech stack constraints
    # Outputs — written by this node
    strategy: str           # Full monetisation blueprint; empty string on failure


# ── Prompt builder ───────────────────────────────────────────────────────────────
# ── Phase 1 — prompt construction (pure, no I/O) ───────────────────────────────────

def _build_strategy_prompt(state: "MonetisationState", persona: dict) -> str:
    """Build the monetisation strategy prompt. Pure function — no I/O."""
    constraints_block = (
        f"\n━━━ CONSTRAINTS ━━━\n{state['constraints']}"
        if state.get("constraints", "").strip()
        else ""
    )
    return f"""{persona['personality']}

You are building a monetisation strategy for the business below.
Be specific — name exact pricing, describe exact funnel steps, reference actual tools.
No generic advice. Every recommendation must be immediately actionable. Max 700 words.

━━━ CLIENT ━━━
Name            : {state['client_name']}
Current Revenue : {state.get('current_revenue') or 'Pre-revenue / not provided'}

━━━ BUSINESS CONTEXT ━━━
{state['business_context'][:CONTEXT_CHARS]}

━━━ GOALS ━━━
{state['goals'][:GOALS_CHARS]}{constraints_block}

━━━ DELIVER ━━━

## Monetisation Strategy: {state['client_name']}

### Revenue Model Assessment
[Current model → recommended model. Why it fits this business.]

### Pricing Architecture
| Tier | Price | What's included | Target buyer |
|---|---|---|---|
[Fill 2-4 tiers. Be specific on price points.]

### Conversion Funnel
[Step-by-step: Awareness → Interest → Decision → Purchase → Upsell]
[For each step: specific channel, specific message, conversion target]

### Quick Wins (0–30 days)
[3 actions that can be shipped immediately to increase revenue]

### Growth Levers (30–90 days)
[2-3 compounding moves: referral, content, partnership, pricing change]

### Revenue Projection
[Conservative / Realistic / Optimistic MRR at 30/90/180 days]

### Risk Flags
[Top 2 risks to this strategy and how to mitigate them]"""


_build_prompt = _build_strategy_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2: Generate (Claude call, retried on transient errors only) ─────────────
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
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


# ── Main node ─────────────────────────────────────────────────────────────────────
def monetisation_strategist_node(state: MonetisationState) -> dict:
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    client_name  = state.get("client_name", "")
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()
    metrics      = CallMetrics(thread_id, ROLE)

    def _checkpoint(cid: str, payload: dict) -> None:
        state_logger.log_state(thread_id, cid, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, client=client_name)

    try:
        if not client_name.strip():
            raise ValueError("client_name is required.")
        if not state.get("business_context", "").strip():
            raise ValueError("business_context is required — describe what the business sells and to whom.")
        if not state.get("goals", "").strip():
            raise ValueError("goals is required — describe the revenue target and timeline.")

        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_strategy_prompt(state, persona)

        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"client": client_name, "status": "generating"},
        )

        strategy = _generate(claude, prompt, metrics)
        metrics.log()
        metrics.persist()

        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"client": client_name, "status": "completed", "strategy_chars": len(strategy)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, strategy_chars=len(strategy))
        return {"strategy": strategy, "error": None, "workflow_id": thread_id, "agent": ROLE}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"client": client_name, "status": "permanent_failure", "error": msg})
        return {"strategy": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        return {"strategy": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        return {"strategy": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(MonetisationState)
    g.add_node("monetisation_strategist", monetisation_strategist_node)
    g.set_entry_point("monetisation_strategist")
    g.add_edge("monetisation_strategist", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
