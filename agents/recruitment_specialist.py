"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : recruitment_specialist
SKILL : Recruitment Specialist

Recruitment Specialist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: recruitment_output (str), evaluation_criteria (str)
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

ROLE        = "recruitment_specialist"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_HIRING_PRINCIPLES = {
    "bar":           "Hire for trajectory, not just current skill. Culture add > culture fit.",
    "jd_writing":    "Outcomes > requirements. Show the mission, not just the checklist.",
    "evaluation":    "Structured interviews. Same questions. Rubric scoring. Reduce bias.",
    "pipeline":      "Source → Screen → Interview → Offer → Close. Measure conversion at each.",
    "speed":         "Time-to-hire is a competitive weapon. 48hr feedback. 1-week decision.",
}


class RecruitmentSpecialistState(BaseState):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    recruitment_output:      str
    evaluation_criteria:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Recruitment and talent acquisition specialist — job descriptions, candidate evaluation, interview design, hiring strategy

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Recruitment Strategy

### Role Definition
[Title, level, reporting structure, key outcomes expected]

### Job Description
[Mission, responsibilities, requirements, nice-to-haves, compensation range]

### Evaluation Rubric
[Scoring criteria for each interview stage]

### Interview Design
[Questions, exercises, take-home (if any), timeline]

### Sourcing Strategy
[Channels, outreach templates, referral incentives]
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


def recruitment_specialist_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "recruitment_output": output, "evaluation_criteria": "", "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(RecruitmentSpecialistState)
    g.add_node("recruitment_specialist", recruitment_specialist_node)
    g.set_entry_point("recruitment_specialist")
    g.add_edge("recruitment_specialist", END)
    return g.compile()
