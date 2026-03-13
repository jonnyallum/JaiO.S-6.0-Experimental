"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : ui_designer
SKILL : Ui Designer

Ui Designer - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: design_output (str), components (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task → ValueError (no retry)
    TRANSIENT  → HTTP 429/529 → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before analysis
    POST - output persisted after successful generation
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

ROLE        = "ui_designer"
MAX_RETRIES = 3
MAX_TOKENS  = 3000



_DESIGN_PRINCIPLES = {
    "hierarchy":     "Size > Color > Position > Shape — in that order",
    "spacing":       "8px grid system. Breathing room > density. Always.",
    "typography":    "2 fonts max. 1.5 line-height body. 1.2 headings.",
    "color":         "60-30-10 rule. Primary 60%, secondary 30%, accent 10%.",
    "contrast":      "WCAG AA minimum: 4.5:1 text, 3:1 large text/UI.",
    "responsiveness":"Mobile-first. Breakpoints: 640, 768, 1024, 1280.",
    "animation":     "150-300ms transitions. Ease-out for enter, ease-in for exit.",
}

_COMPONENT_PATTERNS = {
    "button":    "Label + icon optional. Min 44px touch target. Never rely on color alone.",
    "card":      "Image + title + description + CTA. Max 3 cards per row.",
    "form":      "Label above input. Error below. Never placeholder-only labels.",
    "modal":     "Title + body + actions. Always escapable. Focus trap required.",
    "nav":       "Max 7 items. Active state obvious. Mobile: hamburger or bottom nav.",
    "table":     "Sortable headers. Zebra striping optional. Sticky header on scroll.",
    "toast":     "Auto-dismiss 5s. Actionable toasts persist. Stack from bottom-right.",
}


class UiDesignerState(BaseState):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    design_output:      str
    components:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: UI visual design specialist — component design, design systems, visual hierarchy, responsive layouts, accessibility-first design

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## UI Design: Component Specification

### Visual Hierarchy
[Layout decisions, spacing, typography choices]

### Component Specifications
[For each component: dimensions, states, interactions, responsive behavior]

### Design Tokens
[Colors, spacing, typography, shadows, borders as CSS variables]

### Accessibility Notes
[WCAG compliance, keyboard nav, screen reader considerations]

### Implementation Notes
[Tailwind classes, Framer Motion animations, responsive breakpoints]
"""


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception(_is_transient), reraise=True)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=MAX_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    metrics.record(response); metrics.log(); metrics.persist()
    return response.content[0].text


def ui_designer_node(state: dict) -> dict:
    thread_id = state.get("workflow_id", "local")
    task      = state.get("task", "").strip()

    if not task:
        raise ValueError("PERMANENT: task is required.")

    checkpoint("PRE", thread_id, ROLE, {"task_len": len(task)})

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_prompt(state)

    try:
        output = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    checkpoint("POST", thread_id, ROLE, {"output_len": len(output)})

    return {**state, "agent": ROLE, "design_output": output, "components": "", "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(UiDesignerState)
    g.add_node("ui_designer", ui_designer_node)
    g.set_entry_point("ui_designer")
    g.add_edge("ui_designer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
