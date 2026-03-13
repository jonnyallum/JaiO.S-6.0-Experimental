"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : ecommerce_strategist
 SKILL : Ecommerce Strategist — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), niche (str), output_type (str),
               cost_price (float — optional, GBP),
               sell_price (float — optional, GBP),
               platform_fee_pct (float — optional, default 0.13),
               shipping_cost (float — optional, GBP),
               context (str — optional extra product/market info)
 Output keys : strategy_output (str), margin_data (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 margin computation →
 Phase 2 Claude strategy. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid niche/output_type (ValueError),
               empty product_name, negative prices
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: niche, output_type, has_margin_data
 POST — logged after success: output char count, net_margin_pct

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

ROLE = "ecommerce_strategist"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES          = 3
MAX_TOKENS           = 2200
DEFAULT_PLATFORM_FEE = 0.13   # 13% — eBay/Amazon blended average
MIN_VIABLE_MARGIN    = 0.20   # 20% net margin floor for healthy dropshipping

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_NICHES = {
    "fashion", "electronics", "home_garden", "beauty", "sports_outdoors",
    "toys_games", "automotive", "health_wellness", "pet_supplies",
    "office_supplies", "jewellery", "general"
}
VALID_OUTPUT_TYPES = {
    "product_research", "margin_analysis", "supplier_brief",
    "store_audit", "scaling_plan", "listing_optimisation", "general"
}

# ── Niche-specific intelligence ────────────────────────────────────────────────
_NICHE_INTEL: dict[str, dict] = {
    "fashion": {
        "return_rate":    "30–40% (size/fit issues)",
        "seasonality":    "Q4 + spring collections dominate",
        "margin_target":  "35–50% after returns",
        "sourcing_tip":   "AliExpress + CJdropshipping for unbranded; Faire for boutique",
        "platform_pick":  "Shopify + Instagram/TikTok Shop",
        "red_flags":      "Branded goods, trademark risk, size chart disputes",
    },
    "electronics": {
        "return_rate":    "15–25% (defects, compatibility)",
        "seasonality":    "Black Friday, Jan (new year upgrades)",
        "margin_target":  "15–25% (competitive, thin margins)",
        "sourcing_tip":   "1688.com direct; test samples before scaling",
        "platform_pick":  "eBay, Amazon — price comparison intense",
        "red_flags":      "CE/FCC compliance, counterfeit risk, warranty issues",
    },
    "home_garden": {
        "return_rate":    "10–15%",
        "seasonality":    "Spring/summer for garden; Q4 for home décor",
        "margin_target":  "35–55%",
        "sourcing_tip":   "AliExpress, Spocket for EU/US warehouse",
        "platform_pick":  "Etsy (handmade feel), Shopify, Amazon",
        "red_flags":      "Bulky items eat shipping margins; fragile = high damage returns",
    },
    "beauty": {
        "return_rate":    "5–10% (hygiene policy — final sale often)",
        "seasonality":    "Valentine's, Mother's Day, Q4",
        "margin_target":  "50–70% (high perceived value)",
        "sourcing_tip":   "Private label from Alibaba; get CPSR/FDA compliance docs",
        "platform_pick":  "TikTok Shop, Instagram, Shopify",
        "red_flags":      "Regulatory (cosmetics require compliance), skin reaction liability",
    },
    "sports_outdoors": {
        "return_rate":    "12–20%",
        "seasonality":    "Jan (resolutions), Q2, Black Friday",
        "margin_target":  "30–45%",
        "sourcing_tip":   "Alibaba + local wholesalers for faster shipping",
        "platform_pick":  "Amazon, Shopify, eBay",
        "red_flags":      "Safety standards (helmets, etc.), counterfeit brands",
    },
    "general": {
        "return_rate":    "10–20%",
        "seasonality":    "Q4 universal; niche-dependent otherwise",
        "margin_target":  "30–50%",
        "sourcing_tip":   "AliExpress, CJdropshipping, Spocket",
        "platform_pick":  "Shopify + one marketplace (eBay or Amazon)",
        "red_flags":      "Validate demand before ordering; avoid patent-infringing products",
    },
}
# Default for unlisted niches
for _n in ["toys_games", "automotive", "health_wellness", "pet_supplies", "office_supplies", "jewellery"]:
    if _n not in _NICHE_INTEL:
        _NICHE_INTEL[_n] = _NICHE_INTEL["general"]


# ── State ──────────────────────────────────────────────────────────────────────
class EcommerceState(BaseState):
    # Inputs
    product_name:     str    # product or product category
    niche:            str    # product niche
    output_type:      str    # type of strategy output
    cost_price:       float  # supplier cost (GBP)
    sell_price:       float  # customer sell price (GBP)
    platform_fee_pct: float  # platform commission (0.0–1.0)
    shipping_cost:    float  # per-unit shipping cost (GBP)
    context:          str    # optional extra info
    thread_id:        str    # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    margin_data:  dict  # computed margin breakdown (owner: this node)
    niche_intel:  dict  # niche-specific intelligence (owner: this node)

    # Outputs
    strategy_output: str   # full strategy output (owner: this node)
    error:           str   # failure reason if any (owner: this node)


# ── Phase 1 — pure margin computation (no Claude) ────────────────────────────

def _compute_margins(
    cost_price: float,
    sell_price: float,
    platform_fee_pct: float,
    shipping_cost: float,
) -> dict:
    """
    Phase 1 — compute full margin breakdown. Pure function — no Claude.
    Returns margin_data dict with all calculated fields.
    """
    if sell_price <= 0:
        return {}

    platform_fee  = sell_price * platform_fee_pct
    gross_profit  = sell_price - cost_price - shipping_cost
    net_profit    = gross_profit - platform_fee
    net_margin    = net_profit / sell_price
    markup        = ((sell_price - cost_price) / cost_price * 100) if cost_price > 0 else 0
    break_even    = cost_price + shipping_cost + platform_fee

    return {
        "sell_price":      round(sell_price, 2),
        "cost_price":      round(cost_price, 2),
        "shipping_cost":   round(shipping_cost, 2),
        "platform_fee":    round(platform_fee, 2),
        "gross_profit":    round(gross_profit, 2),
        "net_profit":      round(net_profit, 2),
        "net_margin_pct":  round(net_margin * 100, 1),
        "markup_pct":      round(markup, 1),
        "break_even":      round(break_even, 2),
        "viable":          net_margin >= MIN_VIABLE_MARGIN,
    }


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    product_name: str,
    niche: str,
    output_type: str,
    context: str,
    margin_data: dict,
    niche_intel: dict,
) -> str:
    """Pure function — assembles the e-commerce brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    output_label = output_type.replace("_", " ").title()
    context_str  = f"\nExtra context: {context}" if context else ""

    if margin_data:
        viability    = "✓ VIABLE" if margin_data.get("viable") else f"⚠ BELOW {int(MIN_VIABLE_MARGIN*100)}% FLOOR"
        margin_str   = f"""
