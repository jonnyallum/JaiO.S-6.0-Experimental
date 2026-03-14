"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : course_designer
 SKILL : Course Designer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : course_title (str), target_student (str),
               transformation (str — what student can do after),
               delivery_format (str), num_modules (int),
               expertise_level (str)
 Output keys : curriculum (str), module_count (int)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Loop Policy
 ───────────
 No iterative loops. Single-pass: Phase 1 structure computation →
 Phase 2 Claude curriculum. MODULE_LIMIT = 16 (max modules per course).

 Failure Discrimination
 ──────────────────────
 PERMANENT  — invalid delivery_format/expertise_level (ValueError),
               empty course_title or transformation,
               num_modules < 3 or > MODULE_LIMIT
 TRANSIENT  — Anthropic 529/overload, network timeout on Claude call
 UNEXPECTED — any other unhandled exception

 Checkpoint Semantics
 ────────────────────
 PRE  — logged before Claude call: delivery_format, expertise_level,
        num_modules, estimated_hours
 POST — logged after success: curriculum char count, module_count

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

ROLE = "course_designer"

# ── Budget constants ───────────────────────────────────────────────────────────
MAX_RETRIES  = 3
MAX_TOKENS   = 2600   # full curriculum needs depth
MODULE_LIMIT = 16     # max modules — above this, split into two courses

# ── Validation sets ────────────────────────────────────────────────────────────
VALID_DELIVERY_FORMATS = {
    "self_paced_video", "live_cohort", "hybrid", "text_based",
    "audio_podcast_course", "workshop", "bootcamp", "general"
}
VALID_EXPERTISE_LEVELS = {"beginner", "intermediate", "advanced", "mixed"}

# ── Delivery format specs ──────────────────────────────────────────────────────
_FORMAT_SPECS: dict[str, dict] = {
    "self_paced_video": {
        "lesson_length":   "8–15 min per video",
        "lesson_types":    "Video lecture + PDF cheatsheet + quiz + project",
        "pacing":          "Student sets own pace — no deadlines",
        "engagement_hook": "Pattern interrupt every 3–4 mins; cliffhanger at end of module",
    },
    "live_cohort": {
        "lesson_length":   "60–90 min live sessions, 2–3x per week",
        "lesson_types":    "Live workshop + replay + async exercises + community",
        "pacing":          "Cohort-driven — everyone moves together",
        "engagement_hook": "Hot seat coaching, peer accountability, live Q&A",
    },
    "hybrid": {
        "lesson_length":   "Pre-recorded: 10–20 min; Live: weekly 60-min call",
        "lesson_types":    "Video + live implementation session + peer feedback",
        "pacing":          "Weekly cadence with async content between live calls",
        "engagement_hook": "Live call is the accountability and Q&A anchor",
    },
    "text_based": {
        "lesson_length":   "1,500–3,000 words per lesson",
        "lesson_types":    "Long-form article + exercises + templates + quiz",
        "pacing":          "Self-paced with suggested reading schedule",
        "engagement_hook": "Real-world examples, case studies, interactive exercises",
    },
    "audio_podcast_course": {
        "lesson_length":   "20–40 min per episode",
        "lesson_types":    "Audio lesson + transcript + action worksheet",
        "pacing":          "Weekly release or binge-friendly",
        "engagement_hook": "Interview experts, tell stories, pose questions mid-episode",
    },
    "workshop": {
        "lesson_length":   "Half-day (4h) or full-day (7h) intensive",
        "lesson_types":    "Teaching blocks + hands-on exercises + group work",
        "pacing":          "Single or multi-day intensive",
        "engagement_hook": "Do-the-work-now format — leave with an artifact",
    },
    "bootcamp": {
        "lesson_length":   "4–8h per day over 5–12 days",
        "lesson_types":    "Immersive teaching + project work + mentorship",
        "pacing":          "High-intensity, consecutive days",
        "engagement_hook": "Daily standups, project demos, peer pressure (positive)",
    },
    "general": {
        "lesson_length":   "15–20 min per lesson",
        "lesson_types":    "Lesson + exercise + reflection",
        "pacing":          "Self-paced with suggested weekly schedule",
        "engagement_hook": "Clear wins at the end of each module",
    },
}

