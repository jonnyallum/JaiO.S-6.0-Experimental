"""
Financial Planner - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: financial_output (str), projections (str)
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

ROLE        = "financial_planner"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_FINANCIAL_MODELS = {
    "dcf":         "Discount future cash flows. Use conservative growth rates.",
    "three_stmt":  "Income Statement → Balance Sheet → Cash Flow. Always connected.",
    "unit_econ":   "CAC, LTV, payback period, gross margin per unit.",
    "scenario":    "Base, bull, bear. Assign probabilities. Expected value = weighted avg.",
    "sensitivity": "One variable at a time. Show which inputs matter most.",
}

_COST_CATEGORIES = {
    "fixed":     "Rent, salaries, subscriptions — doesn't change with volume",
    "variable":  "COGS, API costs, commissions — scales with revenue",
    "semi_var":  "Hosting, support staff — step function, not linear",
    "one_time":  "Setup costs, migrations, legal — budget separately",
}


class FinancialPlannerState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    financial_output:      str
    projections:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Financial planning and analysis specialist — budgeting, forecasting, cash flow modeling, scenario planning, cost optimization

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Financial Analysis

### Current State
[Revenue, costs, margins, burn rate, runway]

### Projections (12 months)
[Monthly: revenue, costs, cash flow, key assumptions]

### Scenario Analysis
[Base/Bull/Bear with probability weights]

### Cost Optimization
[Top 5 cost reduction opportunities with estimated savings]

### Recommendations
[Specific actions with financial impact and timeline]
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


def financial_planner_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "financial_output": output, "projections": "", "error": None}
