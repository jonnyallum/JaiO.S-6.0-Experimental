"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : video_brief_writer
SKILL : Video Brief Writer — produce a director-ready short-form video brief with hook,
        scene-by-scene script, b-roll shot list, caption, and thumbnail concept

Node Contract (@langraph doctrine):
  Inputs   : topic (str), platform (str), duration_seconds (int), hook_style (str),
             cta (str), brand_context (str) — immutable after entry
  Outputs  : video_brief (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]
             Telemetry: CallMetrics per invocation — tokens, cost_usd, latency_ms [non-fatal]

Thread Memory (checkpoint-scoped):
  All VideoBriefState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.
  DURATION_LIMIT enforced at input — hard cap, never inside a generation loop.
  This node handles short-form only. Long-form content requires a different agent.

Failure Discrimination:
  PERMANENT  → ValueError (topic missing, invalid platform/hook_style, duration > DURATION_LIMIT)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (records platform, duration, hook_style)
  POST — Supabase log after completion (records brief size)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

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

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE            = "video_brief_writer"
MAX_RETRIES     = 3
RETRY_MIN_S     = 3
RETRY_MAX_S     = 45
MAX_TOKENS      = 1800
TOPIC_CHARS     = 500
CONTEXT_CHARS   = 400
CTA_CHARS       = 200
DURATION_LIMIT  = 180     # Seconds — short-form only; max 3 min
WORDS_PER_SEC   = 2.5     # Average spoken words per second for pacing calculation

VALID_PLATFORMS = {
    "tiktok", "reels", "youtube_shorts", "linkedin", "twitter", "general",
}

VALID_HOOK_STYLES = {
    "question",     # Open with a knowledge-gap question
    "shock",        # Bold counterintuitive statement in first 2 seconds
    "story",        # Start mid-story — in medias res
    "stat",         # Lead with a specific, surprising statistic
    "before_after", # "I used to X... now I Y"
    "direct",       # Direct address — "If you [condition], watch this"
}

# Platform-specific production constraints
_PLATFORM_SPECS = {
    "tiktok":         {"aspect": "9:16", "caption_limit": 2200, "hashtags": "3-5",  "energy": "high"},
    "reels":          {"aspect": "9:16", "caption_limit": 2200, "hashtags": "5-10", "energy": "high"},
    "youtube_shorts": {"aspect": "9:16", "caption_limit": 100,  "hashtags": "3-5",  "energy": "medium"},
    "linkedin":       {"aspect": "1:1 or 9:16", "caption_limit": 700, "hashtags": "3-5", "energy": "professional"},
    "twitter":        {"aspect": "16:9 or 1:1", "caption_limit": 280, "hashtags": "1-2", "energy": "punchy"},
    "general":        {"aspect": "9:16", "caption_limit": 500,  "hashtags": "3-5",  "energy": "medium"},
}


# ── State schema ─────────────────────────────────────────────────────────────────
class VideoBriefState(BaseState):
    # Inputs — written by caller, immutable inside this node
    topic: str              # Core subject of the video
    platform: str           # tiktok | reels | youtube_shorts | linkedin | twitter | general
    duration_seconds: int   # Target video duration — hard-capped at DURATION_LIMIT
    hook_style: str         # question | shock | story | stat | before_after | direct
    cta: str                # Call to action — what the viewer should do after watching
    brand_context: str      # Brand voice, product, audience context
    # Outputs — written by this node, read by downstream nodes
    video_brief: str        # Director-ready brief with all sections; empty on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: Structure planning (pure, independently testable) ────────────────────
def _plan_structure(duration: int, platform: str) -> dict:
    """
    Compute scene structure from duration and platform energy. Pure function — no I/O.
    Returns timing breakdown per scene section.
    Separation allows unit testing without mocking Claude.
    """
    specs        = _PLATFORM_SPECS.get(platform, _PLATFORM_SPECS["general"])
    target_words = int(duration * WORDS_PER_SEC)

    if duration <= 15:
        scenes = [
            ("Hook",       2,              "Scroll-stopping open"),
            ("Core value", duration - 4,   "One idea only"),
            ("CTA",        2,              "What to do now"),
        ]
    elif duration <= 60:
        hook_s = 3 if specs["energy"] == "high" else 5
        cta_s  = 3
        mid_s  = duration - hook_s - cta_s
        scenes = [
            ("Hook",     hook_s,           "Pattern interrupt open"),
            ("Problem",  int(mid_s * 0.3), "Establish the pain/gap"),
            ("Solution", int(mid_s * 0.5), "Core insight delivery"),
            ("Proof",    int(mid_s * 0.2), "Quick evidence or example"),
            ("CTA",      cta_s,            "Single clear action"),
        ]
    else:  # 61–180s
        hook_s = 5
        cta_s  = 5
        mid_s  = duration - hook_s - cta_s
        scenes = [
            ("Hook",     hook_s,            "Strong open — audience commits to watching"),
            ("Problem",  int(mid_s * 0.25), "Deepen problem and stakes"),
            ("Context",  int(mid_s * 0.15), "Why this matters now"),
            ("Solution", int(mid_s * 0.35), "Core teaching point"),
            ("Proof",    int(mid_s * 0.15), "Example, case study, or stat"),
            ("Summary",  int(mid_s * 0.10), "One-line recap"),
            ("CTA",      cta_s,             "What to do next"),
        ]

    return {"scenes": scenes, "target_words": target_words, "specs": specs}


