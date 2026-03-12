"""
System Architect - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: architecture_output (str), diagram_description (str)
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

import re
from typing import TypedDict

import anthropic
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint

ROLE        = "system_architect"
MAX_RETRIES = 3
MAX_TOKENS  = 3000



_ARCHITECTURE_PATTERNS = {
    "monolith":     "Start here. Split when you have clear bounded contexts and team boundaries.",
    "microservices":"One service per bounded context. Own DB. Async communication preferred.",
    "event_driven": "Events for decoupling. CQRS for read/write separation. Idempotency required.",
    "serverless":   "For spiky workloads. Cold starts matter. Keep functions <15s.",
    "edge":         "CDN + edge functions for latency-critical paths. Cache aggressively.",
}

_SCALABILITY_CHECKLIST = [
    "Horizontal scaling: can you add more instances?",
    "Database: read replicas, connection pooling, query optimization",
    "Caching: Redis/CDN for hot paths, cache invalidation strategy",
    "Async: queue heavy work, don't block request threads",
    "Rate limiting: protect upstream services and APIs",
    "Circuit breakers: fail gracefully when dependencies die",
    "Observability: metrics, logs, traces for every service",
]


class SystemArchitectState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    architecture_output:      str
    diagram_description:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: System architecture specialist — infrastructure design, scalability planning, service decomposition, technology selection, capacity planning

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## System Architecture

### Current State Assessment
[What exists, what works, what doesn't scale]

### Proposed Architecture
[Components, services, data flow, technology choices with rationale]

### Scalability Plan
[Horizontal scaling, caching, async processing, database strategy]

### Risk Analysis
[Single points of failure, blast radius, mitigation strategies]

### Migration Path
[Phase 1-3 with clear milestones and rollback plans]
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


def system_architect_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "architecture_output": output, "diagram_description": "", "error": None}
