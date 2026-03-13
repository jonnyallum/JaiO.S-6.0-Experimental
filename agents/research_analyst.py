"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : research_analyst
 SKILL : Research Analyst — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : question (str), research_type (str), depth (str),
               sources (str — optional: provided text/data to synthesise),
               domain (str — optional: subject area for context)
 Output keys : research_report (str), confidence_score (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 research framework
 selection → Phase 2 Claude synthesis. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid research_type/depth (ValueError), empty question
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: research_type, depth, has_sources,
        framework selected
 POST — logged after success: report char count, confidence_score

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

ROLE = "research_analyst"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2800   # deep research needs full token budget

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_RESEARCH_TYPES = {
    "market_research", "competitive_analysis", "academic_synthesis",
    "technical_review", "trend_analysis", "fact_check",
    "due_diligence", "policy_review", "general"
}
VALID_DEPTHS = {"quick_brief", "standard_report", "deep_dive"}

# ── Research framework map ────────────────────────────────────────────────────
_FRAMEWORKS: dict[str, dict[str, str]] = {
    "market_research": {
        "quick_brief":     "TAM/SAM/SOM snapshot + 3 demand signals + go/no-go",
        "standard_report": "Market sizing + segmentation + growth drivers + barriers + competitive landscape",
        "deep_dive":       "Full PESTLE + Porter's Five Forces + market map + white space analysis + 12-month outlook",
    },
    "competitive_analysis": {
        "quick_brief":     "Top 3 competitors — positioning, pricing, key differentiator",
        "standard_report": "5-competitor deep profile + feature matrix + pricing comparison + SWOT",
        "deep_dive":       "Full competitive intelligence — business model, funding, team, product roadmap signals, reviews synthesis, GTM strategy",
    },
    "academic_synthesis": {
        "quick_brief":     "3 key studies + consensus finding + knowledge gaps",
        "standard_report": "Systematic literature summary + methodology comparison + evidence quality rating",
        "deep_dive":       "Meta-analysis synthesis + conflicting findings reconciliation + research agenda recommendations",
    },
    "technical_review": {
        "quick_brief":     "Technology overview + maturity level + fit assessment",
        "standard_report": "Architecture analysis + trade-offs + ecosystem + adoption risk",
        "deep_dive":       "Full technical due diligence — architecture, scalability, security posture, vendor lock-in, migration complexity",
    },
    "trend_analysis": {
        "quick_brief":     "Top 3 emerging trends + 6-month signal strength",
        "standard_report": "Trend taxonomy + adoption curve + early indicators + impact assessment",
        "deep_dive":       "Weak signal detection + S-curve positioning + scenario planning (base/bull/bear) + strategic implications",
    },
    "fact_check": {
        "quick_brief":     "Claim verdict (TRUE/FALSE/PARTIALLY TRUE/UNVERIFIABLE) + primary evidence",
        "standard_report": "Claim breakdown + source quality assessment + counter-evidence + verdict with confidence",
        "deep_dive":       "Full epistemic audit — primary sources, methodology review, expert consensus, confidence interval on verdict",
    },
    "due_diligence": {
        "quick_brief":     "Red flags summary + 3 key risks + go/no-go signal",
        "standard_report": "Financial health signals + legal exposure + operational risks + team assessment",
        "deep_dive":       "Full commercial, technical, legal, and people DD framework with scoring matrix",
    },
    "policy_review": {
        "quick_brief":     "Policy summary + key obligations + compliance gap",
        "standard_report": "Full policy analysis + stakeholder impact + implementation requirements",
        "deep_dive":       "Comparative policy analysis + jurisdictional differences + risk matrix + compliance roadmap",
    },
    "general": {
        "quick_brief":     "Question answered in 300 words with 3 cited sources",
        "standard_report": "Structured report with evidence, analysis, and conclusions",
        "deep_dive":       "Exhaustive analysis with source quality rating, uncertainty quantification, and actionable conclusions",
    },
}

# ── Depth-specific output requirements ────────────────────────────────────────
_DEPTH_SPECS: dict[str, dict] = {
    "quick_brief":     {"word_target": 400,  "sections": 3, "citations": 3,  "time_label": "5-min read"},
    "standard_report": {"word_target": 800,  "sections": 6, "citations": 6,  "time_label": "15-min read"},
    "deep_dive":       {"word_target": 1600, "sections": 9, "citations": 10, "time_label": "30-min read"},
}

# ── State ──────────────────────────────────────────────────────────────────────
class ResearchState(BaseState):
    # Inputs
    question:      str   # research question or brief
    research_type: str   # type of research
    depth:         str   # depth of analysis
    sources:       str   # optional: provided text/data to synthesise
    domain:        str   # optional: subject domain for context
    thread_id:     str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    framework:  str   # selected research framework description (owner: this node)
    depth_spec: dict  # depth output requirements (owner: this node)

    # Outputs
    research_report: str   # full research output (owner: this node)
    confidence_score: int  # 0–100 confidence in findings (owner: this node)
    error:           str   # failure reason if any (owner: this node)


# ── Phase 1 — pure framework selection (no Claude) ────────────────────────────

def _select_framework(research_type: str, depth: str) -> tuple[str, dict]:
    """
    Phase 1 — pure lookup. Returns (framework_description, depth_spec).
    No Claude, no I/O — independently testable.
    """
    type_map   = _FRAMEWORKS.get(research_type, _FRAMEWORKS["general"])
    framework  = type_map.get(depth, type_map.get("standard_report", ""))
    depth_spec = _DEPTH_SPECS[depth]
    return framework, depth_spec


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    question: str,
    research_type: str,
    depth: str,
    sources: str,
    domain: str,
    framework: str,
    depth_spec: dict,
) -> str:
    """Pure function — assembles the research brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    domain_str   = f"\nDomain/subject area: {domain}" if domain else ""
    sources_str  = f"\n\nProvided sources/data to synthesise:\n{sources[:3000]}" if sources else "\n\nNo sources provided — draw on training knowledge. Clearly label knowledge boundaries."
    output_label = depth.replace("_", " ").title()

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} research specialist and academic synthesiser.

Research question : {question}
Research type     : {research_type.replace('_', ' ')}
Depth             : {output_label} (~{depth_spec['word_target']} words, {depth_spec['time_label']})
Framework         : {framework}{domain_str}{sources_str}

Produce a {output_label} research report.

TRUTH-LOCK PROTOCOL (mandatory):
- Every factual claim must be traceable to: (a) provided sources, (b) widely-verified knowledge, or (c) explicitly labelled as "Inferred" / "Estimated"
- Uncertainty must be quantified: "HIGH confidence", "MEDIUM confidence", "LOW confidence / unverifiable"
- Never present a contested finding as settled
- If sources conflict, present both positions with evidence quality assessment

STRUCTURE (use {depth_spec['sections']} sections minimum):
1. EXECUTIVE SUMMARY ({depth_spec['citations']}–sentence overview + confidence rating)
2. KEY FINDINGS (numbered, evidence-backed — most important first)
3. ANALYSIS ({framework})
4. EVIDENCE QUALITY ASSESSMENT (rate sources: primary / secondary / expert consensus / anecdotal)
5. KNOWLEDGE GAPS (what we don't know and why it matters)
6. CONCLUSIONS & IMPLICATIONS (specific, not generic)
{"7. RECOMMENDED NEXT STEPS (3 concrete actions)" if depth != "quick_brief" else ""}
{"8. DISSENTING VIEWS (present the strongest counterargument)" if depth == "deep_dive" else ""}
{"9. METHODOLOGY NOTE (how this analysis was conducted, limitations)" if depth == "deep_dive" else ""}

CITATION FORMAT: [SOURCE: description of source | confidence: HIGH/MEDIUM/LOW]
Use inline citations immediately after each claim.

Confidence score: at the very end, output a single line:
CONFIDENCE_SCORE: [0-100] — [one sentence justification]"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _synthesise(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _extract_confidence(report: str) -> int:
    """Phase 1 (post) — extract confidence score from report. Pure function."""
    import re
    m = re.search(r'CONFIDENCE_SCORE:\s*(\d+)', report)
    return int(m.group(1)) if m else 50


# ── Node ───────────────────────────────────────────────────────────────────────

def research_analyst_node(state: ResearchState) -> ResearchState:
    thread_id     = state.get("thread_id", "unknown")
    question      = state.get("question", "").strip()
    research_type = state.get("research_type", "general").lower().strip()
    depth         = state.get("depth", "standard_report").lower().strip()
    sources       = state.get("sources", "").strip()
    domain        = state.get("domain", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not question:
        return {**state, "error": "PERMANENT: question is required"}
    if research_type not in VALID_RESEARCH_TYPES:
        return {**state, "error": f"PERMANENT: research_type '{research_type}' not in {VALID_RESEARCH_TYPES}"}
    if depth not in VALID_DEPTHS:
        return {**state, "error": f"PERMANENT: depth '{depth}' not in {VALID_DEPTHS}"}

    # ── Phase 1 — pure framework selection ────────────────────────────────────
    framework, depth_spec = _select_framework(research_type, depth)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(question, research_type, depth, sources, domain, framework, depth_spec)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "research_type": research_type,
        "depth":         depth,
        "has_sources":   bool(sources),
        "framework":     framework[:80],
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        research_report = _synthesise(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    confidence_score = _extract_confidence(research_report)

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "report_chars":    len(research_report),
        "confidence_score": confidence_score,
        "depth":           depth,
    })

    return {
        **state,
        "research_report":  research_report,
        "confidence_score": confidence_score,
        "framework":        framework,
        "depth_spec":       depth_spec,
        "error":            "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ResearchState)
    g.add_node("research_analyst", research_analyst_node)
    g.set_entry_point("research_analyst")
    g.add_edge("research_analyst", END)
    return g.compile()
