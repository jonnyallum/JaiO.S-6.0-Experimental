"""
Due Diligence Analyst - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: dd_output (str), risk_score (str)
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

ROLE        = "due_diligence_analyst"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_DD_FRAMEWORK = {
    "market":     "TAM/SAM/SOM, growth rate, market timing, secular trends",
    "product":    "Product-market fit evidence, retention, NPS, usage metrics",
    "team":       "Founder experience, key hires, board composition, advisor quality",
    "financials": "Revenue, burn rate, runway, unit economics, path to profitability",
    "legal":      "Cap table, IP ownership, litigation, regulatory exposure",
    "technology": "Tech stack, scalability, security posture, technical debt",
    "competition":"Direct/indirect competitors, differentiation, switching costs",
}


class DueDiligenceAnalystState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    dd_output:      str
    risk_score:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Due diligence specialist — company evaluation, market validation, risk scoring, investment memo preparation

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Due Diligence Report

### Executive Summary
[One-paragraph verdict with confidence level]

### Market Analysis
[TAM, growth, timing, competitive dynamics]

### Product & Technology
[Product-market fit evidence, tech assessment]

### Team Assessment
[Founders, key hires, gaps, culture signals]

### Financial Analysis
[Revenue, burn, unit economics, projections]

### Risk Matrix
[Each risk scored: probability (1-5) x impact (1-5)]

### Recommendation
[Invest/Pass with conditions and key milestones]
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


def due_diligence_analyst_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "dd_output": output, "risk_score": "", "error": None}
