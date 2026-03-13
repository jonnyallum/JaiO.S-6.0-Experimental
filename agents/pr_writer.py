"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : pr_writer
 SKILL : Pr Writer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : company_name (str), news (str — what happened),
               content_type (str), target_outlet (str — optional),
               spokesperson (str — optional), embargo_date (str — optional),
               key_facts (str — optional bullet points)
 Output keys : pr_content (str), word_count (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 format spec lookup →
 Phase 2 Claude write. PARSE_ATTEMPTS = 1.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid content_type (ValueError), empty company_name
               or news string (< 20 chars)
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: content_type, target_outlet,
        has_spokesperson, has_embargo
 POST — logged after success: pr_content char count, word_count

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

ROLE = "pr_writer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES = 3
MAX_TOKENS  = 2000

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_CONTENT_TYPES = {
    "press_release", "media_pitch", "thought_leadership",
    "award_submission", "product_announcement", "crisis_statement",
    "executive_bio", "company_boilerplate"
}

# ── Content type format specs ──────────────────────────────────────────────────
_FORMAT_SPECS: dict[str, dict] = {
    "press_release": {
        "structure":    ["FOR IMMEDIATE RELEASE / EMBARGO header", "Headline (max 12 words)", "Subheadline (optional)", "Dateline + Lead paragraph (who/what/when/where/why)", "Body (2–3 paragraphs of supporting detail)", "Quote from spokesperson", "Boilerplate", "Contact block", "###"],
        "word_target":  400,
        "tone":         "Inverted pyramid — most newsworthy first. Third person. No adjectives that aren't facts.",
        "journalist_note": "First paragraph must stand alone as the entire story. Journalists read 3 lines max.",
    },
    "media_pitch": {
        "structure":    ["Subject line (< 60 chars)", "Opening hook (1 sentence — why this matters NOW)", "The story in 2–3 sentences", "Why your audience specifically", "3 story angles to choose from", "Your ask + deadline", "Credentials line"],
        "word_target":  200,
        "tone":         "Personal, brief, specific. Written to ONE journalist, not a list. Show you've read their work.",
        "journalist_note": "Journalists get 200+ pitches/day. If it reads like a press release, it's deleted.",
    },
    "thought_leadership": {
        "structure":    ["Provocative headline", "Opening hook (counterintuitive claim)", "The argument (3 supporting points)", "Evidence and examples", "Counterargument addressed", "Practical takeaway", "Author bio"],
        "word_target":  800,
        "tone":         "First person. Strong opinions. Challenge conventional wisdom. Specific over generic.",
        "journalist_note": "Must have a unique POV the author is willing to defend publicly.",
    },
    "award_submission": {
        "structure":    ["Category and eligibility statement", "Executive summary (100 words)", "Challenge/context", "Solution/approach", "Measurable results (specific numbers)", "Wider impact", "Supporting evidence"],
        "word_target":  600,
        "tone":         "Evidence-led. Numbers over adjectives. Show don't tell.",
        "journalist_note": "Judges read hundreds — lead with the best result in the first 2 sentences.",
    },
    "product_announcement": {
        "structure":    ["Headline with the product name and single biggest benefit", "The problem it solves (one sentence)", "What it does (3 bullet points)", "Key differentiator", "Pricing/availability", "Quote", "CTA"],
        "word_target":  300,
        "tone":         "Clear, specific, benefit-led. Avoid feature-dumping.",
        "journalist_note": "Journalists want to know: so what? Answer that in the headline.",
    },
    "crisis_statement": {
        "structure":    ["Acknowledge the situation directly", "Express appropriate empathy (if applicable)", "What you know / what you don't (transparent about uncertainty)", "What you're doing about it", "Next update commitment", "Contact for media"],
        "word_target":  200,
        "tone":         "Calm, honest, direct. No defensiveness. No legal hedging if avoidable.",
        "journalist_note": "Speed and transparency matter more than perfect language. Never say 'no comment'.",
    },
    "executive_bio": {
        "structure":    ["Current role and company", "Core expertise (2–3 sentences)", "Career arc (key roles)", "Signature achievement", "Personal angle (optional)", "Education/credentials (brief)"],
        "word_target":  200,
        "tone":         "Third person. Achievement-led. No buzzwords. Human and specific.",
        "journalist_note": "Should answer: why should I talk to this person specifically?",
    },
    "company_boilerplate": {
        "structure":    ["What the company does (one sentence)", "Who it serves", "Key differentiator", "Traction/scale signal", "Founded/HQ line"],
        "word_target":  100,
        "tone":         "Crisp, factual, specific. Avoid 'leading provider of' and similar.",
        "journalist_note": "This appears at the bottom of every press release. It must be flawless.",
    },
}

# ── State ──────────────────────────────────────────────────────────────────────
class PRState(BaseState):
    # Inputs
    company_name:  str   # company issuing the content
    news:          str   # what happened / what to communicate
    content_type:  str   # type of PR content
    target_outlet: str   # optional — specific publication or journalist type
    spokesperson:  str   # optional — name and title for quotes
    embargo_date:  str   # optional — ISO date if embargoed
    key_facts:     str   # optional — bullet points of must-include facts
    thread_id:     str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    format_spec: dict  # content type format spec (owner: this node)

    # Outputs
    pr_content: str   # written PR content (owner: this node)
    word_count: int   # word count of output (owner: this node)
    error:      str   # failure reason if any (owner: this node)


# ── Phase 1 — pure format spec lookup (no Claude) ────────────────────────────

def _get_format_spec(content_type: str) -> dict:
    """
    Phase 1 — pure lookup. Returns format spec for this content type.
    No Claude, no I/O — independently testable.
    """
    return _FORMAT_SPECS[content_type]


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    company_name: str,
    news: str,
    content_type: str,
    target_outlet: str,
    spokesperson: str,
    embargo_date: str,
    key_facts: str,
    format_spec: dict,
) -> str:
    """Pure function — assembles the PR brief from Phase 1 outputs."""
    persona       = get_persona(ROLE)
    structure_str = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(format_spec["structure"]))
    outlet_str    = f"\nTarget outlet/journalist: {target_outlet}" if target_outlet else ""
    spoke_str     = f"\nSpokesperson: {spokesperson}" if spokesperson else ""
    embargo_str   = f"\nEMBARGO UNTIL: {embargo_date}" if embargo_date else ""
    facts_str     = f"\nKey facts to include:\n{key_facts}" if key_facts else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} PR specialist.

