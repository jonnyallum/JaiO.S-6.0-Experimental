"""
Investment Analyst - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: analysis_output (str), recommendation (str)
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

ROLE        = "investment_analyst"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_ANALYSIS_FRAMEWORKS = {
    "fundamental":  "Revenue, margins, growth rate, TAM, competitive moat, management quality",
    "technical":    "Price action, volume, support/resistance, momentum indicators",
    "dcf":          "Discount future cash flows. Terminal value. WACC sensitivity.",
    "comps":        "Compare multiples: P/E, EV/EBITDA, P/S against peer group",
    "risk":         "Volatility, drawdown, Sharpe ratio, correlation, tail risk",
}

_DUE_DILIGENCE_CHECKLIST = [
    "Market size and growth trajectory",
    "Competitive landscape and defensibility",
    "Revenue model sustainability",
    "Unit economics (CAC, LTV, payback period)",
    "Team capability and track record",
    "Regulatory and legal risks",
    "Technology moat or switching costs",
]


class InvestmentAnalystState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    analysis_output:      str
    recommendation:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Investment analysis specialist — market research, financial modeling, risk assessment, portfolio strategy, due diligence

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Investment Analysis

### Market Overview
[Market size, growth drivers, competitive landscape]

### Financial Analysis
[Key metrics, valuation, growth projections]

### Risk Assessment
[Key risks ranked by probability and impact]

### Recommendation
[Buy/Hold/Sell with price target and thesis]

### Monitoring Triggers
[What would change the thesis — bull and bear scenarios]
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


def investment_analyst_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "analysis_output": output, "recommendation": "", "error": None}
