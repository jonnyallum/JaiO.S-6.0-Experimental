"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : case_study_writer
 SKILL : Case Study Writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : client_name (str), problem (str), solution (str),
               results (str — metrics and outcomes),
               format (str), industry (str — optional)
 Output keys : case_study (str), proof_score (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 proof scoring →
 Phase 2 Claude narrative. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid format/industry (ValueError), missing problem
               or solution, results string empty or < 20 chars
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: format, industry, proof_score
 POST — logged after success: case study char count, format confirmed

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import re

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

ROLE = "case_study_writer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2500   # long-form needs depth

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_FORMATS    = {"long_form", "one_pager", "slide_deck_outline", "social_snippet", "pdf_brief"}
VALID_INDUSTRIES = {"saas", "ecommerce", "agency", "retail", "finance", "healthcare", "general"}

# ── Format output specs ────────────────────────────────────────────────────────
_FORMAT_SPECS: dict[str, dict] = {
    "long_form": {
        "sections":  ["Executive Summary", "The Challenge", "The Solution", "Implementation", "Results & ROI", "Testimonial", "What's Next"],
        "word_target": 800,
        "tone":      "journalistic — narrative arc with data anchors",
    },
    "one_pager": {
        "sections":  ["Problem Snapshot", "Solution", "Key Results (3 bullets)", "Quote"],
        "word_target": 300,
        "tone":      "punchy — every word earns its place",
    },
    "slide_deck_outline": {
        "sections":  ["Slide 1: Hook stat", "Slide 2: Client context", "Slide 3: Challenge", "Slide 4: Solution approach", "Slide 5: Results", "Slide 6: Proof quote", "Slide 7: CTA"],
        "word_target": 400,
        "tone":      "slide note format — headline + 3 bullet points per slide",
    },
    "social_snippet": {
        "sections":  ["Hook (1 line)", "Problem (1–2 lines)", "Solution (2 lines)", "Result stat (1 line)", "CTA (1 line)"],
        "word_target": 150,
        "tone":      "LinkedIn-native — conversational, no corporate speak",
    },
    "pdf_brief": {
        "sections":  ["Header stats", "Challenge", "Approach", "Outcomes", "Quote", "About section"],
        "word_target": 500,
        "tone":      "professional — confident, no fluff",
    },
}

# ── Proof signal patterns ──────────────────────────────────────────────────────
_METRIC_PATTERNS = [
    r'\d+%',             # percentages
    r'\$[\d,]+',         # dollar amounts
    r'£[\d,]+',          # pound amounts
    r'\d+x\b',           # multipliers
    r'\d+\.\d+x\b',      # decimal multipliers
    r'ROI',              # ROI mentions
    r'\d+\s*(days|weeks|months)',  # time-based results
    r'(increased|decreased|grew|reduced|improved|doubled|tripled)',
]

# ── State ──────────────────────────────────────────────────────────────────────
class CaseStudyState(BaseState):
    # Inputs
    client_name: str   # client / company name
    problem:     str   # the challenge faced
    solution:    str   # what was built / done
    results:     str   # metrics and outcomes
    format:      str   # output format
    industry:    str   # optional industry context
    thread_id:   str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    proof_score: int   # 0–100 metric richness score (owner: this node)
    format_spec: dict  # format output spec (owner: this node)

    # Outputs
    case_study: str   # written case study (owner: this node)
    error:      str   # failure reason if any (owner: this node)


# ── Phase 1 — pure proof scoring (no Claude) ──────────────────────────────────

def _score_proof(results: str, problem: str, solution: str) -> int:
    """
    Phase 1 — score the richness of provided proof. Pure function, no Claude.
    Returns 0–100: 40 pts for metrics in results, 30 for completeness, 30 for specificity.
    """
    score = 0

    # Metric richness (up to 40 pts — 8 pts per distinct signal type found)
    found_signals = set()
    for pattern in _METRIC_PATTERNS:
        if re.search(pattern, results, re.IGNORECASE):
            found_signals.add(pattern)
    score += min(len(found_signals) * 8, 40)

    # Content completeness (up to 30 pts)
    if len(results.split()) >= 30:
        score += 10
    if len(problem.split()) >= 20:
        score += 10
    if len(solution.split()) >= 20:
        score += 10

    # Specificity (up to 30 pts — numbers, names, timeframes)
    all_text = f"{problem} {solution} {results}"
    if re.search(r'\d+', all_text):
        score += 10
    if re.search(r'(week|month|year|quarter|Q[1-4])', all_text, re.IGNORECASE):
        score += 10
    if len(all_text.split()) >= 100:
        score += 10

    return min(score, 100)


def _get_format_spec(fmt: str) -> dict:
    """Phase 1 — pure lookup of format spec. No Claude."""
    return _FORMAT_SPECS[fmt]


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    client_name: str,
    problem: str,
    solution: str,
    results: str,
    format: str,
    industry: str,
    proof_score: int,
    format_spec: dict,
) -> str:
    """Pure function — assembles the case study brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    sections_str = "\n".join(f"  - {s}" for s in format_spec["sections"])
    industry_str = f"\nIndustry context: {industry}" if industry and industry != "general" else ""
    proof_note   = (
        "Strong proof available — lean into the numbers."
        if proof_score >= 60
        else "Limited metrics provided — use qualitative storytelling and extract maximum specificity from what's given."
    )

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} case study writer.

Format        : {format} (tone: {format_spec['tone']}, ~{format_spec['word_target']} words)
Client        : {client_name}{industry_str}
Proof score   : {proof_score}/100 — {proof_note}

Raw inputs:
PROBLEM   : {problem}
SOLUTION  : {solution}
RESULTS   : {results}

Required sections:
{sections_str}

Write the complete {format} case study now.

Rules:
- Truth-lock every claim to the raw inputs — invent nothing
- Lead with the most compelling result (strongest number or transformation)
- Use the client name naturally — no "our client" or "the company"
- Every section header must be present
- No corporate jargon: "leveraged", "synergies", "holistic approach" are banned
- If a testimonial quote is included, mark it clearly as [PLACEHOLDER — replace with real quote]"""


