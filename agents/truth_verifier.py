"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : truth_verifier
SKILL : Truth Verifier

Truth Verifier - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : artifact (str), artifact_type (VALID_ARTIFACT_TYPES), check_level (VALID_CHECK_LEVELS)
    Outputs: verification_report (str), gates_passed (int), gates_failed (int), confidence (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty artifact, invalid type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty artifact, unknown artifact_type → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before gate analysis
    POST - verification_report + gate scores persisted after successful generation
"""

from __future__ import annotations

from state.base import BaseState

import re
from typing import TypedDict, Any

import anthropic
import structlog
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint
from tools.supabase_tools import SupabaseStateLogger  # checkpoint alias
from langgraph.graph import StateGraph, END

# ── Constants ─────────────────────────────────────────────────────────────────
log = structlog.get_logger()

ROLE = "truth_verifier"
MAX_RETRIES = 3
MAX_TOKENS  = 2400

VALID_ARTIFACT_TYPES = {
    "skill_md", "agent_spec", "technical_doc", "marketing_copy",
    "research_report", "code_review", "proposal", "general",
}
VALID_CHECK_LEVELS = {"quick_scan", "standard_audit", "deep_verify"}

# ── 13-Gate Checklist ─────────────────────────────────────────────────────────
_GATES = {
    "G01": ("No placeholder text (TBD / lorem ipsum / TODO)",        r'(TBD|TODO|lorem ipsum|PLACEHOLDER|FIXME)'),
    "G02": ("No unqualified superlatives (best, fastest, #1)",       r'(best|fastest|number one|#1|world-class|industry-leading)'),
    "G03": ("No unverified statistics (X% without source)", r'\d+\s*%[^\.]{0,80}(?!source|ref|cite|\[)'),
    "G04": ("No vague timeframes (soon, shortly, in the future)",    r'(soon|shortly|in the future|at some point|eventually)'),
    "G05": ("No passive-voice evasion on claims",                    r'(it is believed|it has been said|some say|reportedly)'),
    "G06": ("No contradictory statements within same document",      None),  # LLM gate
    "G07": ("No unattributed quotes",                                r'"[^"]{20,}"(?!\s*[-–]\s*\w)'),
    "G08": ("No fabricated examples or case studies",                None),  # LLM gate
    "G09": ("No broken/circular logic chains",                       None),  # LLM gate
    "G10": ("No hardcoded personal/company names in templates",      r'(Antigravity|jonnyallum)'),
    "G11": ("Confidence signals present (HIGH/MEDIUM/LOW labels)",   r'(HIGH|MEDIUM|LOW)\s*confidence'),
    "G12": ("Actionable outputs - not just observations",            None),  # LLM gate
    "G13": ("No Latin placeholder text",                             r'(lorem|ipsum|dolor|consectetur|adipiscing)'),
}

_DEPTH_SETTINGS = {
    "quick_scan":      {"llm_gates": False, "note": "Regex gates only - fast pass/fail"},
    "standard_audit":  {"llm_gates": True,  "note": "Regex + LLM reasoning for G06/G08/G09/G12"},
    "deep_verify":     {"llm_gates": True,  "note": "Full 13-gate analysis with confidence scoring"},
}

# ── State ─────────────────────────────────────────────────────────────────────
class TruthVerifierState(BaseState):
    workflow_id:         str
    timestamp:           str
    agent:               str
    error:               str | None
    artifact:            str
    artifact_type:       str
    check_level:         str
    verification_report: str
    gates_passed:        int
    gates_failed:        int
    confidence:          str


# ── Phase 1 - Gate Analysis (pure, no Claude) ─────────────────────────────────
def _run_gate_checks(artifact: str, check_level: str) -> tuple[int, int, dict[str, Any]]:
    """Run regex gates; returns (passed, failed, gate_results)."""
    gate_results: dict[str, Any] = {}
    passed = 0
    failed = 0
    for gate_id, (description, pattern) in _GATES.items():
        if pattern is None:
            # LLM gate - mark as "deferred" for Phase 2
            gate_results[gate_id] = {"description": description, "result": "DEFERRED", "match": None}
        else:
            matches = re.findall(pattern, artifact, re.IGNORECASE)
            if matches:
                gate_results[gate_id] = {"description": description, "result": "FAIL", "match": matches[:3]}
                failed += 1
            else:
                gate_results[gate_id] = {"description": description, "result": "PASS", "match": None}
                passed += 1
    return passed, failed, gate_results

def _format_gate_summary(gate_results: dict) -> str:
    lines = []
    for gate_id, data in gate_results.items():
        icon = {"PASS": "✓", "FAIL": "✗", "DEFERRED": "◌"}.get(data["result"], "?")
        line = f"  {icon} [{gate_id}] {data['description']}"
        if data.get("match"):
            line += f"\n      \u21b3 Found: {data['match']}"
        lines.append(line)
    return "\n".join(lines)

_build_prompt = None  # assigned below after _build_verify_prompt definition


# ── Phase 2 - Claude Verification ────────────────────────────────────────────
def _build_verify_prompt(state: TruthVerifierState, gate_results: dict, passed: int, failed: int) -> str:
    persona       = get_persona(ROLE)
    artifact      = state["artifact"]
    artifact_type = state.get("artifact_type", "general")
    check_level   = state.get("check_level", "standard_audit")
    depth_note    = _DEPTH_SETTINGS[check_level]["note"]
    gate_summary  = _format_gate_summary(gate_results)
    deferred      = [gid for gid, d in gate_results.items() if d["result"] == "DEFERRED"]

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Perform a truth-lock verification audit on the artifact below.

CHECK LEVEL: {check_level} - {depth_note}
ARTIFACT TYPE: {artifact_type}

REGEX GATE RESULTS ({passed} passed, {failed} failed):
{gate_summary}

DEFERRED LLM GATES TO EVALUATE: {', '.join(deferred) if deferred else 'None'}

ARTIFACT:
'''
{artifact[:6000]}
'''

YOUR TASK:
1. Evaluate all DEFERRED gates (G06, G08, G09, G12) with specific evidence from the artifact.
2. Identify any additional trust violations not caught by regex.
3. Assign an overall confidence score: HIGH / MEDIUM / LOW.
4. Write a structured verification report.

OUTPUT FORMAT:
## Truth Verification Report
**Artifact Type:** {artifact_type}
**Check Level:** {check_level}
**Overall Confidence:** [HIGH / MEDIUM / LOW]

### Gate Results Summary
[List all 13 gates with PASS / FAIL / DEFERRED→PASS or DEFERRED→FAIL]

### Violations Found
[Numbered list of each violation with exact quote + gate reference]

### Trust Score
[X / 13 gates passed]

### Verdict
[APPROVED - safe to use | CONDITIONAL - fix listed violations first | BLOCKED - fundamental trust issues]

### Recommended Fixes
[Specific, actionable corrections for every FAIL]

CONFIDENCE SCORE: [HIGH / MEDIUM / LOW] - [one-sentence justification]
"""

_build_prompt = _build_verify_prompt  # spec alias


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
def _verify(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
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


# ── Node Entry Point ──────────────────────────────────────────────────────────
def truth_verifier_node(state: TruthVerifierState) -> TruthVerifierState:
    thread_id     = state.get("workflow_id", "local")
    artifact      = state.get("artifact", "").strip()
    artifact_type = state.get("artifact_type", "general")
    check_level   = state.get("check_level", "standard_audit")

    # ── Phase 1 - validate inputs ────────────────────────────────────────────
    if not artifact:
        raise ValueError("PERMANENT: artifact is required.")
    if artifact_type not in VALID_ARTIFACT_TYPES:
        raise ValueError(f"PERMANENT: artifact_type '{artifact_type}' not in {VALID_ARTIFACT_TYPES}")
    if check_level not in VALID_CHECK_LEVELS:
        raise ValueError(f"PERMANENT: check_level '{check_level}' not in {VALID_CHECK_LEVELS}")

    checkpoint("PRE", thread_id, ROLE, {"artifact_type": artifact_type, "check_level": check_level})

    passed, failed, gate_results = _run_gate_checks(artifact, check_level)

    # ── Phase 2 - Claude verification ────────────────────────────────────────
    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_verify_prompt(state, gate_results, passed, failed)

    try:
        report = _verify(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    # Extract confidence from report
    confidence_match = re.search(r'CONFIDENCE SCORE:\s*(HIGH|MEDIUM|LOW)', report)
    confidence = confidence_match.group(1) if confidence_match else "UNKNOWN"

    checkpoint("POST", thread_id, ROLE, {
        "gates_passed": passed, "gates_failed": failed, "confidence": confidence,
    })

    return {
        **state,
        "agent":               ROLE,
        "verification_report": report,
        "gates_passed":        passed,
        "gates_failed":        failed,
        "confidence":          confidence,
        "error":               None,
    }


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(TruthVerifierState)
    g.add_node("truth_verifier", truth_verifier_node)
    g.set_entry_point("truth_verifier")
    g.add_edge("truth_verifier", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
