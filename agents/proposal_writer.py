"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : proposal_writer
 SKILL : Proposal Writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : client_name (str), client_problem (str),
               proposed_solution (str), budget_range (str),
               timeline_weeks (int), proposal_type (str),
               our_credentials (str — optional)
 Output keys : proposal (str), proposal_sections (list[str])
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 structure computation →
 Phase 2 Claude write. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid proposal_type (ValueError), empty client_name,
               client_problem, or proposed_solution,
               timeline_weeks < 1 or > 104
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: proposal_type, budget_range,
        timeline_weeks, section_count
 POST — logged after success: proposal char count, section count

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

ROLE = "proposal_writer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES     = 3
MAX_TOKENS      = 2800   # proposals need depth — exec summary + full scope sections
TIMELINE_MAX    = 104    # 2 years max planning horizon (weeks)

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_PROPOSAL_TYPES = {
    "website", "saas_build", "ecommerce", "branding", "marketing_retainer",
    "seo_campaign", "automation", "consultancy", "general"
}

# ── Proposal section maps — per type ──────────────────────────────────────────
_SECTION_MAP: dict[str, list[str]] = {
    "website": [
        "Executive Summary", "Understanding Your Challenge",
        "Our Proposed Solution", "Project Scope & Deliverables",
        "Technology Stack", "Project Timeline", "Investment",
        "Why Us", "Next Steps",
    ],
    "saas_build": [
        "Executive Summary", "Problem Statement", "Product Vision",
        "Feature Scope (MVP)", "Technical Architecture Overview",
        "Development Phases", "Team & Responsibilities",
        "Investment & Payment Terms", "Success Metrics", "Next Steps",
    ],
    "ecommerce": [
        "Executive Summary", "Your Current Situation",
        "Proposed Solution", "Platform & Integrations",
        "Design & UX Approach", "Migration Plan",
        "Timeline", "Investment", "ROI Projection", "Next Steps",
    ],
    "branding": [
        "Executive Summary", "Brand Discovery Process",
        "Deliverables", "Creative Direction Brief",
        "Timeline & Milestones", "Investment", "Next Steps",
    ],
    "marketing_retainer": [
        "Executive Summary", "Current Situation Analysis",
        "Proposed Strategy", "Monthly Deliverables",
        "KPIs & Reporting Cadence", "Team", "Monthly Investment",
        "Terms & Conditions", "Next Steps",
    ],
    "seo_campaign": [
        "Executive Summary", "SEO Audit Findings",
        "Strategy & Approach", "Deliverables by Phase",
        "Timeline", "Projected Outcomes", "Investment", "Next Steps",
    ],
    "automation": [
        "Executive Summary", "Current Process Analysis",
        "Automation Blueprint", "Technical Scope",
        "Implementation Phases", "Expected ROI",
        "Investment", "Next Steps",
    ],
    "consultancy": [
        "Executive Summary", "Scope of Engagement",
        "Methodology", "Deliverables", "Timeline",
        "Team", "Investment", "Terms", "Next Steps",
    ],
    "general": [
        "Executive Summary", "Understanding the Brief",
        "Proposed Solution", "Scope & Deliverables",
        "Timeline", "Investment", "Why Us", "Next Steps",
    ],
}

# ── State ──────────────────────────────────────────────────────────────────────
class ProposalState(BaseState):
    # Inputs
    client_name:       str   # prospect company/person name
    client_problem:    str   # their pain point or goal
    proposed_solution: str   # what we're offering
    budget_range:      str   # e.g. "£3,000–£5,000" or "TBC"
    timeline_weeks:    int   # project duration in weeks
    proposal_type:     str   # type of proposal
    our_credentials:   str   # optional — relevant past work / team strengths
    thread_id:         str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    sections:          list  # ordered section list for this proposal type (owner: this node)

    # Outputs
    proposal:          str        # full written proposal (owner: this node)
    proposal_sections: list[str]  # section names used (owner: this node)
    error:             str        # failure reason if any (owner: this node)


# ── Phase 1 — pure structure computation (no Claude) ─────────────────────────

