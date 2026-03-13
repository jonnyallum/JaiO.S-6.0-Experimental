"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : ab_test_designer
 SKILL : Ab Test Designer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : page_or_element (str — what's being tested),
               hypothesis (str — "We believe X will Y because Z"),
               baseline_cvr (float — current conversion rate 0.0–1.0),
               mde (float — minimum detectable effect, e.g. 0.2 = 20% rel. lift),
               daily_visitors (int), test_type (str),
               output_type (str)
 Output keys : test_design (str), sample_size (int), runtime_days (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 statistical power computation →
 Phase 2 Claude test design. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid test_type/output_type (ValueError),
               baseline_cvr not in (0, 1), mde <= 0 or >= 1,
               daily_visitors < 10
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: test_type, sample_size, runtime_days,
        statistical_power
 POST — logged after success: test_design char count, runtime_days

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import math

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

ROLE = "ab_test_designer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2000

# ── Statistical constants ──────────────────────────────────────────────────────
SIGNIFICANCE_LEVEL = 0.05   # α — 95% confidence
STATISTICAL_POWER  = 0.80   # β — 80% power (standard)
Z_ALPHA_2          = 1.96   # two-tailed z for α=0.05
Z_BETA             = 0.842  # z for β=0.80

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_TEST_TYPES = {
    "ab", "multivariate", "split_url", "bandit", "aa_test"
}
VALID_OUTPUT_TYPES = {
    "full_design", "hypothesis_refinement", "variant_brief",
    "results_analysis_plan", "prioritisation_backlog"
}

# ── Test type guidance ─────────────────────────────────────────────────────────
_TEST_GUIDANCE: dict[str, dict] = {
    "ab": {
        "variants":    "Control (A) vs Single Challenger (B)",
        "when_to_use": "Testing one clear hypothesis with sufficient traffic",
        "risk":        "Low — simple to analyse",
        "pitfall":     "Don't add a third variant mid-test",
    },
    "multivariate": {
        "variants":    "Multiple elements changed simultaneously",
        "when_to_use": "High traffic, testing interaction effects between elements",
        "risk":        "High — traffic split thin, takes much longer",
        "pitfall":     "Needs 5–10x the traffic of a simple A/B test",
    },
    "split_url": {
        "variants":    "Two distinct page URLs — full page redesigns",
        "when_to_use": "Testing fundamentally different page concepts",
        "risk":        "Medium — SEO implications, needs 301 handling post-test",
        "pitfall":     "Google may index both URLs — use rel=canonical carefully",
    },
    "bandit": {
        "variants":    "Dynamic traffic allocation to winning variant",
        "when_to_use": "When minimising regret matters more than statistical rigour",
        "risk":        "Medium — sacrifices learning for short-term conversion",
        "pitfall":     "Not appropriate for permanent design decisions",
    },
    "aa_test": {
        "variants":    "Control vs Control (identical pages)",
        "when_to_use": "Validating your testing setup before real experiments",
        "risk":        "None — diagnostic only",
        "pitfall":     "If AA shows significant difference, your setup is broken",
    },
}

# ── State ──────────────────────────────────────────────────────────────────────
class ABTestState(BaseState):
    # Inputs
    page_or_element:  str    # what's being tested
    hypothesis:       str    # structured hypothesis
    baseline_cvr:     float  # current conversion rate (0.0–1.0)
    mde:              float  # minimum detectable effect (relative, e.g. 0.2)
    daily_visitors:   int    # daily unique visitors to the page
    test_type:        str    # type of test
    output_type:      str    # type of design output
    thread_id:        str    # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    sample_size:  int    # required sample size per variant (owner: this node)
    runtime_days: int    # estimated runtime at given traffic (owner: this node)
    target_cvr:   float  # expected CVR after MDE lift (owner: this node)
    test_guidance: dict  # test type guidance (owner: this node)

    # Outputs
    test_design: str   # full test design document (owner: this node)
    error:       str   # failure reason if any (owner: this node)


# ── Phase 1 — pure statistical power computation (no Claude) ──────────────────

def _compute_sample_size(baseline_cvr: float, mde: float) -> tuple[int, float]:
    """
    Phase 1 — compute minimum sample size per variant using two-proportion z-test.
    Returns (sample_size_per_variant, target_cvr). Pure function — no Claude.

    Formula: n = (Z_α/2 + Z_β)² × (p1(1-p1) + p2(1-p2)) / (p1 - p2)²
    """
    p1 = baseline_cvr
    p2 = baseline_cvr * (1 + mde)   # absolute target CVR after relative lift
    p2 = min(p2, 0.9999)            # cap at <100%

    pooled_var  = p1 * (1 - p1) + p2 * (1 - p2)
    effect_sq   = (p1 - p2) ** 2

    if effect_sq == 0:
        return 999_999, p2  # infinite sample needed

    z_sum_sq    = (Z_ALPHA_2 + Z_BETA) ** 2
    sample_size = math.ceil(z_sum_sq * pooled_var / effect_sq)
    return sample_size, round(p2, 6)


def _compute_runtime(sample_size: int, daily_visitors: int, num_variants: int = 2) -> int:
    """Phase 1 — compute runtime days. Pure function. Accounts for traffic split."""
    visitors_per_variant_per_day = daily_visitors / num_variants
    if visitors_per_variant_per_day <= 0:
        return 9999
    return math.ceil(sample_size / visitors_per_variant_per_day)


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    page_or_element: str,
    hypothesis: str,
    baseline_cvr: float,
    mde: float,
    daily_visitors: int,
    test_type: str,
    output_type: str,
    sample_size: int,
    runtime_days: int,
    target_cvr: float,
    test_guidance: dict,
) -> str:
    """Pure function — assembles the A/B test design brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    output_label  = output_type.replace("_", " ").title()
    guidance_str  = "\n".join(f"  {k}: {v}" for k, v in test_guidance.items())
    feasible_note = (
        f"⚠ Runtime is {runtime_days} days — consider increasing MDE or traffic before running."
        if runtime_days > 28
        else f"✓ Runtime of {runtime_days} days is feasible."
    )

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} CRO and experimentation specialist.

Element/page      : {page_or_element}
Hypothesis        : {hypothesis}
Test type         : {test_type}
Baseline CVR      : {baseline_cvr:.1%}
MDE               : {mde:.0%} relative lift (target CVR: {target_cvr:.1%})
Daily visitors    : {daily_visitors:,}
Sample size/arm   : {sample_size:,} visitors
Runtime estimate  : {runtime_days} days ({feasible_note})
Output required   : {output_label}

Test type guidance:
{guidance_str}

Produce a complete {output_label}:

FOR FULL_DESIGN:
1. HYPOTHESIS AUDIT — score the hypothesis: is it falsifiable? specific? tied to a metric? Rewrite if needed.
2. CONTROL (A) — describe exactly what exists today
3. VARIANT (B) — describe exactly what changes and why (grounded in the hypothesis)
4. PRIMARY METRIC — single conversion goal with measurement method
5. SECONDARY METRICS — 2–3 guardrail metrics to watch (to avoid winning on CVR but losing on revenue)
6. SAMPLE SIZE & RUNTIME — confirm {sample_size:,}/arm, {runtime_days} days; flag risks if long
7. SEGMENTATION PLAN — which segments to analyse post-test (device, traffic source, new vs returning)
8. STOPPING RULES — when to stop early (and why peeking is dangerous)
9. IMPLEMENTATION CHECKLIST — 8-step pre-launch QA list
10. SUCCESS CRITERIA — what "winning" looks like and minimum confidence to ship

FOR HYPOTHESIS_REFINEMENT:
- Score the hypothesis against 5 criteria: specific, measurable, achievable, relevant, falsifiable
- Rewrite it in the format: "We believe [change] will [metric impact] for [audience segment] because [rationale]"
- Identify 3 underlying assumptions that could invalidate the test

FOR VARIANT_BRIEF:
- Exact copy/design specification for the variant (enough for a developer/designer to build)
- What must NOT change between control and variant (isolation principle)
- QA checklist specific to this variant

FOR RESULTS_ANALYSIS_PLAN:
- Statistical interpretation guide (what p-value, confidence interval, and effect size mean)
- Decision matrix: win/lose/inconclusive → what to do in each case
- How long to wait after the test ends before making a decision
- Common interpretation mistakes to avoid

FOR PRIORITISATION_BACKLOG:
- Generate 5 additional test ideas for this page/element
- Score each: Potential / Importance / Ease (PIE framework)
- Ranked backlog with rationale

Be specific. No generic CRO advice that applies to every test."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _design_test(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def ab_test_designer_node(state: ABTestState) -> ABTestState:
    thread_id       = state.get("thread_id", "unknown")
    page_or_element = state.get("page_or_element", "").strip()
    hypothesis      = state.get("hypothesis", "").strip()
    baseline_cvr    = float(state.get("baseline_cvr", 0.03))
    mde             = float(state.get("mde", 0.20))
    daily_visitors  = int(state.get("daily_visitors", 500))
    test_type       = state.get("test_type", "ab").lower().strip()
    output_type     = state.get("output_type", "full_design").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not page_or_element:
        return {**state, "error": "PERMANENT: page_or_element is required"}
    if not hypothesis:
        return {**state, "error": "PERMANENT: hypothesis is required"}
    if not (0 < baseline_cvr < 1):
        return {**state, "error": f"PERMANENT: baseline_cvr must be between 0 and 1 (got {baseline_cvr})"}
    if not (0 < mde < 1):
        return {**state, "error": f"PERMANENT: mde must be between 0 and 1 (got {mde})"}
    if daily_visitors < 10:
        return {**state, "error": f"PERMANENT: daily_visitors must be >= 10 (got {daily_visitors})"}
    if test_type not in VALID_TEST_TYPES:
        return {**state, "error": f"PERMANENT: test_type '{test_type}' not in {VALID_TEST_TYPES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    # ── Phase 1 — pure statistical computation ────────────────────────────────
    sample_size, target_cvr = _compute_sample_size(baseline_cvr, mde)
    runtime_days            = _compute_runtime(sample_size, daily_visitors)
    test_guidance           = _TEST_GUIDANCE.get(test_type, _TEST_GUIDANCE["ab"])

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        page_or_element, hypothesis, baseline_cvr, mde, daily_visitors,
        test_type, output_type, sample_size, runtime_days, target_cvr, test_guidance,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "test_type": test_type,
        "sample_size": sample_size,
        "runtime_days": runtime_days,
        "statistical_power": STATISTICAL_POWER,
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        test_design = _design_test(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "design_chars": len(test_design),
        "runtime_days": runtime_days,
        "sample_size": sample_size,
    })

    return {
        **state,
        "test_design":  test_design,
        "sample_size":  sample_size,
        "runtime_days": runtime_days,
        "target_cvr":   target_cvr,
        "test_guidance": test_guidance,
        "error":        "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ABTestState)
    g.add_node("ab_test_designer", ab_test_designer_node)
    g.set_entry_point("ab_test_designer")
    g.add_edge("ab_test_designer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
