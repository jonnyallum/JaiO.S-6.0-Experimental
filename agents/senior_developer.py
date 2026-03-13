"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : senior_developer
SKILL : Senior Developer

Senior Developer - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: dev_output (str), implementation_plan (str)
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

ROLE        = "senior_developer"
MAX_RETRIES = 3
MAX_TOKENS  = 3000



_CODE_PRINCIPLES = {
    "solid":         "Single Responsibility, Open/Closed, Liskov, Interface Seg, Dependency Inv",
    "dry":           "Don't Repeat Yourself - but don't over-abstract prematurely",
    "kiss":          "Keep It Simple. Clever code is technical debt.",
    "yagni":         "You Aren't Gonna Need It. Build what's needed now.",
    "testing":       "Test behavior not implementation. Edge cases first.",
    "naming":        "Long descriptive names > short cryptic ones. Code reads 10x more than writes.",
    "error_handling":"Fail fast, fail loud. Never swallow exceptions silently.",
    "perf":          "Measure first, optimise second. Premature optimisation is evil.",
}

_TECH_STACK = {
    "frontend":   "Next.js 15+, React 19, TypeScript strict, Tailwind v4",
    "backend":    "Python 3.12+, FastAPI, Supabase, PostgreSQL",
    "testing":    "Pytest, Playwright, Vitest, React Testing Library",
    "deployment": "Vercel (frontend), GCP VM (backend), GitHub Actions CI/CD",
    "tooling":    "ESLint, Prettier, Ruff, MyPy, pre-commit hooks",
}


class SeniorDeveloperState(BaseState):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    dev_output:      str
    implementation_plan:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Senior full-stack development specialist — code architecture, code review, implementation planning, technical debt analysis, refactoring strategy

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Development Analysis

### Architecture Assessment
[Current state, patterns used, technical debt identified]

### Implementation Plan
[Step-by-step with file paths, functions, dependencies]

### Code Review
[Issues found, severity, suggested fixes with code snippets]

### Testing Strategy
[Unit tests, integration tests, edge cases to cover]

### Performance & Security
[Bottlenecks, vulnerabilities, optimization opportunities]
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


def senior_developer_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "dev_output": output, "implementation_plan": "", "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(SeniorDeveloperState)
    g.add_node("senior_developer", senior_developer_node)
    g.set_entry_point("senior_developer")
    g.add_edge("senior_developer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
