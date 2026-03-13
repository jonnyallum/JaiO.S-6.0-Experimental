"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : knowledge_base_writer
 SKILL : Knowledge Base Writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : topic (str), doc_type (str), audience (str),
               context (str — domain/product context),
               existing_steps (str — optional raw notes/bullets)
 Output keys : document (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 template skeleton lookup →
 Phase 2 Claude full document. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid doc_type/audience (ValueError), empty topic
               or context
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: doc_type, audience, section count
 POST — logged after success: document char count, doc_type confirmed

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

ROLE = "knowledge_base_writer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2200

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_DOC_TYPES  = {"sop", "runbook", "api_reference", "onboarding", "troubleshooting", "policy"}
VALID_AUDIENCES  = {"technical", "non_technical", "executive", "customer", "internal"}

# ── Document skeleton templates ────────────────────────────────────────────────
_DOC_TEMPLATES: dict[str, dict] = {
    "sop": {
        "title_prefix": "SOP:",
        "sections": [
            "Purpose & Scope",
            "Roles & Responsibilities",
            "Prerequisites",
            "Step-by-Step Procedure",
            "Quality Checks",
            "Exception Handling",
            "Change Log",
        ],
        "tone": "authoritative and precise — numbered steps, no ambiguity",
        "word_target": 600,
    },
    "runbook": {
        "title_prefix": "Runbook:",
        "sections": [
            "Overview & When to Use",
            "Prerequisites & Access",
            "Environment Setup",
            "Execution Steps",
            "Verification & Smoke Tests",
            "Rollback Procedure",
            "Escalation Path",
        ],
        "tone": "ops-ready — assume the reader is under pressure, be unambiguous",
        "word_target": 700,
    },
    "api_reference": {
        "title_prefix": "API Reference:",
        "sections": [
            "Endpoint Overview",
            "Authentication",
            "Request Parameters",
            "Request Example",
            "Response Schema",
            "Response Example",
            "Error Codes",
            "Rate Limits & Notes",
        ],
        "tone": "technical and terse — code blocks, tables, no prose fluff",
        "word_target": 500,
    },
    "onboarding": {
        "title_prefix": "Getting Started:",
        "sections": [
            "Welcome & What You'll Achieve",
            "Day 1 Setup Checklist",
            "Core Concepts (3–5 key ideas)",
            "Your First Win (quick win walkthrough)",
            "Where to Get Help",
            "Next Steps",
        ],
        "tone": "warm and encouraging — celebrate small wins, reduce overwhelm",
        "word_target": 500,
    },
    "troubleshooting": {
        "title_prefix": "Troubleshooting:",
        "sections": [
            "Symptom Overview",
            "Quick Diagnosis Checklist",
            "Common Causes & Fixes",
            "Advanced Diagnostics",
            "When to Escalate",
            "Prevention Tips",
        ],
        "tone": "diagnostic — problem-first, solution-second, be direct",
        "word_target": 550,
    },
    "policy": {
        "title_prefix": "Policy:",
        "sections": [
            "Policy Statement",
            "Scope & Applicability",
            "Definitions",
            "Policy Details",
            "Compliance & Enforcement",
            "Review Schedule",
            "Approvals",
        ],
        "tone": "formal and unambiguous — legally defensible language",
        "word_target": 600,
    },
}

# ── Audience reading level adjustments ────────────────────────────────────────
_AUDIENCE_NOTES: dict[str, str] = {
    "technical":     "Assume deep domain expertise. Use technical terms freely. Include code/command examples.",
    "non_technical": "Avoid jargon. Explain acronyms on first use. Use analogies. Short sentences.",
    "executive":     "Lead with business impact. Numbers and outcomes first. No operational detail.",
    "customer":      "Empathetic and clear. Assume zero prior knowledge. Use 'you' language.",
    "internal":      "Assume company context is known. Reference internal tools/systems directly.",
}

# ── State ──────────────────────────────────────────────────────────────────────
class KnowledgeBaseState(BaseState):
    # Inputs
    topic:          str   # what the document is about
    doc_type:       str   # document type
    audience:       str   # target reader
    context:        str   # domain/product context
    existing_steps: str   # optional raw notes or bullets to incorporate
    thread_id:      str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    template:       dict  # document template spec (owner: this node)
    audience_note:  str   # reading level adjustment (owner: this node)

    # Outputs
    document: str   # full written document (owner: this node)
    error:    str   # failure reason if any (owner: this node)


# ── Phase 1 — pure template lookup (no Claude) ────────────────────────────────

def _get_doc_structure(doc_type: str, audience: str) -> tuple[dict, str]:
    """
    Phase 1 — pure template lookup. Returns (template, audience_note).
    No Claude, no I/O — independently testable.
    """
    template      = _DOC_TEMPLATES[doc_type]
    audience_note = _AUDIENCE_NOTES[audience]
    return template, audience_note


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    topic: str,
    doc_type: str,
    audience: str,
    context: str,
    existing_steps: str,
    template: dict,
    audience_note: str,
) -> str:
    """Pure function — assembles the document brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    sections_str = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(template["sections"]))
    steps_str    = f"\nExisting notes/steps to incorporate:\n{existing_steps}" if existing_steps else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} technical writer.

Document type : {doc_type.upper()} ({template['tone']})
Topic         : {topic}
Audience      : {audience} — {audience_note}
Target length : ~{template['word_target']} words{steps_str}

Context / domain:
{context}

Required sections:
{sections_str}

Write the complete {doc_type} document now.

Title: {template['title_prefix']} {topic}

Rules:
- Every section header must appear exactly as listed
- Truth-lock all content to the provided context — invent no procedures
- Use numbered steps in procedural sections
- Use tables for reference data (parameters, error codes, etc.)
- Use code blocks (```) for commands, config, or API examples
- No filler sections — if a section has no content, write "N/A — not applicable for this context"
- End with a "Last Updated: [DATE]" and "Owner: [OWNER]" footer with placeholder brackets"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_document(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def knowledge_base_writer_node(state: KnowledgeBaseState) -> KnowledgeBaseState:
    thread_id      = state.get("thread_id", "unknown")
    topic          = state.get("topic", "").strip()
    doc_type       = state.get("doc_type", "").lower().strip()
    audience       = state.get("audience", "").lower().strip()
    context        = state.get("context", "").strip()
    existing_steps = state.get("existing_steps", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not topic:
        return {**state, "error": "PERMANENT: topic is required"}
    if not context:
        return {**state, "error": "PERMANENT: context is required — describe the domain/product"}
    if doc_type not in VALID_DOC_TYPES:
        return {**state, "error": f"PERMANENT: doc_type '{doc_type}' not in {VALID_DOC_TYPES}"}
    if audience not in VALID_AUDIENCES:
        return {**state, "error": f"PERMANENT: audience '{audience}' not in {VALID_AUDIENCES}"}

    # ── Phase 1 — pure template lookup ────────────────────────────────────────
    template, audience_note = _get_doc_structure(doc_type, audience)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(topic, doc_type, audience, context, existing_steps, template, audience_note)

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "doc_type": doc_type,
        "audience": audience,
        "section_count": len(template["sections"]),
        "has_existing_steps": bool(existing_steps),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        document = _write_document(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "document_chars": len(document),
        "doc_type": doc_type,
        "audience": audience,
    })

    return {
        **state,
        "document":      document,
        "template":      template,
        "audience_note": audience_note,
        "error":         "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(KnowledgeBaseState)
    g.add_node("knowledge_base_writer", knowledge_base_writer_node)
    g.set_entry_point("knowledge_base_writer")
    g.add_edge("knowledge_base_writer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
