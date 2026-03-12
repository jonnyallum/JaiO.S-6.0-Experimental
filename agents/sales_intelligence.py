"""
Sales Intelligence - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: intel_output (str), action_plan (str)
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

ROLE        = "sales_intelligence"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_SALES_FRAMEWORKS = {
    "bant":       "Budget, Authority, Need, Timeline — qualify before pitching",
    "meddic":     "Metrics, Economic buyer, Decision criteria/process, Identify pain, Champion",
    "spin":       "Situation, Problem, Implication, Need-payoff — discovery sequence",
    "challenger": "Teach, Tailor, Take Control — lead with insight, not questions",
    "sandler":    "Pain → Budget → Decision — upfront contracts, no free consulting",
}

_OBJECTION_PATTERNS = {
    "price":     "Reframe to ROI. Cost of inaction > cost of solution.",
    "timing":    "What changes in 6 months? Usually nothing. Cost of delay = X.",
    "competitor":"Don't trash-talk. Ask what criteria matter most. Win on YOUR strengths.",
    "authority": "Coach the champion. Give them the internal pitch deck.",
    "need":      "Go back to discovery. If no pain, no sale. Walk away.",
}


class SalesIntelligenceState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    intel_output:      str
    action_plan:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Sales intelligence and pipeline specialist — prospect research, outreach strategy, objection handling, deal qualification, pipeline analysis

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Sales Intelligence Report

### Prospect Profile
[Company, decision makers, budget signals, pain indicators]

### Qualification Assessment
[BANT/MEDDIC scoring with evidence]

### Outreach Strategy
[Multi-channel sequence: email, LinkedIn, phone, timing]

### Objection Preparation
[Likely objections and response frameworks]

### Action Plan
[Next 5 specific actions with owners and deadlines]
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


def sales_intelligence_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "intel_output": output, "action_plan": "", "error": None}
