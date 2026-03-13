"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : chatbot_designer
 SKILL : Chatbot Designer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : bot_name (str), bot_purpose (str), platform (str),
               audience (str), tone (str), output_type (str),
               key_intents (str — comma-separated user goals),
               escalation_path (str — optional)
 Output keys : chatbot_design (str), intent_count (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 platform config lookup →
 Phase 2 Claude design output. INTENT_LIMIT = 20 max intents per design.

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid platform/tone/output_type (ValueError),
               empty bot_purpose or audience,
               intent count > INTENT_LIMIT
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: platform, output_type, intent_count,
        has_escalation_path
 POST — logged after success: design char count, intent_count

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

ROLE = "chatbot_designer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES  = 3
MAX_TOKENS   = 2400
INTENT_LIMIT = 20   # max intents per design session

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_PLATFORMS = {
    "website_widget", "whatsapp", "instagram_dm", "facebook_messenger",
    "slack", "telegram", "sms", "voice_ivr", "intercom", "general"
}
VALID_TONES = {
    "professional", "friendly", "casual", "empathetic",
    "authoritative", "playful", "concise", "warm"
}
VALID_OUTPUT_TYPES = {
    "system_prompt", "conversation_flow", "intent_map",
    "persona_card", "full_design", "failure_handling"
}

# ── Platform constraints ───────────────────────────────────────────────────────
_PLATFORM_SPECS: dict[str, dict] = {
    "website_widget": {
        "char_limit":      None,
        "rich_media":      "Buttons, cards, carousels, file uploads",
        "session_timeout": "30 min idle",
        "constraints":     "Keep initial message under 80 chars. Offer quick replies.",
    },
    "whatsapp": {
        "char_limit":      4096,
        "rich_media":      "Images, docs, audio, list messages, reply buttons (max 3)",
        "session_timeout": "24h window after user initiates",
        "constraints":     "No outbound messages without user opt-in. Max 3 quick reply buttons.",
    },
    "instagram_dm": {
        "char_limit":      1000,
        "rich_media":      "Images, stickers, story replies — no carousels in DM",
        "session_timeout": "7-day messaging window",
        "constraints":     "Tone must feel human — users expect casual IG-native voice.",
    },
    "facebook_messenger": {
        "char_limit":      2000,
        "rich_media":      "Buttons, carousels, quick replies, webview",
        "session_timeout": "24h window",
        "constraints":     "Max 3 buttons per card. Persistent menu available.",
    },
    "slack": {
        "char_limit":      40000,
        "rich_media":      "Block Kit — sections, images, buttons, select menus",
        "session_timeout": "No timeout — persistent",
        "constraints":     "Use slash commands for discoverability. DMs or channels.",
    },
    "telegram": {
        "char_limit":      4096,
        "rich_media":      "Inline keyboards, reply keyboards, files, polls",
        "session_timeout": "No timeout — persistent",
        "constraints":     "Inline keyboards preferred over reply keyboards for flows.",
    },
    "sms": {
        "char_limit":      160,
        "rich_media":      "None (plain text only for SMS; MMS for images)",
        "session_timeout": "Session concept doesn't apply",
        "constraints":     "Every message must be under 160 chars. No markdown.",
    },
    "voice_ivr": {
        "char_limit":      None,
        "rich_media":      "Audio only — no visual",
        "session_timeout": "Call duration",
        "constraints":     "Write for ears, not eyes. Short sentences. No acronyms.",
    },
    "intercom": {
        "char_limit":      None,
        "rich_media":      "Articles, buttons, apps, video",
        "session_timeout": "Persistent chat history",
        "constraints":     "Handoff to human agent must be seamless. Tag conversations.",
    },
    "general": {
        "char_limit":      None,
        "rich_media":      "Platform-dependent",
        "session_timeout": "Platform-dependent",
        "constraints":     "Design for the lowest common denominator — text first.",
    },
}

# ── State ──────────────────────────────────────────────────────────────────────
class ChatbotState(BaseState):
    # Inputs
    bot_name:        str   # name of the chatbot
    bot_purpose:     str   # what problem the bot solves
    platform:        str   # deployment platform
    audience:        str   # who will be talking to the bot
    tone:            str   # conversational tone
    output_type:     str   # type of design output
    key_intents:     str   # comma-separated user goals/intents
    escalation_path: str   # optional — how to hand off to a human
    thread_id:       str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    platform_spec: dict  # platform constraints and capabilities (owner: this node)
    intent_list:   list  # parsed intents (owner: this node)
    intent_count:  int   # count of parsed intents (owner: this node)

    # Outputs
    chatbot_design: str   # full design output (owner: this node)
    error:          str   # failure reason if any (owner: this node)


# ── Phase 1 — pure platform config lookup and intent parsing (no Claude) ──────

def _parse_intents(key_intents: str) -> list[str]:
    """Phase 1 — parse comma-separated intents into clean list. Pure function."""
    return [i.strip() for i in key_intents.split(",") if i.strip()]


def _get_platform_spec(platform: str) -> dict:
    """Phase 1 — pure lookup of platform constraints. No Claude."""
    return _PLATFORM_SPECS.get(platform, _PLATFORM_SPECS["general"])


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    bot_name: str,
    bot_purpose: str,
    platform: str,
    audience: str,
    tone: str,
    output_type: str,
    intent_list: list[str],
    escalation_path: str,
    platform_spec: dict,
) -> str:
    """Pure function — assembles the chatbot design brief from Phase 1 outputs."""
    persona        = get_persona(ROLE)
    output_label   = output_type.replace("_", " ").title()
    intents_str    = "\n".join(f"  - {i}" for i in intent_list) if intent_list else "  (not specified — infer from purpose)"
    escalation_str = f"\nEscalation path: {escalation_path}" if escalation_path else "\nEscalation: No human handoff defined"
    char_limit_str = f"Character limit: {platform_spec['char_limit']}" if platform_spec["char_limit"] else "Character limit: None"
    spec_str       = f"  {char_limit_str}\n  Rich media: {platform_spec['rich_media']}\n  Session: {platform_spec['session_timeout']}\n  Constraints: {platform_spec['constraints']}"

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} conversational AI designer.

