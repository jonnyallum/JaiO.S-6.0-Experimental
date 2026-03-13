"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : creative_director
SKILL : Creative Director

Creative Director - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), brand_context (str), output_type (VALID_OUTPUT_TYPES), medium (VALID_MEDIUMS)
    Outputs: creative_brief (str), direction_notes (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/medium → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before creative brief generation
    POST - creative_brief + direction_notes persisted after successful generation
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

ROLE        = "creative_director"
MAX_RETRIES = 3
MAX_TOKENS  = 2600

VALID_OUTPUT_TYPES = {
    "creative_brief", "art_direction", "visual_concept", "campaign_concept",
    "brand_story", "design_critique", "moodboard_spec", "general",
}
VALID_MEDIUMS = {
    "digital", "video", "social", "print", "product", "experiential", "general",
}

# ── Creative Framework Library ─────────────────────────────────────────────────
_CREATIVE_FRAMEWORKS = {
    "problem_solution":  "Lead with the pain. Make it visceral. Then reveal the answer.",
    "aspiration":        "Show the world as it could be - then position the brand as the bridge.",
    "contrast":          "Before / After. Dark / Light. Complicated / Simple. Visual tension.",
    "human_truth":       "Find the universal feeling. Make people feel seen before selling.",
    "provocation":       "Start with a question or statement that challenges the status quo.",
    "intimacy":          "One person speaking to one person - scale authenticity.",
}

_VISUAL_DIRECTIONS = {
    "digital": {
        "aspect_ratios":  ["16:9 (hero)", "1:1 (feed)", "9:16 (stories/reels)", "1.91:1 (OG)"],
        "motion":         "Motion default - static is the exception online",
        "typography":     "Variable fonts for responsive sizing, min 16px body",
        "colour":         "sRGB for web, P3 for OLED/Retina displays",
        "file_formats":   "WebP/AVIF for images, WEBM/MP4 for video, SVG for icons",
    },
    "video": {
        "aspect_ratios":  ["16:9 (YouTube/broadcast)", "9:16 (TikTok/Reels)", "1:1 (feed ads)"],
        "motion":         "Hook in 0–3s. Value in 3–15s. CTA at end AND middle.",
        "typography":     "Supertitle within safe zones - bottom 10% is often cropped",
        "colour":         "Rec. 709 for broadcast, HDR10 for streaming platforms",
        "file_formats":   "H.264 for compatibility, H.265/HEVC for quality/size",
    },
    "social": {
        "aspect_ratios":  ["1:1 (universal)", "4:5 (feed priority)", "9:16 (stories)"],
        "motion":         "First frame must work as static - autoplay is silent",
        "typography":     "Large, readable at thumbnail size - no paragraph text",
        "colour":         "High contrast - competes in noisy feed",
        "file_formats":   "JPG/PNG for static, MP4 <15MB for video",
    },
    "general": {
        "aspect_ratios":  ["Context-dependent"],
        "motion":         "Consider context before adding motion",
        "typography":     "Readable, brand-consistent",
        "colour":         "Brand palette primary",
        "file_formats":   "Platform-appropriate",
    },
}

_QUALITY_GATES = [
    "Does it work in black and white? (If not, colour is doing too much work)",
    "Can you explain the concept in one sentence?",
    "Does it pass the 3-second rule? (Thumb stops scrolling)",
    "Is there one clear focal point?",
    "Would the target audience recognise themselves in this?",
    "Does it feel fresh or does it look like a template?",
    "Is the brand unmistakable without seeing the logo?",
]


class CreativeDirectorState(BaseState):
    workflow_id:      str
    timestamp:        str
    agent:            str
    error:            str | None
    task:             str
    brand_context:    str
    output_type:      str
    medium:           str
    creative_brief:   str
    direction_notes:  str


# ── Phase 1 - Creative Analysis (pure, no Claude) ─────────────────────────────
def _analyse_creative_brief(task: str, medium: str) -> dict:
    """Returns creative_data dict - pure lookup, no Claude."""
    vis_dir    = _VISUAL_DIRECTIONS.get(medium, _VISUAL_DIRECTIONS["general"])
    task_lower = task.lower()
    flags: list[str] = []

    if "brand" in task_lower:
        flags.append("Brand work - define single-minded proposition before any visual")
    if "campaign" in task_lower:
        flags.append("Campaign - needs idea that travels across all touchpoints")
    if "launch" in task_lower:
        flags.append("Launch - hero moment + teaser + reveal arc needed")
    if "video" in task_lower or "reel" in task_lower:
        flags.append("Video - hook at 0–3s is non-negotiable, test first frame as static")
    if "social" in task_lower:
        flags.append("Social - thumb-stop is the only metric that matters at top of funnel")

    # Pick most relevant framework
    framework_key = "human_truth"
    if "launch" in task_lower:
        framework_key = "provocation"
    elif "problem" in task_lower or "pain" in task_lower:
        framework_key = "problem_solution"
    elif "aspirat" in task_lower or "lifestyle" in task_lower:
        framework_key = "aspiration"

    return {
        "visual_direction":  vis_dir,
        "framework":         _CREATIVE_FRAMEWORKS[framework_key],
        "framework_name":    framework_key,
        "quality_gates":     _QUALITY_GATES,
        "flags":             flags,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Creative Brief ───────────────────────────────────────────
def _build_creative_prompt(state: CreativeDirectorState, creative_data: dict) -> str:
    persona    = get_persona(ROLE)
    task       = state["task"]
    brand_ctx  = state.get("brand_context", "")
    out_type   = state.get("output_type", "creative_brief")
    medium     = state.get("medium", "general")
    vis_dir    = creative_data["visual_direction"]

    flags_text  = "\n".join(f"  ⚡ {f}" for f in creative_data["flags"]) or "  None detected"
    gates_text  = "\n".join(f"  ☐ {g}" for g in creative_data["quality_gates"])
    ar_text     = " | ".join(vis_dir["aspect_ratios"])

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Direct a world-class {out_type} for medium: {medium}.

CREATIVE FRAMEWORK: {creative_data['framework_name'].replace('_',' ').title()}
"{creative_data['framework']}"

VISUAL DIRECTION - {medium}:
  Aspect Ratios: {ar_text}
  Motion:        {vis_dir['motion']}
  Typography:    {vis_dir['typography']}
  Colour Space:  {vis_dir['colour']}
  File Formats:  {vis_dir['file_formats']}

CREATIVE FLAGS:
{flags_text}

QUALITY GATES (must pass all):
{gates_text}

TASK:
{task}

BRAND CONTEXT:
{brand_ctx or "None provided - infer from task and create strong defaults"}

OUTPUT FORMAT:
## Creative Direction: {out_type.replace('_',' ').title()} - {medium}

### The Single-Minded Proposition
[One sentence. The one thing this work must communicate.]

### The Big Idea
[The creative concept - 2–3 sentences. What makes it memorable?]

### Visual Concept
[Describe the visual world - colours, textures, photography style, typography mood]

### Art Direction Notes
[Specific direction: lighting, composition, talent, props, environment]

### Copy Direction
[Tone, voice, headline approach, word-count guidelines]

### Execution Across Formats
| Format | Key Adaptation | First Frame / Hero Element |
|---|---|---|
[rows per format]

### Quality Gate Sign-off
[Each gate: PASS / FAIL / N/A - with reasoning]

### Reference / Inspiration
[3 specific references - brand, era, or creator - with what to borrow]

### Next Action
[Single most important first step]
"""

_build_prompt = _build_creative_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception(_is_transient), reraise=True)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(model="claude-opus-4-6", max_tokens=MAX_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    metrics.record(response); metrics.log(); metrics.persist()
    return response.content[0].text


def creative_director_node(state: CreativeDirectorState) -> CreativeDirectorState:
    thread_id  = state.get("workflow_id", "local")
    task       = state.get("task", "").strip()
    out_type   = state.get("output_type", "creative_brief")
    medium     = state.get("medium", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if medium not in VALID_MEDIUMS:
        raise ValueError(f"PERMANENT: medium '{medium}' not in {VALID_MEDIUMS}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "medium": medium})
    creative_data = _analyse_creative_brief(task, medium)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_creative_prompt(state, creative_data)

    try:
        brief = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    direction_match = re.search(r'### Art Direction Notes([\s\S]+?)(?=###|$)', brief)
    direction_notes = direction_match.group(1).strip() if direction_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "medium": medium})

    return {**state, "agent": ROLE, "creative_brief": brief, "direction_notes": direction_notes, "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(CreativeDirectorState)
    g.add_node("creative_director", creative_director_node)
    g.set_entry_point("creative_director")
    g.add_edge("creative_director", END)
    return g.compile()