Pre-computed margin analysis:
  Sell price    : £{margin_data['sell_price']}
  Cost price    : £{margin_data['cost_price']}
  Shipping      : £{margin_data['shipping_cost']}
  Platform fee  : £{margin_data['platform_fee']}
  Net profit    : £{margin_data['net_profit']}
  Net margin    : {margin_data['net_margin_pct']}%  {viability}
  Markup        : {margin_data['markup_pct']}%
  Break-even    : £{margin_data['break_even']}"""
    else:
        margin_str = "\nNo pricing data provided — provide margin recommendations based on niche benchmarks."

    intel_str = "\n".join(f"  {k}: {v}" for k, v in niche_intel.items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} e-commerce and dropshipping strategist.

Product        : {product_name}
Niche          : {niche}
Output type    : {output_label}{context_str}
{margin_str}

Niche intelligence:
{intel_str}

Deliver a complete {output_label}:

FOR PRODUCT_RESEARCH:
- Market demand signals (search volume proxy, trend direction)
- Competition assessment (saturation level, differentiation opportunities)
- 5 product variations or adjacent products to test
- Recommended sourcing platforms with search terms
- Winning product criteria checklist (does this product pass?)
- Go / No-go verdict with reasoning

FOR MARGIN_ANALYSIS:
- Full margin breakdown (use pre-computed data if available)
- Margin optimisation levers (3 ways to improve net margin)
- Volume required for £1k/month net profit
- Platform fee comparison (eBay vs Amazon vs Shopify own-store)
- Price positioning recommendation vs competitors

FOR SUPPLIER_BRIEF:
- Where to source (platform + search query)
- Supplier qualification criteria (7-point checklist)
- Sample order process and what to test
- MOQ negotiation tactics
- Red flags to reject a supplier
- Backup supplier strategy

FOR STORE_AUDIT:
- Homepage: conversion elements present/missing
- Product page: persuasion checklist (images, copy, social proof, urgency, trust)
- Checkout: friction points
- 10 specific improvements ranked by conversion impact
- Competitor benchmarking (3 things they do better)

FOR SCALING_PLAN:
- Phase 1: Validation (0→£1k/month) — what to prove
- Phase 2: Growth (£1k→£10k/month) — what to scale
- Phase 3: Scale (£10k+/month) — operations, team, automation
- Key metrics to track at each phase
- When to bring fulfilment in-house

FOR LISTING_OPTIMISATION:
- Title formula for {niche} (keyword structure)
- Bullet points framework (5 bullets, each with hook + feature + benefit)
- Description structure
- Image brief (hero + 5 supporting)
- Backend search terms strategy
- A9/Cassini algorithm tips for this niche

No generic e-commerce advice. Every recommendation must be specific to {product_name} in the {niche} niche."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _generate_strategy(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def ecommerce_strategist_node(state: EcommerceState) -> EcommerceState:
    thread_id        = state.get("thread_id", "unknown")
    product_name     = state.get("product_name", "").strip()
    niche            = state.get("niche", "general").lower().strip()
    output_type      = state.get("output_type", "general").lower().strip()
    cost_price       = float(state.get("cost_price", 0) or 0)
    sell_price       = float(state.get("sell_price", 0) or 0)
    platform_fee_pct = float(state.get("platform_fee_pct", DEFAULT_PLATFORM_FEE) or DEFAULT_PLATFORM_FEE)
    shipping_cost    = float(state.get("shipping_cost", 0) or 0)
    context          = state.get("context", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not product_name:
        return {**state, "error": "PERMANENT: product_name is required"}
    if niche not in VALID_NICHES:
        return {**state, "error": f"PERMANENT: niche '{niche}' not in {VALID_NICHES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}
    if cost_price < 0 or sell_price < 0 or shipping_cost < 0:
        return {**state, "error": "PERMANENT: prices cannot be negative"}

    # ── Phase 1 — pure margin computation ─────────────────────────────────────
    margin_data = _compute_margins(cost_price, sell_price, platform_fee_pct, shipping_cost) if sell_price > 0 else {}
    niche_intel = _NICHE_INTEL.get(niche, _NICHE_INTEL["general"])

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(product_name, niche, output_type, context, margin_data, niche_intel)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "niche": niche,
        "output_type": output_type,
        "has_margin_data": bool(margin_data),
        "net_margin_pct": margin_data.get("net_margin_pct"),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        strategy_output = _generate_strategy(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "output_chars":   len(strategy_output),
        "net_margin_pct": margin_data.get("net_margin_pct"),
        "niche":          niche,
    })

    return {
        **state,
        "strategy_output": strategy_output,
        "margin_data":     margin_data,
        "niche_intel":     niche_intel,
        "error":           "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(EcommerceState)
    g.add_node("ecommerce_strategist", ecommerce_strategist_node)
    g.set_entry_point("ecommerce_strategist")
    g.add_edge("ecommerce_strategist", END)
    return g.compile()