# ── Expertise level calibration ────────────────────────────────────────────────
_LEVEL_NOTES: dict[str, str] = {
    "beginner":     "Assume zero prior knowledge. Define every term. Build confidence through quick wins in Module 1.",
    "intermediate": "Assume core concepts known. Skip basics. Focus on nuance, common mistakes, and advanced patterns.",
    "advanced":     "Assume deep expertise. Focus on edge cases, optimisation, and expert-level mental models.",
    "mixed":        "Layer content: core concept first, then intermediate application, then advanced extension. Label each layer.",
}

# ── State ──────────────────────────────────────────────────────────────────────
class CourseState(BaseState):
    # Inputs
    course_title:    str   # name of the course
    target_student:  str   # who this course is for
    transformation:  str   # what students can DO after completing
    delivery_format: str   # how the course is delivered
    num_modules:     int   # number of modules
    expertise_level: str   # student expertise level
    thread_id:       str   # conversation thread ID (owner: supervisor)

    # Computed (Phase 1)
    format_spec:     dict  # delivery format specifications (owner: this node)
    level_note:      str   # expertise calibration note (owner: this node)
    estimated_hours: float # estimated total learning hours (owner: this node)

    # Outputs
    curriculum:   str   # full course curriculum (owner: this node)
    module_count: int   # confirmed module count (owner: this node)
    error:        str   # failure reason if any (owner: this node)


# ── Phase 1 — pure structure computation (no Claude) ─────────────────────────

def _compute_structure(delivery_format: str, expertise_level: str, num_modules: int) -> tuple[dict, str, float]:
    """
    Phase 1 — pure lookup and computation. Returns (format_spec, level_note, estimated_hours).
    No Claude, no I/O — independently testable.
    """
    fmt_spec   = _FORMAT_SPECS.get(delivery_format, _FORMAT_SPECS["general"])
    level_note = _LEVEL_NOTES[expertise_level]

    # Estimate total hours based on format and module count
    lesson_mins = {
        "self_paced_video": 12, "live_cohort": 75, "hybrid": 50,
        "text_based": 25, "audio_podcast_course": 30, "workshop": 210,
        "bootcamp": 360, "general": 20,
    }
    mins_per_lesson  = lesson_mins.get(delivery_format, 20)
    lessons_per_mod  = 4   # average
    total_mins       = num_modules * lessons_per_mod * mins_per_lesson
    estimated_hours  = round(total_mins / 60, 1)

    return fmt_spec, level_note, estimated_hours


# ── Phase 2 — prompt construction + Claude call ───────────────────────────────

def _build_prompt(
    course_title: str,
    target_student: str,
    transformation: str,
    delivery_format: str,
    num_modules: int,
    expertise_level: str,
    fmt_spec: dict,
    level_note: str,
    estimated_hours: float,
) -> str:
    """Pure function — assembles the curriculum brief from Phase 1 outputs."""
    persona    = get_persona(ROLE)
    format_str = "\n".join(f"  {k}: {v}" for k, v in fmt_spec.items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} curriculum architect.

Course title     : {course_title}
Target student   : {target_student}
Transformation   : After completing this course, the student will be able to: {transformation}
Delivery format  : {delivery_format.replace('_', ' ')}
Modules          : {num_modules}
Expertise level  : {expertise_level} — {level_note}
Est. total hours : ~{estimated_hours}h

Format specifications:
{format_str}

Design the complete course curriculum:

1. COURSE OVERVIEW
   - One-sentence promise (the transformation in plain language)
   - Who this is NOT for (just as important as who it IS for)
   - Prerequisites (honest list)
   - What they'll walk away with (3 bullet tangible outcomes)

