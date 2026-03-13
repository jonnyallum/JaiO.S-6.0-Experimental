"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : pricing_strategist
 SKILL : Pricing Strategist — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), business_model (str),
               value_metric (str — what customers pay for),
               target_segment (str), cost_to_serve (str — optional),
               competitors (str — optional pricing intel),
               output_type (str)
 Output keys : pricing_strategy (str), recommended_tiers (list[dict])
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 model context lookup →
 Phase 2 Claude pricing design. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid business_model/output_type (ValueError),
               empty product_name, value_metric, or target_segment
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: business_model, output_type,
        has_competitor_intel, has_cost_data
 POST — logged after success: strategy char count, tier count

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import anthropic
import structlog
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona
from tools.supabase_tools import SupabaseStateLogger

# ── Identity ──────────────────────────────────────────────────────────────────
log = structlog.get_logger()

ROLE = "pricing_strategist"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2200

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_BUSINESS_MODELS = {
    "saas", "agency_retainer", "productised_service", "marketplace",
    "ecommerce", "consulting", "course", "community", "freemium", "general"
}
VALID_OUTPUT_TYPES = {
    "tier_design", "value_metric_analysis", "competitive_positioning",
    "packaging_strategy", "price_increase_plan", "freemium_conversion", "general"
}

# ── Model-specific pricing principles ─────────────────────────────────────────
_MODEL_PRINCIPLES: dict[str, dict] = {
    "saas": {
        "anchor":      "3 tiers: Starter / Pro / Enterprise — never fewer, rarely more",
        "psychology":  "Middle tier should be the obvious choice (price anchoring)",
        "value_align": "Price scales with value metric (seats, usage, features, outcomes)",
        "common_traps":"Don't price on cost; don't copy competitor prices blindly",
    },
    "agency_retainer": {
        "anchor":      "3 packages: Essentials / Growth / Partnership",
        "psychology":  "Name tiers by outcome, not by features",
        "value_align": "Anchor to hours saved or results delivered, not hours worked",
        "common_traps":"Hourly pricing erodes perceived value; avoid scope creep clauses",
    },
    "productised_service": {
        "anchor":      "Fixed scope, fixed price — clarity beats flexibility",
        "psychology":  "List what's NOT included as prominently as what is",
        "value_align": "Price to outcome, not to effort",
        "common_traps":"Scope creep without add-on pricing kills margins",
    },
    "marketplace": {
        "anchor":      "Take rate (%) or listing fee + success fee hybrid",
        "psychology":  "Free to list lowers friction; monetise at transaction",
        "value_align": "Align fee to value exchanged, not arbitrary percentage",
        "common_traps":"Race to zero on fees without differentiation",
    },
    "ecommerce": {
        "anchor":      "Volume tiers or subscription bundle discounts",
        "psychology":  "Charm pricing (£X.99) works for sub-£50 items",
        "value_align": "Bundle logically related products to raise AOV",
        "common_traps":"Perpetual discounting destroys perceived value",
    },
    "consulting": {
        "anchor":      "Daily rate + retainer + project-based hybrid",
        "psychology":  "Productise your IP — methodology pricing > time pricing",
        "value_align": "Price to the client's ROI, not your cost",
        "common_traps":"Selling time caps revenue; sell outcomes instead",
    },
    "course": {
        "anchor":      "Self-paced / Cohort / VIP tiers",
        "psychology":  "Community + accountability = 3x price tolerance",
        "value_align": "Price to transformation, not content volume",
        "common_traps":"Underpricing signals low quality; price confidence matters",
    },
    "community": {
        "anchor":      "Free / Member / Founding Member tiers",
        "psychology":  "Founding member pricing creates urgency and loyalty",
        "value_align": "Access + connection + status drive willingness to pay",
        "common_traps":"Free tier must provide real value or it pollutes culture",
    },
    "freemium": {
        "anchor":      "Free tier gates power features; paid unlocks outcomes",
        "psychology":  "2–5% conversion rate is healthy; design free to show value, not replace paid",
        "value_align": "Gate the 'aha moment' behind paid, not the basics",
        "common_traps":"Too generous free tier kills conversion; too stingy kills acquisition",
    },
    "general": {
        "anchor":      "Good / Better / Best three-tier architecture",
        "psychology":  "Middle tier anchoring drives 60–70% of sales",
        "value_align": "Price must reflect perceived value, not cost",
        "common_traps":"Avoid too many options (paradox of choice); max 3–4 tiers",
    },
}