def _get_structure(proposal_type: str, timeline_weeks: int) -> tuple[list[str], str]:
    """
    Phase 1 — pure lookup. Returns (sections, timeline_label).
    No Claude, no I/O — independently testable.
    """
    sections = _SECTION_MAP.get(proposal_type, _SECTION_MAP["general"])

    if timeline_weeks <= 2:
        timeline_label = f"{timeline_weeks} week{'s' if timeline_weeks > 1 else ''} (rapid delivery)"
    elif timeline_weeks <= 8:
        timeline_label = f"{timeline_weeks} weeks"
    elif timeline_weeks <= 26:
        months = round(timeline_weeks / 4.33, 1)
        timeline_label = f"{timeline_weeks} weeks (~{months} months)"
    else:
        months = round(timeline_weeks / 4.33)
        timeline_label = f"~{months} months ({timeline_weeks} weeks)"

    return sections, timeline_label


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    client_name: str,
    client_problem: str,
    proposed_solution: str,
    budget_range: str,
    timeline_label: str,
    proposal_type: str,
    our_credentials: str,
    sections: list[str],
) -> str:
    """Pure function — assembles the proposal brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    sections_str = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))
    creds_str    = f"\nOur credentials to highlight:\n{our_credentials}" if our_credentials else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} proposal writer.

Write a complete, winning {proposal_type.replace('_', ' ')} proposal.

Client         : {client_name}
Their problem  : {client_problem}
Our solution   : {proposed_solution}
Budget         : {budget_range}
Timeline       : {timeline_label}{creds_str}

Required sections (in order):
{sections_str}

Rules:
- Write every section in full — no placeholders except [CLIENT_LOGO] and [OUR_LOGO]
- Executive Summary: 3 punchy bullet points — problem, solution, outcome
- Investment section: present budget as an investment, not a cost; include payment terms suggestion
- "Why Us" or equivalent: specific, not generic — reference the credentials provided
- Timeline: present as phases with milestones, not just a duration
- Next Steps: exactly 3 steps numbered 1–3, first step is always "Schedule a 30-minute discovery call"
- Tone: confident and professional — we are the expert they've been looking for
- No corporate waffle: "leverage", "synergies", "holistic" are banned
- End with a signature block: [AGENCY NAME] | [CONTACT EMAIL] | [PHONE]"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_proposal(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def proposal_writer_node(state: ProposalState) -> ProposalState:
    thread_id          = state.get("thread_id", "unknown")
    client_name        = state.get("client_name", "").strip()
    client_problem     = state.get("client_problem", "").strip()
    proposed_solution  = state.get("proposed_solution", "").strip()
    budget_range       = state.get("budget_range", "TBC").strip()
    timeline_weeks     = int(state.get("timeline_weeks", 8))
    proposal_type      = state.get("proposal_type", "general").lower().strip()
    our_credentials    = state.get("our_credentials", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not client_name:
        return {**state, "error": "PERMANENT: client_name is required"}
    if not client_problem:
        return {**state, "error": "PERMANENT: client_problem is required"}
    if not proposed_solution:
        return {**state, "error": "PERMANENT: proposed_solution is required"}
    if proposal_type not in VALID_PROPOSAL_TYPES:
        return {**state, "error": f"PERMANENT: proposal_type '{proposal_type}' not in {VALID_PROPOSAL_TYPES}"}
    if not (1 <= timeline_weeks <= TIMELINE_MAX):
        return {**state, "error": f"PERMANENT: timeline_weeks must be 1–{TIMELINE_MAX}"}

    # ── Phase 1 — pure structure computation ──────────────────────────────────
    sections, timeline_label = _get_structure(proposal_type, timeline_weeks)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        client_name, client_problem, proposed_solution,
        budget_range, timeline_label, proposal_type,
        our_credentials, sections,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "proposal_type": proposal_type,
        "budget_range": budget_range,
        "timeline_weeks": timeline_weeks,
        "section_count": len(sections),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        proposal = _write_proposal(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "proposal_chars": len(proposal),
        "section_count": len(sections),
        "proposal_type": proposal_type,
    })

    return {
        **state,
        "proposal":          proposal,
        "proposal_sections": sections,
        "sections":          sections,
        "error":             "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ProposalState)
    g.add_node("proposal_writer", proposal_writer_node)
    g.set_entry_point("proposal_writer")
    g.add_edge("proposal_writer", END)
    return g.compile()
