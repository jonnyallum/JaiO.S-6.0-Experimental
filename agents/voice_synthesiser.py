"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : voice_synthesiser
SKILL : Voice Synthesiser

ElevenLabs Voice Synthesis Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : script_brief (str), voice_use (VALID_VOICE_USES), tone_style (VALID_TONE_STYLES), duration_target_seconds (int)
    Outputs: production_script (str), voice_direction (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty brief, invalid use) raise immediately.

Failure Discrimination:
    PERMANENT  → empty script_brief, unknown voice_use → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before voice spec calculation
    POST - production_script + voice_direction persisted after successful generation
"""

from __future__ import annotations

from state.base import BaseState

import re
from typing import TypedDict

import anthropic
import structlog
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint
from tools.supabase_tools import SupabaseStateLogger  # checkpoint alias
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

ROLE        = "voice_synthesiser"
MAX_RETRIES = 3
MAX_TOKENS  = 2000

VALID_VOICE_USES = {
    "podcast_intro", "explainer", "ad_voiceover", "character_voice",
    "notification", "ux_audio", "training_narration", "general",
}
VALID_TONE_STYLES = {
    "professional", "casual", "dramatic", "warm", "authoritative", "energetic", "calm",
}

# ── Voice Specification Matrix ────────────────────────────────────────────────
_VOICE_SPECS = {
    "podcast_intro": {
        "pacing":               "medium - 130–150 wpm, pause after hook",
        "emphasis":             "first line punchy, brand name stressed",
        "style_note":           "conversational but polished, host-like warmth",
        "model_recommendation": "eleven_turbo_v2_5 (low latency) or eleven_multilingual_v2",
        "word_budget_per_min":  140,
        "ssml_hints":           ['<break time="500ms"/>', '<prosody rate="slow">key phrase</prosody>'],
    },
    "explainer": {
        "pacing":               "slow-medium - 120–130 wpm, pause between steps",
        "emphasis":             "step numbers, key terms, outcomes",
        "style_note":           "clear, friendly, no jargon - 'you' language throughout",
        "model_recommendation": "eleven_multilingual_v2 (clarity focus)",
        "word_budget_per_min":  125,
        "ssml_hints":           ['<break time="300ms"/>', '<emphasis level="strong">key term</emphasis>'],
    },
    "ad_voiceover": {
        "pacing":               "dynamic - fast build, slow CTA",
        "emphasis":             "problem hook, product name, CTA phrase",
        "style_note":           "energy peak at 70% through, land soft on CTA",
        "model_recommendation": "eleven_turbo_v2_5 (crisp, broadcast-ready)",
        "word_budget_per_min":  150,
        "ssml_hints":           ['<prosody rate="fast">', '<prosody rate="slow">call to action</prosody>'],
    },
    "character_voice": {
        "pacing":               "character-defined - match the persona's energy",
        "emphasis":             "character-specific verbal tics and catch phrases",
        "style_note":           "consistency is key - establish voice in first 3 lines",
        "model_recommendation": "eleven_multilingual_v2 with custom voice clone (IVC/PVC)",
        "word_budget_per_min":  130,
        "ssml_hints":           ['<prosody pitch="high">', '<prosody pitch="low">deep moment</prosody>'],
    },
    "notification": {
        "pacing":               "fast - 160–180 wpm, punchy and complete",
        "emphasis":             "action word, key data point",
        "style_note":           "2–3 sentences max, mobile-friendly, no fluff",
        "model_recommendation": "eleven_turbo_v2_5 (ultra-low latency)",
        "word_budget_per_min":  170,
        "ssml_hints":           [],
    },
    "ux_audio": {
        "pacing":               "natural - matches UI interaction rhythm",
        "emphasis":             "confirmation words (done, complete, ready)",
        "style_note":           "warm and reassuring, never condescending",
        "model_recommendation": "eleven_turbo_v2_5 (real-time)",
        "word_budget_per_min":  150,
        "ssml_hints":           ['<prosody volume="soft">'],
    },
    "training_narration": {
        "pacing":               "deliberate - 110–120 wpm, pause after each key point",
        "emphasis":             "learning objectives, warnings, summaries",
        "style_note":           "authoritative but approachable - 'let us look at' not 'you must'",
        "model_recommendation": "eleven_multilingual_v2",
        "word_budget_per_min":  115,
        "ssml_hints":           ['<break time="700ms"/>', '<emphasis level="moderate">objective</emphasis>'],
    },
    "general": {
        "pacing":               "medium - 130 wpm",
        "emphasis":             "key nouns and action verbs",
        "style_note":           "clear, engaging, natural delivery",
        "model_recommendation": "eleven_turbo_v2_5",
        "word_budget_per_min":  130,
        "ssml_hints":           [],
    },
}

_TONE_MODIFIERS = {
    "professional":  "measured, precise, zero filler words, confident pauses",
    "casual":        "contractions throughout, conversational fillers OK, relaxed rhythm",
    "dramatic":      "dynamic range - quiet build to strong peaks, cinematic pacing",
    "warm":          "smiling voice implied, empathetic language, personal pronouns",
    "authoritative": "declarative sentences, strong endings, no upward inflection",
    "energetic":     "faster overall pace, punchy short sentences, exclamation rhythm",
    "calm":          "lower pace, longer pauses, soft emphasis, grounding language",
}


class VoiceSynthesiserState(BaseState):
    workflow_id:             str
    timestamp:               str
    agent:                   str
    error:                   str | None
    script_brief:            str
    voice_use:               str
    tone_style:              str
    duration_target_seconds: int
    production_script:       str
    voice_direction:         str


# ── Phase 1 - Voice Brief (pure, no Claude) ───────────────────────────────────
def _build_voice_spec(voice_use: str, tone_style: str, duration_target_seconds: int) -> dict:
    """Returns voice_spec dict - pure calculation, no Claude."""
    spec          = _VOICE_SPECS.get(voice_use, _VOICE_SPECS["general"])
    tone_mod      = _TONE_MODIFIERS.get(tone_style, "")
    word_budget   = round(spec["word_budget_per_min"] * duration_target_seconds / 60)
    return {
        "pacing":               spec["pacing"],
        "emphasis":             spec["emphasis"],
        "style_note":           spec["style_note"],
        "model_recommendation": spec["model_recommendation"],
        "tone_modifier":        tone_mod,
        "word_budget":          word_budget,
        "ssml_hints":           spec["ssml_hints"],
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Script Generation ─────────────────────────────────────────
def _build_script_prompt(state: VoiceSynthesiserState, voice_spec: dict) -> str:
    persona    = get_persona(ROLE)
    brief      = state["script_brief"]
    voice_use  = state.get("voice_use", "general")
    tone_style = state.get("tone_style", "professional")
    duration   = state.get("duration_target_seconds", 60)

    ssml_hints_text = "\n".join(f"  {h}" for h in voice_spec["ssml_hints"]) if voice_spec["ssml_hints"] else "  None required"

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Write a production-ready voice script with full ElevenLabs direction notes.

VOICE USE: {voice_use}
TONE STYLE: {tone_style}
TARGET DURATION: {duration} seconds
WORD BUDGET: ~{voice_spec['word_budget']} words

VOICE SPECIFICATION:
  Pacing:    {voice_spec['pacing']}
  Emphasis:  {voice_spec['emphasis']}
  Style:     {voice_spec['style_note']}
  Tone Mod:  {voice_spec['tone_modifier']}
  Model:     {voice_spec['model_recommendation']}
  SSML Hints:
{ssml_hints_text}

SCRIPT BRIEF:
'''
{brief[:3000]}
'''

YOUR TASK:
1. Write the production script - exactly {voice_spec['word_budget']} words (±10%).
2. Mark emphasis with [STRESS: word], pauses with [PAUSE: Xms], and tone shifts with [SHIFT: direction].
3. Write the ElevenLabs Voice Direction card - 5 specific parameters.
4. Suggest 3 voice profile characteristics for voice casting or cloning.

OUTPUT FORMAT:
## Production Script
**Use Case:** {voice_use}
**Tone:** {tone_style}
**Target Duration:** {duration}s (~{voice_spec['word_budget']} words)
**Recommended Model:** {voice_spec['model_recommendation']}

---
[PRODUCTION SCRIPT - marked up with STRESS / PAUSE / SHIFT tags]
---

## ElevenLabs Voice Direction
**Stability:** [0.0–1.0 - lower = more expressive]
**Similarity Boost:** [0.0–1.0 - higher = closer to reference]
**Style:** [0.0–1.0 - exaggeration level]
**Speaker Boost:** [true/false]
**Speed:** [0.5–2.0]

## Voice Casting Profile
1. [characteristic - e.g. "slightly gravelly, mid-30s male, broadcast trained"]
2. [characteristic]
3. [characteristic]

## SSML-Ready Version
[Script with proper SSML markup for ElevenLabs API]
"""

