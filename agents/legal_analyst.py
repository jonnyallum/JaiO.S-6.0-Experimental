"""
Legal Analyst - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: legal_output (str), risk_flags (str)
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
from langgraph.graph import StateGraph, END

ROLE        = "legal_analyst"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_LEGAL_AREAS = {
    "contract":    "Terms, obligations, liability caps, termination, IP assignment, non-compete",
    "compliance":  "GDPR, CCPA, SOC2, PCI-DSS, industry-specific regulations",
    "ip":          "Patents, trademarks, copyrights, trade secrets, licensing",
    "employment":  "At-will, non-compete, equity, classification, termination risk",
    "corporate":   "Formation, governance, cap table, shareholder agreements",
}

_RISK_SEVERITY = {
    "critical": "Immediate legal exposure. Stop and fix before proceeding.",
    "high":     "Significant risk. Address within 30 days.",
    "medium":   "Manageable risk. Schedule for next review cycle.",
    "low":      "Minor concern. Note and monitor.",
}


class LegalAnalystState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    legal_output:      str
    risk_flags:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Legal analysis specialist — contract review, compliance assessment, risk identification, regulatory research, IP strategy

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Legal Analysis

### Summary
[Plain-English summary of the legal situation]

### Key Findings
[Clause-by-clause or issue-by-issue analysis]

### Risk Assessment
[Each risk: severity, probability, impact, mitigation]

### Recommendations
[Specific actions: clauses to negotiate, compliance steps, filings needed]

### Disclaimer
[This is AI-assisted analysis, not legal advice. Consult qualified counsel.]
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


def legal_analyst_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "legal_output": output, "risk_flags": "", "error": None}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(LegalAnalystState)
    g.add_node("legal_analyst", legal_analyst_node)
    g.set_entry_point("legal_analyst")
    g.add_edge("legal_analyst", END)
    return g.compile()
