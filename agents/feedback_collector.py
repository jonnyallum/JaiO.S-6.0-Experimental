"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 feedback_collector — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : feedback_data (str), product_context (str), feedback_type (str — nps/csat/open/bug)
 Output keys : analysis (str), themes (str), action_items (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 User feedback collection and analysis agent

 Persona: identity injected at runtime via personas/config.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

import anthropic
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona

ROLE = "feedback_collector"
MAX_RETRIES = 3
MAX_TOKENS = 2000


class FeedbackCollectorState(TypedDict):
    task: str
    context: str
    thread_id: str
    output: str
    error: str


@retry(
    retry=retry_if_exception_type(APIStatusError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def _call_claude(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def feedback_collector_node(state: FeedbackCollectorState) -> FeedbackCollectorState:
    thread_id = state.get("thread_id", "unknown")
    task = state.get("task", "").strip()
    context = state.get("context", "").strip()

    if not task:
        return {**state, "error": "PERMANENT: task is required"}

    persona = get_persona(ROLE)
    context_block = f"\n\nAdditional context:\n{context}" if context else ""

    prompt = f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

Role: User feedback collection and analysis agent

You design feedback collection strategies, analyse sentiment, identify patterns, prioritise issues, and produce actionable improvement recommendations.

Task: {task}{context_block}

Produce a thorough, actionable response. Be specific, not generic."""

    checkpoint("PRE", ROLE, thread_id, {"task_chars": len(task)})

    client = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)

    try:
        output = _call_claude(client, prompt, metrics)
    except APIStatusError as exc:
        return {**state, "error": f"TRANSIENT: Claude API error {exc.status_code}"}
    except Exception as exc:
        return {**state, "error": f"UNEXPECTED: {type(exc).__name__}: {exc}"}

    metrics.log()
    metrics.persist()

    checkpoint("POST", ROLE, thread_id, {"output_chars": len(output)})

    return {**state, "output": output, "error": ""}


def build_graph() -> StateGraph:
    g = StateGraph(FeedbackCollectorState)
    g.add_node("feedback_collector", feedback_collector_node)
    g.set_entry_point("feedback_collector")
    g.add_edge("feedback_collector", END)
    return g.compile()