_build_prompt = _build_script_prompt  # spec alias


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
def _generate_script(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
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


def voice_synthesiser_node(state: VoiceSynthesiserState) -> VoiceSynthesiserState:
    thread_id   = state.get("workflow_id", "local")
    brief       = state.get("script_brief", "").strip()
    voice_use   = state.get("voice_use", "general")
    tone_style  = state.get("tone_style", "professional")
    duration    = state.get("duration_target_seconds", 60)

    if not brief:
        raise ValueError("PERMANENT: script_brief is required.")
    if voice_use not in VALID_VOICE_USES:
        raise ValueError(f"PERMANENT: voice_use '{voice_use}' not in {VALID_VOICE_USES}")
    if tone_style not in VALID_TONE_STYLES:
        raise ValueError(f"PERMANENT: tone_style '{tone_style}' not in {VALID_TONE_STYLES}")

    checkpoint("PRE", thread_id, ROLE, {"voice_use": voice_use, "tone_style": tone_style, "duration": duration})

    voice_spec = _build_voice_spec(voice_use, tone_style, duration)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_script_prompt(state, voice_spec)

    try:
        output = _generate_script(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529):
            raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    # Split production script and voice direction
    direction_match = re.search(r'## ElevenLabs Voice Direction([\s\S]+?)(?=## |$)', output)
    voice_direction = direction_match.group(0) if direction_match else ""

    checkpoint("POST", thread_id, ROLE, {"voice_use": voice_use, "word_budget": voice_spec["word_budget"]})

    return {
        **state,
        "agent":             ROLE,
        "production_script": output,
        "voice_direction":   voice_direction,
        "error":             None,
    }


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(VoiceSynthesiserState)
    g.add_node("voice_synthesiser", voice_synthesiser_node)
    g.set_entry_point("voice_synthesiser")
    g.add_edge("voice_synthesiser", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
