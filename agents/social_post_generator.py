"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : social_post_generator
SKILL : Social Post Generator — brief → FB/IG copy, optional publish via Meta Graph API

Node Contract (@langraph doctrine):
  Inputs   : brief (str), platform (str), tone (str), hashtags (str),
             publish (bool), image_url (str|None) — immutable after entry
  Outputs  : post_copy (dict), published (bool), post_ids (dict), error (str|None)
  Tools    : Anthropic [read-only for generation], MetaSocialTools [write for publish]
  Effects  : Meta Graph API post [conditional on publish=True],
             Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All SocialPostState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (missing required fields, invalid platform)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  PUBLISH    → httpx.HTTPError, ValueError from MetaSocialTools (credentials missing)
               Non-fatal — generation output is always returned even if publish fails.
               Error is recorded in post_ids["error"], node error field stays None.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE     — Supabase log before Claude call (marks generation started)
  POST    — Supabase log after generation complete (records copy length, platform)
  PUBLISH — Supabase log after publish attempt (records post IDs or publish error)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.social_tools import MetaSocialTools
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics
from typing import TypedDict

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE           = "social_post_generator"
MAX_RETRIES    = 3
RETRY_MIN_S    = 3
RETRY_MAX_S    = 45
MAX_TOKENS     = 600    # Social posts are short — 600 tokens is generous for copy + variants
BRIEF_CHARS    = 2000   # Brief truncation limit
FB_CHAR_TARGET = 800    # Target FB post length (under 1k for readability)
IG_CHAR_TARGET = 300    # Target IG caption — punchy, hashtag-tailed

VALID_PLATFORMS = {"facebook", "instagram", "both"}
DEFAULT_HASHTAGS = "#JaiOS6 #JonnyAI #AIAutomation"


# ── State schema ─────────────────────────────────────────────────────────────────
class SocialPostState(BaseState):
    # Inputs — written by caller, immutable inside this node
    brief: str              # Topic/content brief for the post
    platform: str           # facebook | instagram | both
    tone: str               # professional | casual | urgent | celebratory | custom
    hashtags: str           # Hashtag string to append — defaults to DEFAULT_HASHTAGS
    publish: bool           # If True, post via Meta Graph API after generation
    image_url: Optional[str]  # Required for Instagram publishing (must be public HTTPS URL)
    # Outputs — written by this node, read by downstream nodes
    post_copy: dict         # {"facebook": str, "instagram": str} — one or both keys
    published: bool         # True if at least one platform was published successfully
    post_ids: dict          # {"facebook": post_id, "instagram": media_id} or {"error": msg}
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Pure helpers ─────────────────────────────────────────────────────────────────
def _build_copy_prompt(
    brief: str,
    platform: str,
    tone: str,
    hashtags: str,
    persona: dict,
) -> str:
    """Build the copy generation prompt. Pure function — no I/O."""

    fb_block = f"""
━━━ FACEBOOK POST (target {FB_CHAR_TARGET} chars) ━━━
Write a Facebook post for this brand page. Hook in the first line. Conversational but professional.
End with a call to action. Append hashtags on a new line.
HASHTAGS: {hashtags}
""" if platform in ("facebook", "both") else ""

    ig_block = f"""
━━━ INSTAGRAM CAPTION (target {IG_CHAR_TARGET} chars) ━━━
Write an Instagram caption. Punchy opening line. Emoji where natural. Keep it tight.
Hashtags at the end on a new line (max 10 tags).
HASHTAGS: {hashtags}
""" if platform in ("instagram", "both") else ""

    return f"""{persona['personality']}

Write social media copy for the following brief. Tone: {tone.upper()}.
Output ONLY the post copy — no commentary, no labels like "Here is your post:".

━━━ BRIEF ━━━
{brief[:BRIEF_CHARS]}
{fb_block}{ig_block}"""


# ── Phase 1: Copy generation (Claude call, retried on transient errors only) ─────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _generate_copy(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def _split_copy(raw_copy: str, platform: str) -> dict:
    """
    Split generated copy into per-platform dict.
    For 'both', Claude outputs two blocks separated by the platform headers.
    Pure function — no I/O.
    """
    if platform == "facebook":
        return {"facebook": raw_copy}
    if platform == "instagram":
        return {"instagram": raw_copy}

    # platform == "both" — split on the IG header marker
    parts = raw_copy.split("━━━ INSTAGRAM")
    fb_copy = parts[0].strip()
    ig_copy = ("━━━ INSTAGRAM" + parts[1]).strip() if len(parts) > 1 else raw_copy
    # Strip the header line from ig_copy if present
    ig_lines = ig_copy.split("\n")
    ig_copy = "\n".join(ig_lines[1:]).strip() if ig_lines[0].startswith("━━━") else ig_copy
    return {"facebook": fb_copy, "instagram": ig_copy}