2. MODULE BREAKDOWN (exactly {num_modules} modules)
   For each module:
   MODULE [N]: [Title]
   Learning objective: By the end of this module, students will [specific skill/knowledge]
   Lessons:
     - [Lesson 1 title] ({fmt_spec.get('lesson_length', '15 min')})
     - [Lesson 2 title]
     - [Lesson 3 title]
     - [Lesson 4 title]
   Module project/exercise: [specific hands-on task they complete]
   Module win: [the mini-transformation they get at the end of this module]

3. ASSESSMENT STRATEGY
   - How students demonstrate mastery (quiz / project / peer review / coach review)
   - Completion certificate criteria

4. QUICK WIN MODULE
   Which module should come first to give students a win within 30 minutes? (May reorder)

Rules:
- Module 1 must deliver a result within the first lesson — hook them immediately
- Every module must have a clear "win" — a tangible outcome the student has
- No padding modules — if a topic doesn't deserve a module, make it a lesson
- Titles must be outcome-based, not topic-based (e.g. "Build Your First Landing Page" not "Landing Pages")"""


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _write_curriculum(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
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

def course_designer_node(state: CourseState) -> CourseState:
    thread_id       = state.get("thread_id", "unknown")
    course_title    = state.get("course_title", "").strip()
    target_student  = state.get("target_student", "").strip()
    transformation  = state.get("transformation", "").strip()
    delivery_format = state.get("delivery_format", "self_paced_video").lower().strip()
    num_modules     = int(state.get("num_modules", 6))
    expertise_level = state.get("expertise_level", "beginner").lower().strip()

    # ── Input validation (PERMANENT failures) ─────────────────────────────────
    if not course_title:
        return {**state, "error": "PERMANENT: course_title is required"}
    if not transformation:
        return {**state, "error": "PERMANENT: transformation is required (what students can DO after)"}
    if not target_student:
        return {**state, "error": "PERMANENT: target_student is required"}
    if delivery_format not in VALID_DELIVERY_FORMATS:
        return {**state, "error": f"PERMANENT: delivery_format '{delivery_format}' not in {VALID_DELIVERY_FORMATS}"}
    if expertise_level not in VALID_EXPERTISE_LEVELS:
        return {**state, "error": f"PERMANENT: expertise_level '{expertise_level}' not in {VALID_EXPERTISE_LEVELS}"}
    if not (3 <= num_modules <= MODULE_LIMIT):
        return {**state, "error": f"PERMANENT: num_modules must be 3–{MODULE_LIMIT} (got {num_modules})"}

    # ── Phase 1 — pure structure computation ──────────────────────────────────
    fmt_spec, level_note, estimated_hours = _compute_structure(delivery_format, expertise_level, num_modules)

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt = _build_prompt(
        course_title, target_student, transformation,
        delivery_format, num_modules, expertise_level,
        fmt_spec, level_note, estimated_hours,
    )

    # ── PRE checkpoint ────────────────────────────────────────────────────────
    checkpoint("PRE", ROLE, thread_id, {
        "delivery_format": delivery_format,
        "expertise_level": expertise_level,
        "num_modules": num_modules,
        "estimated_hours": estimated_hours,
    })

    claude  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    # ── Phase 2 — Claude call (TRANSIENT retry) ────────────────────────────────
    try:
        curriculum = _write_curriculum(claude, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code} — {exc.message}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    # ── Telemetry ──────────────────────────────────────────────────────────────
    metrics.log()
    metrics.persist()

    # ── POST checkpoint ───────────────────────────────────────────────────────
    checkpoint("POST", ROLE, thread_id, {
        "curriculum_chars": len(curriculum),
        "module_count": num_modules,
        "delivery_format": delivery_format,
    })

    return {
        **state,
        "curriculum":    curriculum,
        "module_count":  num_modules,
        "format_spec":   fmt_spec,
        "level_note":    level_note,
        "estimated_hours": estimated_hours,
        "error":         "",
    }


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(CourseState)
    g.add_node("course_designer", course_designer_node)
    g.set_entry_point("course_designer")
    g.add_edge("course_designer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
