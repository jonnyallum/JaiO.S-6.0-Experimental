"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : persona_builder
 SKILL : Persona Builder — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : product_name (str), product_description (str),
               persona_type (str), num_personas (int — default 2),
               raw_signals (str — optional: interviews, reviews, data),
               output_depth (str)
 Output keys : personas (str), signal_summary (dict)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 signal extraction →
 Phase 2 Claude persona synthesis. PERSONA_LIMIT = 5.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid persona_type/output_depth (ValueError),
               empty product_name or product_description,
               num_personas > PERSONA_LIMIT
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: persona_type, num_personas,
        output_depth, signal_count
 POST — logged after success: personas char count, signal_summary keys

 Persona: identity injected at runtime via personas/config.py — no
          names or nicknames hardcoded in this skill file.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import re
from typing import Any

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

ROLE = "persona_builder"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES   = 3
MAX_TOKENS    = 2600
PERSONA_LIMIT = 5

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_PERSONA_TYPES = {
    "buyer", "user", "icp", "anti_persona", "champion", "influencer", "general"
}
VALID_OUTPUT_DEPTHS = {"quick_sketch", "full_profile", "journey_map"}

# ── Persona depth specs ────────────────────────────────────────────────────────
_DEPTH_SPECS: dict[str, dict] = {
    "quick_sketch": {
        "sections":    ["Name & Role", "Demographics", "Top 3 Goals", "Top 3 Frustrations", "Buying trigger"],
        "word_target": 200,
        "use_case":    "Fast alignment in team workshops",
    },
    "full_profile": {
        "sections":    [
            "Name, Photo Description & Tagline",
            "Demographics & Firmographics",
            "Psychographics (values, personality, lifestyle)",
            "Goals & Motivations (functional + emotional + social)",
            "Frustrations & Pain Points",
            "Information sources (where they learn)",
            "Objections to buying",
            "Buying journey (awareness → decision)",
            "Quote that captures their worldview",
            "How to reach them",
        ],
        "word_target": 500,
        "use_case":    "Campaign planning, messaging strategy, product decisions",
    },
    "journey_map": {
        "sections":    [
            "Persona snapshot",
            "Stage 1 — Unaware: situation before problem recognition",
            "Stage 2 — Problem aware: trigger event, emotion, search behaviour",
            "Stage 3 — Solution aware: evaluation criteria, trust signals needed",
            "Stage 4 — Product aware: comparison behaviour, objections",
            "Stage 5 — Purchase: decision driver, risk mitigation needed",
            "Stage 6 — Post-purchase: success condition, advocacy trigger",
            "Touchpoint map: where to reach them at each stage",
        ],
        "word_target": 800,
        "use_case":    "Content strategy, CRO, onboarding design",
    },
}

# ── Persona type instructions ──────────────────────────────────────────────────
_TYPE_FOCUS: dict[str, str] = {
    "buyer":      "The person who signs the contract or makes the purchase. Focus on business objectives, ROI concerns, and approval authority.",
    "user":       "The person who uses the product daily. Focus on workflow, usability friction, feature needs, and daily context.",
    "icp":        "Ideal Customer Profile — the company/person who gets maximum value fastest. Focus on firmographics, use case fit, and expansion potential.",
    "anti_persona":"The customer you do NOT want. Document why — wasted support time, wrong use case, churn risk. Use to sharpen targeting.",
    "champion":   "Internal advocate at the client who sells you upward. Focus on their career motivations, internal credibility, and what they need to win internally.",
    "influencer": "Person who recommends but doesn't buy directly. Focus on what they care about professionally and what makes them recommend to others.",
    "general":    "Balanced buyer/user hybrid persona covering both purchase decision and product usage.",
}

# ── State ──────────────────────────────────────────────────────────────────────
class PersonaState(BaseState):
    # Inputs
    product_name:        str   # product or service
    product_description: str   # what it does and who it serves
    persona_type:        str   # type of persona
    num_personas:        int   # number of personas to build
    raw_signals:         str   # optional: quotes, reviews, interviews, survey data
    output_depth:        str   # depth of persona output
    thread_id:           str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    signal_summary: dict  # extracted signal counts from raw_signals (owner: this node)
    depth_spec:     dict  # output depth specification (owner: this node)

    # Outputs
    personas: str   # full persona output (owner: this node)
    error:    str   # failure reason if any (owner: this node)


# ── Phase 1 — pure signal extraction (no Claude) ──────────────────────────────

_PAIN_PATTERNS     = [r'\b(frustrat|annoying|hate|struggle|difficult|can\'t|broken|slow|expensive|confusing)\w*\b']
_GOAL_PATTERNS     = [r'\b(want|need|wish|hope|try|goal|achieve|improve|grow|save|increase)\w*\b']
_EMOTION_PATTERNS  = [r'\b(feel|felt|worried|anxious|excited|confident|overwhelmed|stressed|happy)\w*\b']