Bot name     : {bot_name}
Purpose      : {bot_purpose}
Platform     : {platform.replace('_', ' ')}
Audience     : {audience}
Tone         : {tone}
Output type  : {output_label}{escalation_str}

Platform specs:
{spec_str}

User intents to handle:
{intents_str}

Produce a complete {output_label}:

FOR SYSTEM_PROMPT:
- Full system prompt (ready to paste into an LLM API)
- Identity, personality, capabilities, limitations
- Response format instructions
- What to do when the bot doesn't know
- Escalation trigger conditions

FOR CONVERSATION_FLOW:
- Welcome message (first message the user sees)
- Main menu / quick reply options
- Detailed flow for each intent: user message → bot response → next step
- Error/fallback handling for unrecognised input
- Closing / handoff messages

FOR INTENT_MAP:
- Intent name | Description | Example user phrases (3+) | Bot action | Success state
- Table format, all {len(intent_list) if intent_list else 'identified'} intents covered

FOR PERSONA_CARD:
- Name, personality traits (5 adjectives)
- Voice and tone guide (do/don't table)
- Sample responses for 5 situations: greeting, confusion, complaint, success, handoff
- Banned phrases / off-brand language list

FOR FULL_DESIGN:
- All of the above combined into a complete design document

FOR FAILURE_HANDLING:
- Taxonomy of failure types (can't understand, wrong platform, escalation needed, etc.)
- Response strategy for each failure type
- Fallback message library (6–10 variants to avoid repetition)
- Recovery flows — how to get back on track

Write specifically for {platform.replace('_', ' ')}. Respect the platform constraints above."""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _design_chatbot(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def chatbot_designer_node(state: ChatbotState) -> ChatbotState:
    thread_id        = state.get("thread_id", "unknown")
    bot_name         = state.get("bot_name", "").strip()
    bot_purpose      = state.get("bot_purpose", "").strip()
    platform         = state.get("platform", "general").lower().strip()
    audience         = state.get("audience", "").strip()
    tone             = state.get("tone", "friendly").lower().strip()
    output_type      = state.get("output_type", "full_design").lower().strip()
    key_intents      = state.get("key_intents", "")
    escalation_path  = state.get("escalation_path", "").strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not bot_name:
        return {**state, "error": "PERMANENT: bot_name is required"}
    if not bot_purpose:
        return {**state, "error": "PERMANENT: bot_purpose is required"}
    if not audience:
        return {**state, "error": "PERMANENT: audience is required"}
    if platform not in VALID_PLATFORMS:
        return {**state, "error": f"PERMANENT: platform '{platform}' not in {VALID_PLATFORMS}"}
    if tone not in VALID_TONES:
        return {**state, "error": f"PERMANENT: tone '{tone}' not in {VALID_TONES}"}
    if output_type not in VALID_OUTPUT_TYPES:
        return {**state, "error": f"PERMANENT: output_type '{output_type}' not in {VALID_OUTPUT_TYPES}"}

    # ── Phase 1 — pure platform lookup and intent parsing ─────────────────────
    platform_spec = _get_platform_spec(platform)
    intent_list   = _parse_intents(key_intents)
    intent_count  = len(intent_list)

    if intent_count > INTENT_LIMIT:
        return {**state, "error": f"PERMANENT: {intent_count} intents exceeds INTENT_LIMIT={INTENT_LIMIT} — split into multiple design passes"}

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        bot_name, bot_purpose, platform, audience, tone,
        output_type, intent_list, escalation_path, platform_spec,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "platform": platform,
        "output_type": output_type,
        "intent_count": intent_count,
        "has_escalation_path": bool(escalation_path),
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        chatbot_design = _design_chatbot(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "design_chars": len(chatbot_design),
        "intent_count": intent_count,
        "platform": platform,
        "output_type": output_type,
    })

    return {
        **state,
        "chatbot_design": chatbot_design,
        "intent_count":   intent_count,
        "platform_spec":  platform_spec,
        "intent_list":    intent_list,
        "error":          "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ChatbotState)
    g.add_node("chatbot_designer", chatbot_designer_node)
    g.set_entry_point("chatbot_designer")
    g.add_edge("chatbot_designer", END)
    return g.compile()
