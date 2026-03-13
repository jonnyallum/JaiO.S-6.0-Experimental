"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : deep_researcher
SKILL : Deep Researcher

Deep Researcher - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: research_output (str), sources (str)
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

ROLE        = "deep_researcher"
MAX_RETRIES = 3
MAX_TOKENS  = 4000



_RESEARCH_METHODS = {
    "systematic":   "Define question → search strategy → inclusion criteria → synthesis",
    "comparative":  "Multiple sources → cross-reference → consensus + divergence",
    "adversarial":  "Steel-man opposing views. Attack your own thesis first.",
    "temporal":     "Track how understanding evolved. Latest ≠ best.",
    "quantitative": "Numbers > opinions. Primary data > secondary. Sample size matters.",
}

_EVIDENCE_GRADES = {
    "A": "Multiple high-quality sources agree. Strong confidence.",
    "B": "Good evidence with minor gaps. Moderate confidence.",
    "C": "Limited or conflicting evidence. Low confidence.",
    "D": "Single source or speculation. Very low confidence.",
}


class DeepResearcherState(BaseState):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    research_output:      str
    sources:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Deep research specialist — multi-source synthesis, academic rigor, structured argumentation, evidence grading, comprehensive literature review

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Deep Research Report

### Research Question
[Precisely stated question with scope boundaries]

### Methodology
[Sources consulted, search strategy, inclusion/exclusion criteria]

### Findings
[Evidence-graded findings (A/B/C/D) with citations]

### Analysis
[Synthesis, patterns, contradictions, knowledge gaps]

### Conclusions
[Answering the research question with confidence level]

### Limitations
[What this research cannot answer and why]
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


def deep_researcher_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "research_output": output, "sources": "", "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(DeepResearcherState)
    g.add_node("deep_researcher", deep_researcher_node)
    g.set_entry_point("deep_researcher")
    g.add_edge("deep_researcher", END)
    return g.compile()