# ── Phase 2: Brief writing (Claude call, retried on transient errors only) ────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _write_brief(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def _build_prompt(state: "VideoBriefState", structure: dict, persona: dict) -> str:
    """Format structure and context into a video brief writing prompt. Pure function — no I/O."""
    specs      = structure["specs"]
    scene_plan = "\n".join(
        f"  {name} ({dur}s): {desc}" for name, dur, desc in structure["scenes"]
    )

    return f"""{persona['personality']}

Write a complete, director-ready video brief. Every section must be production-actionable.
No vague direction. Hook style: {state['hook_style'].upper().replace('_', ' ')}.

━━━ VIDEO SPEC ━━━
Platform     : {state['platform'].upper()}
Duration     : {state['duration_seconds']}s (~{structure['target_words']} spoken words)
Aspect ratio : {specs['aspect']}
Energy level : {specs['energy']}
Hook style   : {state['hook_style'].replace('_', ' ')}
CTA          : {state.get('cta', 'Follow for more')[:CTA_CHARS]}

━━━ TOPIC ━━━
{state['topic'][:TOPIC_CHARS]}

━━━ BRAND CONTEXT ━━━
{state.get('brand_context', '')[:CONTEXT_CHARS]}

━━━ SCENE TIMING PLAN ━━━
{scene_plan}

━━━ OUTPUT FORMAT ━━━
## Video Brief: {state['topic'][:60]}
**Platform:** {state['platform']} | **Duration:** {state['duration_seconds']}s

### Hook (first spoken line — exact words)
[This IS the {state['hook_style'].replace('_', ' ')} hook. Must stop the scroll in 1-2 seconds.
Write the exact line the creator says on camera.]

### Script (scene by scene)
For each scene use this format:
**[SCENE NAME - Xs]**
Spoken: "Exact words here."
Visual: [Shot description, cuts, camera direction]

### B-Roll Shot List
[Specific footage to cut between talking head moments — numbered, actionable]

### Caption
[Post-ready caption, max {specs['caption_limit']} chars. Restate hook + CTA + hashtags ({specs['hashtags']} tags)]

### Thumbnail Concept
[Text overlay, facial expression, background, colour contrast — specific enough to design]

### Director Notes
[Pacing, delivery, energy specific to {state['platform']} and {state['hook_style'].replace('_',' ')} hook style]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def video_brief_writer_node(state: VideoBriefState) -> dict:
    """
    Video Brief Writer node — single pass, no loop.

    Execution order:
      1. Validate inputs (topic required; platform/hook_style valid; duration <= DURATION_LIMIT)
      2. Plan scene structure (Phase 1 — pure, no Claude)
      3. PRE checkpoint (before Claude call)
      4. Write brief (Phase 2 — Claude)
      5. metrics.log() + metrics.persist() [non-fatal]
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: DURATION_LIMIT enforced at input — never inside a generation loop.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    topic        = state.get("topic", "")
    platform     = state.get("platform", "general")
    duration     = state.get("duration_seconds", 60)
    hook         = state.get("hook_style", "direct")
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, platform=platform,
             duration=duration, hook=hook)

    try:
        # Input guards — PERMANENT failures
        if not topic.strip():
            raise ValueError("topic is required and cannot be empty.")
        if platform not in VALID_PLATFORMS:
            raise ValueError(
                f"Invalid platform '{platform}'. Must be one of: {', '.join(sorted(VALID_PLATFORMS))}"
            )
        if hook not in VALID_HOOK_STYLES:
            raise ValueError(
                f"Invalid hook_style '{hook}'. Must be one of: {', '.join(sorted(VALID_HOOK_STYLES))}"
            )
        if not (1 <= duration <= DURATION_LIMIT):
            raise ValueError(
                f"duration_seconds must be 1-{DURATION_LIMIT} (got {duration}). "
                f"This node handles short-form only. Use a different agent for longer content."
            )

        claude  = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics = CallMetrics(thread_id, ROLE)

        # Phase 1 — plan scene structure (pure)
        structure = _plan_structure(duration, platform)
        log.info(f"{ROLE}.structured", scenes=len(structure["scenes"]),
                 target_words=structure["target_words"])

        # PRE checkpoint — mark brief writing started
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"platform": platform, "duration": duration, "hook": hook,
             "status": "writing", "scenes_planned": len(structure["scenes"])},
        )

        # Phase 2 — write brief (TRANSIENT failures retried by tenacity)
        prompt      = _build_prompt(state, structure, persona)
        video_brief = _write_brief(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"platform": platform, "duration": duration, "status": "completed",
             "brief_chars": len(video_brief)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, brief_chars=len(video_brief))
        return {"video_brief": video_brief, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input",
                  error=msg, platform=platform)
        notifier.agent_error(ROLE, platform, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"platform": platform, "status": "permanent_failure", "error": msg})
        return {"video_brief": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, platform, msg)
        return {"video_brief": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, platform, msg)
        return {"video_brief": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(VideoBriefState)
    g.add_node("video_brief_writer", video_brief_writer_node)
    g.set_entry_point("video_brief_writer")
    g.add_edge("video_brief_writer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