def _extract_signals(raw_signals: str) -> dict:
    """
    Phase 1 — extract surface-level counts from raw signal text.
    Pure function — no Claude, no I/O. Independently testable.
    """
    if not raw_signals:
        return {"pain_mentions": 0, "goal_mentions": 0, "emotion_mentions": 0, "word_count": 0, "has_quotes": False}

    text       = raw_signals.lower()
    word_count = len(text.split())

    pain_count  = sum(len(re.findall(p, text)) for p in _PAIN_PATTERNS)
    goal_count  = sum(len(re.findall(p, text)) for p in _GOAL_PATTERNS)
    emo_count   = sum(len(re.findall(p, text)) for p in _EMOTION_PATTERNS)
    has_quotes  = '"' in raw_signals or "'" in raw_signals

    return {
        "pain_mentions":    pain_count,
        "goal_mentions":    goal_count,
        "emotion_mentions": emo_count,
        "word_count":       word_count,
        "has_quotes":       has_quotes,
    }


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    product_name: str,
    product_description: str,
    persona_type: str,
    num_personas: int,
    raw_signals: str,
    output_depth: str,
    signal_summary: dict,
    depth_spec: dict,
    type_focus: str,
) -> str:
    """Pure function — assembles the persona brief from Phase 1 outputs."""
    persona      = get_persona(ROLE)
    sections_str = "\n".join(f"  - {s}" for s in depth_spec["sections"])
    signals_str  = f"\nRaw research signals provided ({signal_summary['word_count']} words):\n{raw_signals[:2000]}" if raw_signals else "\nNo raw signals provided — infer from product description and best-practice ICP patterns."
    signal_note  = f"Signal quality: {signal_summary['pain_mentions']} pain mentions, {signal_summary['goal_mentions']} goal mentions, {signal_summary['emotion_mentions']} emotion signals." if raw_signals else ""

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} customer research specialist.

Product        : {product_name}
Description    : {product_description}
Persona type   : {persona_type} — {type_focus}
Output depth   : {output_depth} (~{depth_spec['word_target']} words per persona)
Use case       : {depth_spec['use_case']}
Personas to build: {num_personas}
{signal_note}{signals_str}

Build exactly {num_personas} {persona_type} persona(s) for {product_name}.

For each persona, cover these sections:
{sections_str}

Format:
===PERSONA [N]: [FIRST NAME] [LAST NAME]===
[All sections with headers]
===END PERSONA [N]===

Rules:
- Give each persona a specific, memorable name (not "Marketing Mary")
- Every pain point must be visceral and specific — not "they want efficiency"
- Every goal must be tied to a real business/personal outcome
- The "Quote" must sound like a real human, not a marketing brief
- Anti-personas: be brutal and specific about why they're the wrong customer
- If raw signals provided: truth-lock every claim to the signals — cite paraphrased evidence
- If no signals: clearly label as "Hypothesised" at the top of each persona"""


def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _synthesise_personas(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Phase 2 — Claude call. Only TRANSIENT errors (529/overload) are retried."""
    metrics.start()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text
_generate = _synthesise_personas  # spec alias



# ── Node ───────────────────────────────────────────────────────────────────────

def persona_builder_node(state: PersonaState) -> PersonaState:
    thread_id           = state.get("thread_id", "unknown")
    product_name        = state.get("product_name", "").strip()
    product_description = state.get("product_description", "").strip()
    persona_type        = state.get("persona_type", "buyer").lower().strip()
    num_personas        = int(state.get("num_personas", 2))
    raw_signals         = state.get("raw_signals", "").strip()
    output_depth        = state.get("output_depth", "full_profile").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not product_name:
        return {**state, "error": "PERMANENT: product_name is required"}
    if not product_description:
        return {**state, "error": "PERMANENT: product_description is required"}
    if persona_type not in VALID_PERSONA_TYPES:
        return {**state, "error": f"PERMANENT: persona_type '{persona_type}' not in {VALID_PERSONA_TYPES}"}
    if output_depth not in VALID_OUTPUT_DEPTHS:
        return {**state, "error": f"PERMANENT: output_depth '{output_depth}' not in {VALID_OUTPUT_DEPTHS}"}
    if num_personas > PERSONA_LIMIT:
        return {**state, "error": f"PERMANENT: num_personas {num_personas} exceeds PERSONA_LIMIT={PERSONA_LIMIT}"}

    # ── Phase 1 — pure signal extraction ──────────────────────────────────────
    signal_summary = _extract_signals(raw_signals)
    depth_spec     = _DEPTH_SPECS[output_depth]
    type_focus     = _TYPE_FOCUS.get(persona_type, _TYPE_FOCUS["general"])

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        product_name, product_description, persona_type,
        num_personas, raw_signals, output_depth,
        signal_summary, depth_spec, type_focus,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "persona_type": persona_type,
        "num_personas": num_personas,
        "output_depth": output_depth,
        "signal_count": signal_summary["word_count"],
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        personas = _synthesise_personas(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "personas_chars": len(personas),
        "signal_summary": signal_summary,
        "num_personas": num_personas,
    })

    return {
        **state,
        "personas":       personas,
        "signal_summary": signal_summary,
        "depth_spec":     depth_spec,
        "agent": ROLE,

        "error": None,
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PersonaState)
    g.add_node("persona_builder", persona_builder_node)
    g.set_entry_point("persona_builder")
    g.add_edge("persona_builder", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