def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_case_study(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text
_generate = _write_case_study  # spec alias



# ── Node ───────────────────────────────────────────────────────────────────────

def case_study_writer_node(state: CaseStudyState) -> CaseStudyState:
    thread_id   = state.get("thread_id", "unknown")
    client_name = state.get("client_name", "").strip()
    problem     = state.get("problem", "").strip()
    solution    = state.get("solution", "").strip()
    results     = state.get("results", "").strip()
    format      = state.get("format", "long_form").lower().strip()
    industry    = state.get("industry", "general").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not client_name:
        return {**state, "error": "PERMANENT: client_name is required"}
    if not problem:
        return {**state, "error": "PERMANENT: problem description is required"}
    if not solution:
        return {**state, "error": "PERMANENT: solution description is required"}
    if len(results) < 20:
        return {**state, "error": "PERMANENT: results must be at least 20 characters"}
    if format not in VALID_FORMATS:
        return {**state, "error": f"PERMANENT: format '{format}' not in {VALID_FORMATS}"}
    if industry not in VALID_INDUSTRIES:
        return {**state, "error": f"PERMANENT: industry '{industry}' not in {VALID_INDUSTRIES}"}

    # ── Phase 1 — pure proof scoring and format lookup ────────────────────────
    proof_score = _score_proof(results, problem, solution)
    format_spec = _get_format_spec(format)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(client_name, problem, solution, results, format, industry, proof_score, format_spec)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "format": format, "industry": industry,
        "proof_score": proof_score,
        "results_chars": len(results),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        case_study = _write_case_study(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "case_study_chars": len(case_study),
        "format": format,
        "proof_score": proof_score,
    })

    return {
        **state,
        "case_study": case_study,
        "proof_score": proof_score,
        "format_spec": format_spec,
        "agent": ROLE,

        "error": None,
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(CaseStudyState)
    g.add_node("case_study_writer", case_study_writer_node)
    g.set_entry_point("case_study_writer")
    g.add_edge("case_study_writer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
