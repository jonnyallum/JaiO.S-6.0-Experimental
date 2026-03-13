"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : funnel_architect
SKILL : Funnel Architect — design a complete conversion funnel with stage-specific copy
        angles, offer structure, objection matrix, upsell map, and CRO recommendations

Node Contract (@langraph doctrine):
  Inputs   : product (str), audience (str), funnel_stage (str), traffic_source (str),
             avg_order_value (str), current_conversion (str) — immutable after entry
  Outputs  : funnel_spec (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]
             Telemetry: CallMetrics per invocation — tokens, cost_usd, latency_ms [non-fatal]

Thread Memory (checkpoint-scoped):
  All FunnelState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: single-pass is architecturally correct here. A funnel spec requires one
  coherent voice — multi-pass calls produce contradictory stage recommendations.
  High token budget (MAX_TOKENS=2200) provides sufficient depth in one shot.

Failure Discrimination:
  PERMANENT  → ValueError (product or audience missing, invalid funnel_stage/traffic_source)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (records stage, traffic_source, audience temperature)
  POST — Supabase log after completion (records funnel_spec size)

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

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE             = "funnel_architect"
MAX_RETRIES      = 3
RETRY_MIN_S      = 3
RETRY_MAX_S      = 45
MAX_TOKENS       = 2200    # Funnel specs include tables, copy, and objection maps — needs depth
PRODUCT_CHARS    = 800
AUDIENCE_CHARS   = 600
AOV_CHARS        = 100
CONVERSION_CHARS = 200

VALID_FUNNEL_STAGES = {
    "awareness",      # Top of funnel — cold audience, first touch
    "consideration",  # Mid funnel — knows the problem, evaluating solutions
    "decision",       # Bottom of funnel — ready to buy, needs final push
    "retention",      # Post-purchase — activation, usage depth, upsell
    "reactivation",   # Churned or dormant — win-back sequence
}

VALID_TRAFFIC_SOURCES = {
    "paid_social",    # Meta, TikTok, LinkedIn ads
    "organic_search", # SEO, blog, YouTube
    "email",          # Existing list, newsletter
    "referral",       # Word of mouth, affiliate, partner
    "direct",         # Direct traffic, brand search
    "cold_outreach",  # DM, cold email, LinkedIn outreach
}

# Audience temperature by traffic source — informs trust lever recommendations
_TEMP_MAP = {
    "paid_social":    "cold",
    "organic_search": "warm",
    "email":          "warm",
    "referral":       "warm",
    "direct":         "hot",
    "cold_outreach":  "cold",
}

# Stage-specific strategic priorities — informs prompt framing
_STAGE_PRIORITIES = {
    "awareness":     "Build desire, establish the problem, capture attention. No hard sell.",
    "consideration": "Differentiate from alternatives, build trust, reduce perceived risk.",
    "decision":      "Remove final objections, create urgency, make the buy decision easy.",
    "retention":     "Drive activation, establish habit, surface upsell opportunity.",
    "reactivation":  "Re-establish relevance, acknowledge the gap, make returning frictionless.",
}

# Industry average conversion benchmarks per stage
_BENCHMARKS = {
    "awareness":     "1-3% CTR to next stage",
    "consideration": "5-15% to lead/trial",
    "decision":      "1-5% to purchase (e-com) | 20-40% (high-touch sales)",
    "retention":     "80%+ 30-day retention is healthy",
    "reactivation":  "5-15% win-back rate",
}

# Trust mechanisms most effective per audience temperature
_TRUST_MAP = {
    "cold": ["social proof volume", "risk reversal (money-back guarantee)", "authority signals"],
    "warm": ["specific case studies", "head-to-head comparison", "testimonials with outcomes"],
    "hot":  ["urgency/scarcity signals", "objection elimination", "frictionless checkout"],
}


# ── State schema ─────────────────────────────────────────────────────────────────
class FunnelState(BaseState):
    # Inputs — written by caller, immutable inside this node
    product: str              # Product or service being sold
    audience: str             # Target customer — role, pain, awareness level, ICP
    funnel_stage: str         # See VALID_FUNNEL_STAGES
    traffic_source: str       # See VALID_TRAFFIC_SOURCES
    avg_order_value: str      # AOV or LTV context — informs offer structure
    current_conversion: str   # Current CVR or "unknown" — for gap analysis
    # Outputs — written by this node, read by downstream nodes
    funnel_spec: str          # Complete funnel specification; empty on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: Stage context computation (pure, independently testable) ─────────────
def _get_stage_context(stage: str, source: str) -> dict:
    """
    Compute funnel design context from stage and traffic source. Pure function — no I/O.
    Returns audience temperature, priority framing, benchmark, and trust levers.
    Separation allows unit testing without mocking Claude.
    """
    temp  = _TEMP_MAP.get(source, "cold")
    trust = _TRUST_MAP.get(temp, _TRUST_MAP["cold"])
    return {
        "temperature":  temp,
        "priority":     _STAGE_PRIORITIES.get(stage, "Optimise conversion at this stage."),
        "benchmark":    _BENCHMARKS.get(stage, "Varies by industry"),
        "trust_levers": trust,
    }