# ── State ──────────────────────────────────────────────────────────────────────
class PricingState(BaseState):
    # Inputs
    product_name:    str   # product or service being priced
    business_model:  str   # business model type
    value_metric:    str   # what customers pay for (e.g. "seats", "API calls", "results")
    target_segment:  str   # ideal customer profile
    cost_to_serve:   str   # optional — COGS, fixed costs, margin targets
    competitors:     str   # optional — competitor pricing intel
    output_type:     str   # type of pricing output
    thread_id:       str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    model_principles: dict  # pricing principles for this business model (owner: this node)

    # Outputs
    pricing_strategy:  str        # full pricing strategy (owner: this node)
    recommended_tiers: list[dict] # structured tier recommendations (owner: this node)
    error:             str        # failure reason if any (owner: this node)


# ── Phase 1 — pure model context lookup (no Claude) ───────────────────────────

def _get_model_context(business_model: str) -> dict:
    """
    Phase 1 — pure lookup of pricing principles for this model type.
    No Claude, no I/O — independently testable.
    """
    return _MODEL_PRINCIPLES.get(business_model, _MODEL_PRINCIPLES["general"])


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    product_name: str,
    business_model: str,
    value_metric: str,
    target_segment: str,
    cost_to_serve: str,
    competitors: str,
    output_type: str,
    principles: dict,
) -> str:
    """Pure function — assembles the pricing brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    output_label  = output_type.replace("_", " ").title()
    cost_str      = f"\nCost / margin data: {cost_to_serve}" if cost_to_serve else ""
    comp_str      = f"\nCompetitor intel: {competitors}" if competitors else ""
    principles_str = "\n".join(f"  {k}: {v}" for k, v in principles.items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} pricing strategist.

Product        : {product_name}
Business model : {business_model}
Value metric   : {value_metric} (what the customer pays for)
Target segment : {target_segment}{cost_str}{comp_str}

Pricing principles for this model:
{principles_str}

Output required: {output_label}

Deliver a complete, specific {output_label} including:

FOR TIER DESIGN:
- 3 tiers named by outcome (not "Basic/Pro/Enterprise")
- Each tier: name | price point | included features (bulleted) | ideal customer
- Psychological rationale for each price point
- Recommended "hero" tier and why

FOR VALUE METRIC ANALYSIS:
- Is this the right value metric? Analysis + recommendation
- How to align price to value delivery
- Potential alternative metrics and trade-offs

FOR COMPETITIVE POSITIONING:
- Price positioning matrix (cheaper/same/premium vs competitors)
- Differentiation rationale that justifies chosen position
- Risk assessment of positioning

FOR PACKAGING STRATEGY:
- Which features belong in which tier and why (not random bundling)
- Features to gate, features to give away, features to charge for
- Add-on / upsell opportunities

FOR PRICE INCREASE PLAN:
- How to communicate increase (messaging framework)
- Grandfathering strategy for existing customers
- Timeline and rollout sequence

FOR FREEMIUM CONVERSION:
- Free tier design (what to include/exclude)
- Upgrade trigger design
- Conversion rate benchmarks and targets

FOR GENERAL:
- Full pricing recommendation with rationale
- Implementation sequence
- Common mistakes to avoid for this business model

Be specific with numbers. No ranges as final answers — commit to recommended price points."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _design_pricing(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


# ── Node ───────────────────────────────────────────────────────────────────────

def pricing_strategist_node(state: PricingState) -> PricingState:
    thread_id      = state.get("thread_id", "unknown")
    product_name   = state.get("product_name", "").strip()
    business_model = state.get("business_model", "general").lower().strip()
    value_metric   = state.get("value_metric", "").strip()
    target_segment = state.get("target_segment", "").strip()
    cost_to_serve  = state.get("cost_to_serve", "").strip()
    competitors    = state.get("competitors", "").strip()
    output_type    = state.get("output_type", "tier_design").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not product_name:
        return {**state, "error": "PERMANENT: product_name is required"}
    if not value_metric:
        return {**state, "error": "PERMANENT: value_metric is required"}
    if not target_segment:
        return {**state, "error": "PERMANENT: target_segment is required"}
    if business_model not in VALID_BUSINESS_MODELS:
        return {**state, "error": f"PERMANENT: business_model '{business_model}' not in {VALID_BUSINESS_MODELS}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    # ── Phase 1 — pure model context lookup ───────────────────────────────────
    principles = _get_model_context(business_model)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        product_name, business_model, value_metric, target_segment,
        cost_to_serve, competitors, output_type, principles,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "business_model": business_model,
        "output_type": output_type,
        "has_competitor_intel": bool(competitors),
        "has_cost_data": bool(cost_to_serve),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        pricing_strategy = _design_pricing(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "strategy_chars": len(pricing_strategy),
        "business_model": business_model,
        "output_type": output_type,
    })

    return {
        **state,
        "pricing_strategy":  pricing_strategy,
        "recommended_tiers": [],   # structured parsing of tiers is a downstream task
        "model_principles":  principles,
        "error":             "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PricingState)
    g.add_node("pricing_strategist", pricing_strategist_node)
    g.set_entry_point("pricing_strategist")
    g.add_edge("pricing_strategist", END)
    return g.compile()
