"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : venture_ideator
SKILL : Venture Ideator

Creative Venture Architect — 19-point @langraph compliant agent node.

Node Contract:
    Inputs : idea_context (str), idea_type (VALID_IDEA_TYPES), market_size (VALID_MARKET_SIZES), budget_hint (str)
    Outputs: venture_blueprint (str), viability_score (int)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 — retries on TRANSIENT (API overload) only.
    Permanent failures (empty context, invalid type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty idea_context, unknown idea_type → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  — state snapshot before viability scoring
    POST — venture_blueprint + viability_score persisted after successful generation
"""

from __future__ import annotations

from state.base import BaseState

import re
from typing import TypedDict

import anthropic
import structlog
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint
from tools.supabase_tools import SupabaseStateLogger  # checkpoint alias
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

ROLE        = "venture_ideator"
MAX_RETRIES = 3
MAX_TOKENS  = 2800

VALID_IDEA_TYPES = {
    "saas_product", "agency_service", "content_brand", "ecommerce",
    "tool", "platform", "newsletter", "community", "general",
}
VALID_MARKET_SIZES = {"micro", "niche", "mid_market", "mass_market"}

# ── Trend Signal Database ─────────────────────────────────────────────────────
_TREND_SIGNALS = {
    "saas_product": {
        "platforms":            ["Product Hunt", "Hacker News", "IndieHackers"],
        "indicators":           ["API-first", "usage-based pricing", "AI-native", "vertical SaaS"],
        "monetisation_models":  ["per-seat", "usage-based", "freemium → pro", "enterprise tiers"],
        "risk_factors":         ["churn", "support overhead", "feature creep", "CAC"],
        "time_to_revenue":      "3–6 months",
    },
    "agency_service": {
        "platforms":            ["LinkedIn", "Twitter/X", "cold outreach", "referrals"],
        "indicators":           ["niche specialisation", "productised services", "retainer model"],
        "monetisation_models":  ["retainer", "project-based", "performance-fee", "hybrid"],
        "risk_factors":         ["client concentration", "scope creep", "delivery bottleneck"],
        "time_to_revenue":      "2–4 weeks",
    },
    "content_brand": {
        "platforms":            ["YouTube", "TikTok", "Twitter/X", "Substack"],
        "indicators":           ["niche authority", "audience ownership", "email list"],
        "monetisation_models":  ["sponsorship", "digital products", "community", "affiliate"],
        "risk_factors":         ["algorithm dependency", "burnout", "slow compounding"],
        "time_to_revenue":      "3–12 months",
    },
    "ecommerce": {
        "platforms":            ["Shopify", "Amazon", "TikTok Shop", "Instagram"],
        "indicators":           ["trending products", "private label", "DTC brand"],
        "monetisation_models":  ["direct sales", "subscription box", "wholesale", "marketplace"],
        "risk_factors":         ["inventory", "returns", "ad costs", "competition"],
        "time_to_revenue":      "1–3 months",
    },
    "tool": {
        "platforms":            ["Chrome Web Store", "npm", "GitHub", "VS Code marketplace"],
        "indicators":           ["developer pain point", "automation", "time-saving"],
        "monetisation_models":  ["one-time purchase", "freemium", "open-core", "hosted version"],
        "risk_factors":         ["commoditisation", "platform dependency", "support"],
        "time_to_revenue":      "1–6 months",
    },
    "newsletter": {
        "platforms":            ["Substack", "Beehiiv", "ConvertKit"],
        "indicators":           ["niche expertise", "curation value", "weekly cadence"],
        "monetisation_models":  ["paid subscription", "sponsorship", "product upsell"],
        "risk_factors":         ["list growth plateau", "churn", "content fatigue"],
        "time_to_revenue":      "6–18 months",
    },
    "community": {
        "platforms":            ["Circle", "Slack", "Discord", "Skool"],
        "indicators":           ["shared identity", "peer learning", "accountability"],
        "monetisation_models":  ["membership fee", "events", "courses", "mastermind"],
        "risk_factors":         ["engagement decay", "moderation overhead", "network effects slow"],
        "time_to_revenue":      "3–9 months",
    },
    "platform": {
        "platforms":            ["web", "mobile", "API"],
        "indicators":           ["two-sided marketplace", "network effects", "data flywheel"],
        "monetisation_models":  ["take rate", "subscription", "advertising", "data licensing"],
        "risk_factors":         ["cold start", "trust", "regulation", "high burn"],
        "time_to_revenue":      "6–24 months",
    },
    "general": {
        "platforms":            ["web", "mobile", "social"],
        "indicators":           ["problem-solution fit", "target audience clarity"],
        "monetisation_models":  ["direct sales", "subscription", "marketplace", "licensing"],
        "risk_factors":         ["market validation", "execution", "competition"],
        "time_to_revenue":      "variable",
    },
}

_BUDGET_TIERS = {
    "bootstrap":  {"range": "£0–£500",    "constraint": "zero-cost tools, sweat equity only"},
    "lean":       {"range": "£500–£5k",   "constraint": "minimal paid tools, outsource sparingly"},
    "standard":   {"range": "£5k–£25k",   "constraint": "paid stack, small team or contractors"},
    "funded":     {"range": "£25k+",      "constraint": "full team, paid acquisition, product dev"},
}


class VentureIdeatorState(BaseState):
    workflow_id:      str
    timestamp:        str
    agent:            str
    error:            str | None
    idea_context:     str
    idea_type:        str
    market_size:      str
    budget_hint:      str
    venture_blueprint: str
    viability_score:  int


# ── Phase 1 — Viability Scoring (pure, no Claude) ─────────────────────────────
def _score_viability(idea_context: str, idea_type: str, market_size: str) -> dict:
    """Returns viability_data dict — pure heuristic scoring."""
    signals     = _TREND_SIGNALS.get(idea_type, _TREND_SIGNALS["general"])
    word_count  = len(idea_context.split())

    # Score components (0–10 each)
    clarity_score   = min(10, word_count // 20)               # more context = clearer idea
    market_score    = {"micro": 5, "niche": 7, "mid_market": 8, "mass_market": 6}.get(market_size, 5)
    complexity_score = {"saas_product": 5, "platform": 3, "tool": 7, "agency_service": 9,
                        "content_brand": 7, "ecommerce": 6, "newsletter": 8,
                        "community": 7, "general": 6}.get(idea_type, 6)
    overall = round((clarity_score + market_score + complexity_score) / 3)

    return {
        "clarity_score":    clarity_score,
        "market_score":     market_score,
        "complexity_score": complexity_score,
        "overall":          overall,
        "time_to_revenue":  signals["time_to_revenue"],
        "risk_factors":     signals["risk_factors"],
        "monetisation":     signals["monetisation_models"],
        "platforms":        signals["platforms"],
    }

_build_prompt = None  # assigned below


# ── Phase 2 — Claude Venture Blueprint ─────────────────────────────────────────
def _build_venture_prompt(state: VentureIdeatorState, viability: dict) -> str:
    persona     = get_persona(ROLE)
    idea_context = state["idea_context"]
    idea_type   = state.get("idea_type", "general")
    market_size = state.get("market_size", "niche")
    budget_hint = state.get("budget_hint", "lean")
    budget_info = _BUDGET_TIERS.get(budget_hint, _BUDGET_TIERS["lean"])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Turn this raw idea into a concrete, monetisable venture blueprint.

IDEA TYPE: {idea_type}
MARKET SIZE: {market_size}
BUDGET TIER: {budget_hint} ({budget_info['range']}) — {budget_info['constraint']}

VIABILITY PRE-SCORE:
  Clarity:     {viability['clarity_score']}/10
  Market:      {viability['market_score']}/10
  Feasibility: {viability['complexity_score']}/10
  Overall:     {viability['overall']}/10
  Time-to-Revenue: {viability['time_to_revenue']}
  Key Risks: {', '.join(viability['risk_factors'])}
  Monetisation Models: {', '.join(viability['monetisation'])}
  Distribution Channels: {', '.join(viability['platforms'])}

IDEA CONTEXT:
'''
{idea_context[:4000]}
'''

YOUR TASK:
1. Name the venture (punchy, memorable, domain-available style).
2. Define the exact target customer (one-line persona).
3. Write the positioning statement (X for Y who Z).
4. Build the 90-day launch roadmap (3 phases, 30 days each).
5. Detail the monetisation stack (primary + upsell).
6. Identify the single biggest risk and the mitigation plan.
7. List the 3 unfair advantages this idea has.

OUTPUT FORMAT:
## Venture Blueprint
**Venture Name:** [name]
**One-Liner:** [X for Y who Z]
**Target Customer:** [specific persona]

### Viability Score: {viability['overall']}/10
[2-sentence justification]

### 90-Day Launch Roadmap
**Phase 1 (Days 1–30): Validation**
[5 specific actions]

**Phase 2 (Days 31–60): Build & Launch**
[5 specific actions]

**Phase 3 (Days 61–90): Growth**
[5 specific actions]

### Monetisation Stack
**Primary:** [model, price point, expected MRR at 90 days]
**Upsell:** [model, price point]
**Long-term:** [model]

### Biggest Risk & Mitigation
**Risk:** [specific risk]
**Mitigation:** [exact plan]

### Unfair Advantages
1. [advantage]
2. [advantage]
3. [advantage]

### First 3 Actions (start today)
1. [action — specific, time-boxed]
2. [action — specific, time-boxed]
3. [action — specific, time-boxed]

VIABILITY_SCORE: {viability['overall']}
"""

_build_prompt = _build_venture_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError):
        return exc.status_code in (429, 529)
    return False


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
def _ideate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    metrics.log()
    metrics.persist()
    return response.content[0].text
