"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : content_scaler
SKILL : Content Scaling — topic + brand voice → multiple A/B copy variants for testing

Node Contract (@langraph doctrine):
  Inputs   : topic (str), brand_voice (str), platform (str),
             variant_count (int), cta (str) — immutable after entry
  Outputs  : variants (list[str]), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All ContentScalerState fields are thread-scoped only.

Loop Policy:
  NONE — single-pass node. Claude generates all variants in ONE call.
  variant_count is capped at VARIANT_LIMIT to bound token usage.
  @langraph: bounded by VARIANT_LIMIT constant, not a retry loop.

Failure Discrimination:
  PERMANENT  → ValueError (missing topic, brand_voice; variant_count > VARIANT_LIMIT)
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
  UNEXPECTED → Exception

Checkpoint Semantics:
  PRE  — before Claude call
  POST — after completion, records variant count produced

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import List

import anthropic
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics
from typing import TypedDict
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

ROLE          = "content_scaler"
MAX_RETRIES   = 3
RETRY_MIN_S   = 3
RETRY_MAX_S   = 45
MAX_TOKENS    = 2000   # Generous — multiple variants can fill this fast
TOPIC_CHARS   = 1000
VOICE_CHARS   = 500
VARIANT_LIMIT = 6      # Hard ceiling — prevents token blowout

VALID_PLATFORMS = {
    "linkedin", "twitter", "facebook", "instagram",
    "email_subject", "ad_headline", "blog_intro", "sms", "general"
}


class ContentScalerState(BaseState):
    # Inputs — written by caller, immutable inside this node
    topic: str           # What the content is about
    brand_voice: str     # Tone, style, personality cues (e.g. "punchy, no jargon, results-first")
    platform: str        # linkedin | twitter | facebook | instagram | email_subject | ad_headline | blog_intro | sms | general
    variant_count: int   # How many variants to produce — capped at VARIANT_LIMIT
    cta: str             # Call to action to weave into variants (optional)
    # Outputs — written by this node
    variants: List[str]  # List of copy variants; empty list on failure


# ── Phase 1 — prompt construction (pure, no I/O) ───────────────────────────────────

def _build_variants_prompt(state: "ContentScalerState", persona: dict) -> str:
    cta_block = (
        f"\nCTA to include: {state['cta']}"
        if state.get("cta", "").strip()
        else ""
    )
    count = min(state.get("variant_count", 3), VARIANT_LIMIT)
    return f"""{persona['personality']}

Generate {count} distinct A/B copy variants for the topic below.
Each variant must test a DIFFERENT angle: different hook, different emotional trigger, different format.
Platform: {state['platform'].upper()}. Match the platform's character limits and style conventions.{cta_block}
Output ONLY the variants, one per numbered block. No commentary, no labels like "Variant 1:".

━━━ TOPIC ━━━
{state['topic'][:TOPIC_CHARS]}

━━━ BRAND VOICE ━━━
{state['brand_voice'][:VOICE_CHARS]}

━━━ FORMAT ━━━
Return exactly {count} variants.
Separate each variant with this exact delimiter on its own line:
---VARIANT---

Start immediately with the first variant."""


def _parse_variants(raw: str) -> list[str]:
    """Split Claude's output on the delimiter. Pure function — no I/O."""
    parts = [v.strip() for v in raw.split("---VARIANT---") if v.strip()]
    return parts


_build_prompt = _build_variants_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2 — Claude call (TRANSIENT errors retried) ────────────────────────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def content_scaler_node(state: ContentScalerState) -> dict:
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    topic        = state.get("topic", "")
    platform     = state.get("platform", "general")
    variant_count = state.get("variant_count", 3)
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()
    metrics      = CallMetrics(thread_id, ROLE)

    def _checkpoint(cid: str, payload: dict) -> None:
        state_logger.log_state(thread_id, cid, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, platform=platform,
             variant_count=variant_count)

    try:
        if not topic.strip():
            raise ValueError("topic is required.")
        if not state.get("brand_voice", "").strip():
            raise ValueError("brand_voice is required — describe tone, style, and personality.")
        if variant_count > VARIANT_LIMIT:
            raise ValueError(
                f"variant_count {variant_count} exceeds VARIANT_LIMIT={VARIANT_LIMIT}. "
                "Reduce variant_count."
            )
        if platform not in VALID_PLATFORMS:
            raise ValueError(
                f"Invalid platform '{platform}'. "
                f"Must be one of: {', '.join(sorted(VALID_PLATFORMS))}"
            )

        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_variants_prompt(state, persona)

        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"topic": topic[:80], "platform": platform,
             "variant_count": variant_count, "status": "generating"},
        )

        raw      = _generate(claude, prompt, metrics)
        variants = _parse_variants(raw)
        metrics.log()
        metrics.persist()

        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"platform": platform, "status": "completed",
             "variants_produced": len(variants)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, variants_produced=len(variants))
        return {"variants": variants, "error": None, "workflow_id": thread_id, "agent": ROLE}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", error=msg)
        notifier.agent_error(ROLE, topic[:80], msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"topic": topic[:80], "status": "permanent_failure", "error": msg})
        return {"variants": [], "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", error=msg)
        notifier.agent_error(ROLE, topic[:80], msg)
        return {"variants": [], "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", error=msg)
        notifier.agent_error(ROLE, topic[:80], msg)
        return {"variants": [], "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(ContentScalerState)
    g.add_node("content_scaler", content_scaler_node)
    g.set_entry_point("content_scaler")
    g.add_edge("content_scaler", END)
    return g.compile()
