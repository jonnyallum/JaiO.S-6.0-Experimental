"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : investor_pitch_writer
 SKILL : Investor Pitch Writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : company_name (str), one_liner (str),
               funding_stage (str), raise_amount (str),
               problem (str), solution (str),
               traction (str — optional metrics/milestones),
               market_size (str — optional TAM/SAM/SOM),
               output_type (str)
 Output keys : pitch_content (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 deck structure lookup →
 Phase 2 Claude pitch narrative. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid funding_stage/output_type (ValueError),
               empty company_name, one_liner, problem, or solution
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: funding_stage, output_type,
        has_traction, has_market_size
 POST — logged after success: pitch char count, output_type

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

ROLE = "investor_pitch_writer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 8000

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_FUNDING_STAGES = {
    "pre_seed", "seed", "series_a", "series_b", "series_c_plus",
    "bootstrapped", "grant", "crowdfunding"
}
VALID_OUTPUT_TYPES = {
    "deck_narrative", "one_pager", "executive_summary",
    "cold_email_sequence", "investor_update", "pitch_script"
}

# ── Stage-specific investor priorities ────────────────────────────────────────
_STAGE_FOCUS: dict[str, dict] = {
    "pre_seed": {
        "investors_care_about": "Team credibility, problem insight, unfair advantage",
        "traction_bar":         "Early users / waitlist / letters of intent",
        "deck_slides":          10,
        "red_flags":            "No technical co-founder, no domain expertise, overcrowded space",
        "raise_typical":        "£50k–£500k",
    },
    "seed": {
        "investors_care_about": "Product-market fit signals, early revenue, repeatable acquisition",
        "traction_bar":         "MRR, DAU, retention curve, NPS",
        "deck_slides":          12,
        "red_flags":            "High churn, no moat, founder disagreement on vision",
        "raise_typical":        "£500k–£3M",
    },
    "series_a": {
        "investors_care_about": "Scalable GTM, unit economics, path to market leadership",
        "traction_bar":         "£500k–£2M ARR, <120% net revenue retention, clear CAC/LTV",
        "deck_slides":          15,
        "red_flags":            "No clear market leadership thesis, CAC > 12-month payback",
        "raise_typical":        "£3M–£15M",
    },
    "series_b": {
        "investors_care_about": "Proven GTM, international expansion readiness, org scale",
        "traction_bar":         "£5M+ ARR, proven enterprise or SMB motion, strong team",
        "deck_slides":          15,
        "red_flags":            "Plateauing growth, management gaps, single market dependency",
        "raise_typical":        "£15M–£50M",
    },
    "series_c_plus": {
        "investors_care_about": "Path to profitability or IPO, global scale, defensible position",
        "traction_bar":         "£20M+ ARR, international revenue, clear market position",
        "deck_slides":          18,
        "red_flags":            "Down round risk, leadership churn, regulatory exposure",
        "raise_typical":        "£50M+",
    },
    "bootstrapped": {
        "investors_care_about": "Profitability, cash efficiency, strategic acqui-hire value",
        "traction_bar":         "Profitable or near-profitable, sustainable growth",
        "deck_slides":          10,
        "red_flags":            "Lifestyle business signals when pitching growth investors",
        "raise_typical":        "Varies",
    },
    "grant": {
        "investors_care_about": "Social/scientific impact, technical innovation, team credentials",
        "traction_bar":         "Research credentials, pilot results, partnerships",
        "deck_slides":          12,
        "red_flags":            "Commercial framing in non-commercial grant applications",
        "raise_typical":        "£10k–£500k",
    },
    "crowdfunding": {
        "investors_care_about": "Compelling narrative, crowd appeal, existing community",
        "traction_bar":         "Pre-existing audience, social proof, momentum",
        "deck_slides":          10,
        "red_flags":            "No existing community, complex B2B product",
        "raise_typical":        "£50k–£2M",
    },
}

# ── State ──────────────────────────────────────────────────────────────────────
class InvestorPitchState(BaseState):
    # Inputs
    company_name:   str   # company name
    one_liner:      str   # one sentence company description
    funding_stage:  str   # stage of fundraise
    raise_amount:   str   # how much being raised
    problem:        str   # problem being solved
    solution:       str   # how it's solved
    traction:       str   # optional — metrics, milestones, revenue
    market_size:    str   # optional — TAM/SAM/SOM
    output_type:    str   # type of pitch output
    thread_id:      str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    stage_focus: dict  # investor priorities for this stage (owner: this node)

    # Outputs
    pitch_content: str   # full pitch output (owner: this node)
    error:         str   # failure reason if any (owner: this node)


# ── Phase 1 — pure stage context lookup (no Claude) ───────────────────────────

def _get_stage_context(funding_stage: str) -> dict:
    """
    Phase 1 — pure lookup. Returns investor priorities for this funding stage.
    No Claude, no I/O — independently testable.
    """
    return _STAGE_FOCUS.get(funding_stage, _STAGE_FOCUS["seed"])


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    company_name: str,
    one_liner: str,
    funding_stage: str,
    raise_amount: str,
    problem: str,
    solution: str,
    traction: str,
    market_size: str,
    output_type: str,
    stage_focus: dict,
) -> str:
    """Pure function — assembles the investor pitch brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    output_label  = output_type.replace("_", " ").title()
    traction_str  = f"\nTraction: {traction}" if traction else "\nTraction: [None provided — flag this gap]"
    market_str    = f"\nMarket size: {market_size}" if market_size else "\nMarket size: [None provided — flag this gap]"
    focus_str     = "\n".join(f"  {k}: {v}" for k, v in stage_focus.items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} investor pitch specialist.

Company        : {company_name}
One-liner      : {one_liner}
Funding stage  : {funding_stage} (typical raise: {stage_focus['raise_typical']})
Raise amount   : {raise_amount}
Problem        : {problem}
Solution       : {solution}{traction_str}{market_str}

What {funding_stage} investors care about:
{focus_str}

Output type: {output_label}

FOR DECK_NARRATIVE (slide-by-slide narrative script):
Write the spoken narrative for each of the {stage_focus['deck_slides']} slides:
- Slide 1: Cover — company name, one-liner, presenter name placeholder
- Slide 2: Problem — make it visceral; make the investor feel the pain
- Slide 3: Solution — show don't tell; demo moment if applicable
- Slide 4: Market — TAM/SAM/SOM with methodology, not just big numbers
- Slide 5: Product — key features that prove the insight
- Slide 6: Business Model — how money flows, unit economics preview
- Slide 7: Traction — show the curve, not just the number
- Slide 8: GTM — how you reach customers at scale
- Slide 9: Competition — honest 2×2 matrix, acknowledge real threats
- Slide 10: Team — why you specifically? What's the unfair advantage?
- Slides 11+: Financials, Use of Funds, Ask (stage-dependent)

FOR ONE_PAGER:
Single page — problem, solution, traction, team, ask. Max 400 words. Visually scannable.

FOR EXECUTIVE_SUMMARY:
2 pages max: company overview, problem, solution, market, traction, team, financials, ask.

FOR COLD_EMAIL_SEQUENCE:
3 emails: initial outreach (50 words) → follow-up with new hook (40 words) → final bump (20 words).
Each targeted at a {funding_stage} stage investor. Personalisation placeholder included.

FOR INVESTOR_UPDATE:
Monthly update format: highlights, metrics, asks, low-lights (honest), pipeline.

FOR PITCH_SCRIPT:
5-minute verbal pitch: hook → problem → solution → traction → ask → close.

Rules:
- Truth-lock everything to the provided inputs — invent no metrics
- If traction is missing, explicitly flag: "FOUNDER ACTION: Add [specific metric] before pitching"
- Never use "disruptive", "revolutionary", "game-changing" — show it instead
- The ask must include: amount, use of funds (3 lines), timeline to next milestone"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_pitch(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def investor_pitch_writer_node(state: InvestorPitchState) -> InvestorPitchState:
    thread_id     = state.get("thread_id", "unknown")
    company_name  = state.get("company_name", "").strip()
    one_liner     = state.get("one_liner", "").strip()
    funding_stage = state.get("funding_stage", "seed").lower().strip()
    raise_amount  = state.get("raise_amount", "TBC").strip()
    problem       = state.get("problem", "").strip()
    solution      = state.get("solution", "").strip()
    traction      = state.get("traction", "").strip()
    market_size   = state.get("market_size", "").strip()
    output_type   = state.get("output_type", "deck_narrative").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not company_name:
        return {**state, "error": "PERMANENT: company_name is required"}
    if not one_liner:
        return {**state, "error": "PERMANENT: one_liner is required"}
    if not problem:
        return {**state, "error": "PERMANENT: problem is required"}
    if not solution:
        return {**state, "error": "PERMANENT: solution is required"}
    if funding_stage not in VALID_FUNDING_STAGES:
        return {**state, "error": f"PERMANENT: funding_stage '{funding_stage}' not in {VALID_FUNDING_STAGES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    # ── Phase 1 — pure stage context lookup ───────────────────────────────────
    stage_focus = _get_stage_context(funding_stage)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        company_name, one_liner, funding_stage, raise_amount,
        problem, solution, traction, market_size, output_type, stage_focus,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "funding_stage": funding_stage,
        "output_type": output_type,
        "has_traction": bool(traction),
        "has_market_size": bool(market_size),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        pitch_content = _write_pitch(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "pitch_chars": len(pitch_content),
        "output_type": output_type,
        "funding_stage": funding_stage,
    })

    return {
        **state,
        "pitch_content": pitch_content,
        "stage_focus":   stage_focus,
        "error":         "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(InvestorPitchState)
    g.add_node("investor_pitch_writer", investor_pitch_writer_node)
    g.set_entry_point("investor_pitch_writer")
    g.add_edge("investor_pitch_writer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