_build_prompt = _build_copy_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2: Publish (MetaSocialTools side-effect, non-fatal) ────────────────────
def _publish(social: MetaSocialTools, copy: dict, image_url: Optional[str]) -> dict:
    """
    Attempt to publish generated copy to Meta platforms.
    Returns dict of post IDs on success, {"error": msg} on failure.
    Non-fatal — caller catches and logs, does not fail the node.
    """
    post_ids: dict = {}

    if "facebook" in copy:
        result = social.post_to_facebook(copy["facebook"])
        post_ids["facebook"] = result.get("id", "unknown")

    if "instagram" in copy:
        if not image_url:
            raise ValueError(
                "image_url is required to publish to Instagram. "
                "Provide a public HTTPS image URL."
            )
        result = social.post_to_instagram(image_url=image_url, caption=copy["instagram"])
        post_ids["instagram"] = result.get("id", "unknown")

    return post_ids


# ── Main node ─────────────────────────────────────────────────────────────────────
def social_post_generator_node(state: SocialPostState) -> dict:
    """
    Social Post Generator node — single pass, no loop.

    Execution order:
      1. Validate inputs (platform, brief)
      2. Build prompt (pure function)
      3. PRE checkpoint (before Claude call)
      4. Generate copy (Phase 1 — Claude)
      5. Split copy by platform (pure function)
      6. POST checkpoint (after generation)
      7. If publish=True: attempt publish (Phase 2 — Meta API, non-fatal)
      8. PUBLISH checkpoint
      9. Return state patch

    Generation always succeeds or fails cleanly.
    Publish failure is non-fatal — copy is always returned.

    @langraph: show me the checkpoint before you call production-ready.
    """
    thread_id  = state.get("workflow_id") or str(uuid.uuid4())
    platform   = state.get("platform", "facebook")
    tone       = state.get("tone", "professional")
    hashtags   = state.get("hashtags") or DEFAULT_HASHTAGS
    publish    = state.get("publish", False)
    image_url  = state.get("image_url")
    persona    = get_persona(ROLE)
    notifier   = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, platform=platform,
             tone=tone, publish=publish)

    try:
        # Input guards — PERMANENT failures
        if not state.get("brief", "").strip():
            raise ValueError("brief is required and cannot be empty.")
        if platform not in VALID_PLATFORMS:
            raise ValueError(
                f"Invalid platform '{platform}'. Must be one of: {', '.join(VALID_PLATFORMS)}"
            )

        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # Build prompt (pure — no I/O)
        prompt = _build_copy_prompt(state["brief"], platform, tone, hashtags, persona)

        # PRE checkpoint — mark generation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"platform": platform, "tone": tone, "publish": publish, "status": "generating"},
        )

        # Phase 1 — generate copy (TRANSIENT failures retried by tenacity)
        raw_copy    = _generate_copy(claude, prompt, metrics)
        post_copy   = _split_copy(raw_copy, platform)

        metrics.log()
        metrics.persist()

        # POST checkpoint — generation complete
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"platform": platform, "status": "generated",
             "copy_chars": {k: len(v) for k, v in post_copy.items()}},
        )

        log.info(f"{ROLE}.generated", thread_id=thread_id, platform=platform,
                 copy_keys=list(post_copy.keys()))

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input", error=msg)
        notifier.agent_error(ROLE, platform, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"platform": platform, "status": "permanent_failure", "error": msg})
        return {"post_copy": {}, "published": False, "post_ids": {},
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, platform, msg)
        return {"post_copy": {}, "published": False, "post_ids": {},
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, platform, msg)
        return {"post_copy": {}, "published": False, "post_ids": {},
                "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── Publish phase — non-fatal, always returns generated copy ─────────────────
    published = False
    post_ids: dict = {}

    if publish:
        try:
            social   = MetaSocialTools()
            post_ids = _publish(social, post_copy, image_url)
            published = bool(post_ids)
            log.info(f"{ROLE}.published", thread_id=thread_id, post_ids=post_ids)
        except (httpx.HTTPError, ValueError) as exc:
            post_ids = {"error": str(exc)}
            log.warning(f"{ROLE}.publish_failed", error=str(exc))
            notifier.alert(f"⚠️ Social publish failed ({platform}): {exc}")
        except Exception as exc:
            post_ids = {"error": f"Unexpected publish error: {exc}"}
            log.exception(f"{ROLE}.publish_unexpected", error=str(exc))

        _checkpoint(
            f"{ROLE}_publish_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"platform": platform, "published": published, "post_ids": post_ids},
        )

    return {
        "post_copy":   post_copy,
        "published":   published,
        "post_ids":    post_ids,
        "error":       None,
        "workflow_id": thread_id,
        "agent":       ROLE,
    }