# ── Phase 2: Funnel design (Claude call, retried on transient errors only) ────────
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
def _design_funnel(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()
_generate = _design_funnel  # spec alias



def _build_prompt(state: "FunnelState", context: dict, persona: dict) -> str:
    """Format funnel context into a conversion design prompt. Pure function — no I/O."""
    trust_md = "\n".join(f"  - {t}" for t in context["trust_levers"])

    return f"""{persona['personality']}

Design a conversion funnel specification. Be specific — write actual copy angles,
not descriptions of what to write. Reference specific mechanisms, not generic advice.

━━━ FUNNEL BRIEF ━━━
Product         : {state.get('product', '')[:PRODUCT_CHARS]}
Audience (ICP)  : {state.get('audience', '')[:AUDIENCE_CHARS]}
Stage           : {state['funnel_stage'].upper()}
Traffic source  : {state['traffic_source'].replace('_', ' ')}
Audience temp   : {context['temperature'].upper()}
AOV / LTV       : {state.get('avg_order_value', 'unknown')[:AOV_CHARS]}
Current CVR     : {state.get('current_conversion', 'unknown')[:CONVERSION_CHARS]}
Stage benchmark : {context['benchmark']}
Stage priority  : {context['priority']}

Trust levers for {context['temperature']} audience:
{trust_md}

━━━ REQUIRED OUTPUT ━━━
## Funnel Specification: {state['funnel_stage'].title()} Stage

### Audience State at Entry
[What does this visitor know, feel, and fear? Specific to the ICP — not generic]

### Core Copy Angle
[The central message that moves this audience forward. Write the actual headline
and sub-headline verbatim — not a description of what they should say]

### Funnel Stage Map
| Stage | Page/Touchpoint | Goal | Primary CTA | Trust Element |
|---|---|---|---|---|
[3-5 rows covering this stage and immediate next steps]

### Offer Structure
[Exact offer at this stage: lead magnet / tripwire / core / upsell / downsell.
Include pricing psychology recommendations if AOV data is available]

### Objection Matrix
| Objection | Trigger point | Counter-copy (verbatim) |
|---|---|---|
[Top 5 objections for this audience + stage]

### Upsell / Cross-sell Map
[Next logical offers after conversion — ordered by relevance and AOV impact]

### CRO Recommendations
| Element | Current assumption | Recommended change | Expected impact |
|---|---|---|---|
[Top 5 optimisation actions ranked by impact/effort]

### Copy Angles to A/B Test
1. [Value-led angle — specific headline]
2. [Pain-led angle — specific headline]
3. [Social proof-led angle — specific headline]

### 30-Day Action Plan
[Priority-ordered implementation steps with time estimates]

### Verdict
[CVR gap vs benchmark + single highest-leverage fix in one paragraph]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def funnel_architect_node(state: FunnelState) -> dict:
    """
    Funnel Architect node — single pass, no loop.

    Execution order:
      1. Validate inputs (product, audience required; stage, source validated)
      2. Compute stage context (Phase 1 — pure, no Claude)
      3. PRE checkpoint (before Claude call)
      4. Design funnel (Phase 2 — Claude, MAX_TOKENS=2200 for full depth)
      5. metrics.log() + metrics.persist() [non-fatal]
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: single-pass is correct — funnel coherence requires one voice.
    Multi-call approaches produce contradictory stage-level recommendations.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    stage        = state.get("funnel_stage", "")
    source       = state.get("traffic_source", "")
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, stage=stage, source=source)

    try:
        # Input guards — PERMANENT failures
        if not state.get("product", "").strip():
            raise ValueError("product is required — describe what is being sold.")
        if not state.get("audience", "").strip():
            raise ValueError("audience is required — describe the ICP (role, pain, awareness).")
        if stage not in VALID_FUNNEL_STAGES:
            raise ValueError(
                f"Invalid funnel_stage '{stage}'. "
                f"Must be one of: {', '.join(sorted(VALID_FUNNEL_STAGES))}"
            )
        if source not in VALID_TRAFFIC_SOURCES:
            raise ValueError(
                f"Invalid traffic_source '{source}'. "
                f"Must be one of: {', '.join(sorted(VALID_TRAFFIC_SOURCES))}"
            )

        claude  = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics = CallMetrics(thread_id, ROLE)

        # Phase 1 — compute stage context (pure)
        context = _get_stage_context(stage, source)
        log.info(f"{ROLE}.context_computed", temperature=context["temperature"],
                 trust_levers=len(context["trust_levers"]))

        # PRE checkpoint — mark funnel design started
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"stage": stage, "source": source, "temperature": context["temperature"],
             "status": "designing"},
        )

        # Phase 2 — design funnel (TRANSIENT failures retried by tenacity)
        prompt      = _build_prompt(state, context, persona)
        funnel_spec = _design_funnel(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"stage": stage, "source": source, "status": "completed",
             "spec_chars": len(funnel_spec)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, spec_chars=len(funnel_spec))
        return {"funnel_spec": funnel_spec, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input",
                  error=msg, stage=stage)
        notifier.agent_error(ROLE, stage, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"stage": stage, "status": "permanent_failure", "error": msg})
        return {"funnel_spec": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, stage, msg)
        return {"funnel_spec": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, stage, msg)
        return {"funnel_spec": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(FunnelState)
    g.add_node("funnel_architect", funnel_architect_node)
    g.set_entry_point("funnel_architect")
    g.add_edge("funnel_architect", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
