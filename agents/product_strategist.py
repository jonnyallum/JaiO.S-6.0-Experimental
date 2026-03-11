"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 product_strategist — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), stage (str), goal (str),
               user_pain (str), constraints (str — optional),
               output_type (str)
 Output keys : strategy_output (str), framework_used (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 framework selection →
 Phase 2 Claude strategy output. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid stage/output_type (ValueError), empty
               product_name, goal, or user_pain
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: stage, output_type, framework
 POST — logged after success: output char count, framework_used

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

import anthropic
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona

# ── Identity ──────────────────────────────────────────────────────────────────
ROLE = "product_strategist"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2400

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_STAGES      = {"idea", "pre_launch", "mvp", "growth", "scale", "mature", "pivot"}
VALID_OUTPUT_TYPES = {
    "roadmap", "sprint_plan", "user_stories", "feature_prioritisation",
    "okrs", "north_star_metric", "jobs_to_be_done", "general"
}

# ── Framework selection — stage × output_type → best framework ────────────────
_FRAMEWORK_MAP: dict[str, dict[str, str]] = {
    "roadmap": {
        "idea":       "Now / Next / Later horizon roadmap — low commitment, high flexibility",
        "pre_launch": "Theme-based roadmap with launch milestone gates",
        "mvp":        "MoSCoW prioritised roadmap — Must/Should/Could/Won't",
        "growth":     "Opportunity Solution Tree roadmap — outcome-driven",
        "scale":      "OKR-aligned quarterly roadmap with capacity planning",
        "mature":     "Strategic bets roadmap — innovation vs core vs technical debt",
        "pivot":      "Clean-sheet roadmap — kill list + rebuild priorities",
    },
    "sprint_plan": {
        "default": "2-week sprint: goal → epics → stories → acceptance criteria → points",
    },
    "user_stories": {
        "default": "As a [persona], I want [goal] so that [outcome] + acceptance criteria + edge cases",
    },
    "feature_prioritisation": {
        "idea":    "Effort/Impact 2×2 matrix",
        "mvp":     "RICE scoring (Reach × Impact × Confidence ÷ Effort)",
        "growth":  "ICE scoring (Impact × Confidence × Ease)",
        "scale":   "Weighted scoring matrix (revenue, retention, NPS, cost)",
        "default": "RICE scoring (Reach × Impact × Confidence ÷ Effort)",
    },
    "okrs": {
        "default": "3 Objectives × 3 Key Results each — measurable, time-bound, ambitious",
    },
    "north_star_metric": {
        "default": "NSM discovery: value moment → leading indicators → lagging proof",
    },
    "jobs_to_be_done": {
        "default": "JTBD: functional job + emotional job + social job + switch triggers",
    },
    "general": {
        "default": "Opportunity Solution Tree: outcome → opportunities → solutions → experiments",
    },
}


def _select_framework(output_type: str, stage: str) -> str:
    """Phase 1 — pure framework selection. No Claude."""
    type_map = _FRAMEWORK_MAP.get(output_type, _FRAMEWORK_MAP["general"])
    return type_map.get(stage, type_map.get("default", "Opportunity Solution Tree"))


# ── State ──────────────────────────────────────────────────────────────────────
class ProductStrategyState(TypedDict):
    # Inputs
    product_name: str   # product or feature name
    stage:        str   # product lifecycle stage
    goal:         str   # strategic goal for this output
    user_pain:    str   # primary user pain point being addressed
    constraints:  str   # optional — budget, time, team size, tech debt
    output_type:  str   # type of strategy output required
    thread_id:    str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    framework_used: str   # selected framework (owner: this node)

    # Outputs
    strategy_output: str   # full strategy document (owner: this node)
    error:           str   # failure reason if any (owner: this node)


# ── Phase 1 — pure framework selection (no Claude) ────────────────────────────

def _get_framework(output_type: str, stage: str) -> str:
    """
    Phase 1 — pure lookup. Returns the best framework for the stage/output combo.
    No Claude, no I/O — independently testable.
    """
    return _select_framework(output_type, stage)


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    product_name: str,
    stage: str,
    goal: str,
    user_pain: str,
    constraints: str,
    output_type: str,
    framework: str,
) -> str:
    """Pure function — assembles the strategy brief from Phase 1 outputs."""
    persona         = get_persona(ROLE)
    output_label    = output_type.replace("_", " ").title()
    constraints_str = f"\nConstraints: {constraints}" if constraints else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} product strategist.

Product        : {product_name}
Stage          : {stage}
Strategic goal : {goal}
User pain      : {user_pain}{constraints_str}

Output type    : {output_label}
Framework      : {framework}

Produce a complete, actionable {output_label} using the {framework} framework.

Output rules by type:
- roadmap: 3–5 themes, each with 2–4 initiatives, clearly labelled Now/Next/Later or by quarter
- sprint_plan: sprint goal + 5–8 user stories with story points (Fibonacci) + definition of done
- user_stories: 6–10 stories, each with "As a / I want / So that" + 3 acceptance criteria
- feature_prioritisation: score 6–10 features using the framework, show scoring table, output ranked list with rationale
- okrs: 3 Objectives × 3 Key Results each — every KR must be measurable with a target number
- north_star_metric: identify the NSM, 3 leading indicators, 2 lagging proofs, measurement method
- jobs_to_be_done: 3–5 jobs, each with functional/emotional/social dimension + switch trigger
- general: opportunity map → top 3 opportunities → solution hypotheses → experiment designs

Ruthless prioritisation over exhaustive lists. Every item must earn its place.
No filler. No "TBD". Every output must be usable in a real planning session."""


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

def product_strategist_node(state: ProductStrategyState) -> ProductStrategyState:
    thread_id    = state.get("thread_id", "unknown")
    product_name = state.get("product_name", "").strip()
    stage        = state.get("stage", "").lower().strip()
    goal         = state.get("goal", "").strip()
    user_pain    = state.get("user_pain", "").strip()
    constraints  = state.get("constraints", "").strip()
    output_type  = state.get("output_type", "general").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not product_name:
        return {**state, "error": "PERMANENT: product_name is required"}
    if not goal:
        return {**state, "error": "PERMANENT: goal is required"}
    if not user_pain:
        return {**state, "error": "PERMANENT: user_pain is required"}
    if stage not in VALID_STAGES:
        return {**state, "error": f"PERMANENT: stage '{stage}' not in {VALID_STAGES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    # ── Phase 1 — pure framework selection ────────────────────────────────────
    framework = _get_framework(output_type, stage)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(product_name, stage, goal, user_pain, constraints, output_type, framework)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "stage": stage, "output_type": output_type, "framework": framework,
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
        "output_chars": len(strategy_output),
        "framework_used": framework,
        "output_type": output_type,
    })

    return {
        **state,
        "strategy_output": strategy_output,
        "framework_used":  framework,
        "error":           "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ProductStrategyState)
    g.add_node("product_strategist", product_strategist_node)
    g.set_entry_point("product_strategist")
    g.add_edge("product_strategist", END)
    return g.compile()