_generate = _ideate  # spec alias



def venture_ideator_node(state: VentureIdeatorState) -> VentureIdeatorState:
    thread_id    = state.get("workflow_id", "local")
    idea_context = state.get("idea_context", "").strip()
    idea_type    = state.get("idea_type", "general")
    market_size  = state.get("market_size", "niche")
    budget_hint  = state.get("budget_hint", "lean")

    if not idea_context:
        raise ValueError("PERMANENT: idea_context is required.")
    if idea_type not in VALID_IDEA_TYPES:
        raise ValueError(f"PERMANENT: idea_type '{idea_type}' not in {VALID_IDEA_TYPES}")
    if market_size not in VALID_MARKET_SIZES:
        raise ValueError(f"PERMANENT: market_size '{market_size}' not in {VALID_MARKET_SIZES}")

    checkpoint("PRE", thread_id, ROLE, {"idea_type": idea_type, "market_size": market_size})

    viability = _score_viability(idea_context, idea_type, market_size)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_venture_prompt(state, viability)

    try:
        blueprint = _ideate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    score_match    = re.search(r'VIABILITY_SCORE:\s*(\d+)', blueprint)
    viability_score = int(score_match.group(1)) if score_match else viability["overall"]

    checkpoint("POST", thread_id, ROLE, {"viability_score": viability_score})

    return {
        **state,
        "agent":             ROLE,
        "venture_blueprint": blueprint,
        "viability_score":   viability_score,
        "error":             None,
    }


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(VentureIdeatorState)
    g.add_node("venture_ideator", venture_ideator_node)
    g.set_entry_point("venture_ideator")
    g.add_edge("venture_ideator", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