Company      : {company_name}
News         : {news}{outlet_str}{spoke_str}{embargo_str}{facts_str}

Content type : {content_type.replace('_', ' ').title()}
Tone         : {format_spec['tone']}
Target length: ~{format_spec['word_target']} words
Journalist note: {format_spec['journalist_note']}

Required structure:
{structure_str}

Write the complete {content_type.replace('_', ' ')} now.

Rules:
- Truth-lock all claims to the provided news and facts — invent nothing
- No adjectives that aren't provable facts ("innovative", "revolutionary", "world-class" — banned)
- If a spokesperson quote is needed and no name provided, write [SPOKESPERSON NAME, TITLE] as placeholder
- If boilerplate is needed and not provided, write [COMPANY BOILERPLATE] as placeholder
- Embargo header: use exactly "EMBARGO UNTIL [DATE] — DO NOT PUBLISH BEFORE THIS DATE" if embargo_date provided
- Press release: end with ### on its own line
- All dates in format: DD Month YYYY"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_pr(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def pr_writer_node(state: PRState) -> PRState:
    thread_id     = state.get("thread_id", "unknown")
    company_name  = state.get("company_name", "").strip()
    news          = state.get("news", "").strip()
    content_type  = state.get("content_type", "press_release").lower().strip()
    target_outlet = state.get("target_outlet", "").strip()
    spokesperson  = state.get("spokesperson", "").strip()
    embargo_date  = state.get("embargo_date", "").strip()
    key_facts     = state.get("key_facts", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not company_name:
        return {**state, "error": "PERMANENT: company_name is required"}
    if len(news) < 20:
        return {**state, "error": "PERMANENT: news must be at least 20 characters"}
    if content_type not in VALID_CONTENT_TYPES:
        return {**state, "error": f"PERMANENT: content_type '{content_type}' not in {VALID_CONTENT_TYPES}"}

    # ── Phase 1 — pure format spec lookup ─────────────────────────────────────
    format_spec = _get_format_spec(content_type)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        company_name, news, content_type,
        target_outlet, spokesperson, embargo_date, key_facts,
        format_spec,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "content_type": content_type,
        "target_outlet": target_outlet or "not specified",
        "has_spokesperson": bool(spokesperson),
        "has_embargo": bool(embargo_date),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        pr_content = _write_pr(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    word_count = len(pr_content.split())

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "pr_chars": len(pr_content),
        "word_count": word_count,
        "content_type": content_type,
    })

    return {
        **state,
        "pr_content":  pr_content,
        "word_count":  word_count,
        "format_spec": format_spec,
        "error":       "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PRState)
    g.add_node("pr_writer", pr_writer_node)
    g.set_entry_point("pr_writer")
    g.add_edge("pr_writer", END)
    return g.compile()
