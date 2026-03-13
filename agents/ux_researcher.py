"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : ux_researcher
SKILL : Ux Researcher

Ux Researcher - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: research_output (str), recommendations (str)
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

ROLE        = "ux_researcher"
MAX_RETRIES = 3
MAX_TOKENS  = 3000



_HEURISTICS = {
    "visibility":       "System status always visible. User never guesses what happened.",
    "match":            "System speaks user language. No internal jargon.",
    "control":          "Undo always available. User never trapped.",
    "consistency":      "Same action = same result. Patterns reused, not reinvented.",
    "error_prevention": "Prevent errors > fix errors. Confirm destructive actions.",
    "recognition":      "Show options, don't make users remember. Autocomplete > free text.",
    "flexibility":      "Shortcuts for experts. Defaults for novices.",
    "aesthetics":       "Remove until it breaks. Every element earns its space.",
    "error_recovery":   "Plain language errors. Suggest fix. Never blame user.",
    "help":             "Contextual help > documentation. Progressive disclosure.",
}

_JOURNEY_STAGES = ["Awareness", "Consideration", "Decision", "Onboarding", "Usage", "Retention", "Advocacy"]


class UxResearcherState(BaseState):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    research_output:      str
    recommendations:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: UX research and usability specialist — user journey mapping, heuristic evaluation, usability testing plans, interaction design critique

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## UX Research Analysis

### User Journey Map
[Stage-by-stage analysis: touchpoints, emotions, pain points, opportunities]

### Heuristic Evaluation
[Score each of Nielsen's 10 heuristics 1-5, with specific findings]

### Usability Issues (Priority Ranked)
[Severity 1-4, issue description, affected user segment, recommended fix]

### Recommendations
[Specific, actionable improvements with expected impact]

### Research Plan
[What to test next, methodology, metrics to track]
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


def ux_researcher_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "research_output": output, "recommendations": "", "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(UxResearcherState)
    g.add_node("ux_researcher", ux_researcher_node)
    g.set_entry_point("ux_researcher")
    g.add_edge("ux_researcher", END)
    return g.compile()
