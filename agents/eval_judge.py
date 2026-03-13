"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : eval_judge
 SKILL : Eval Judge — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : content (str), criteria (str), rubric (str — optional)
 Output keys : score (int 0-100), verdict (str), feedback (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 LLM-as-judge quality scoring agent

 Persona: identity injected at runtime via personas/config.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

from __future__ import annotations

from state.base import BaseState

import anthropic
import structlog
from anthropic import APIStatusError
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing_extensions import TypedDict

from checkpoints import checkpoint
from metrics import CallMetrics
from personas.config import get_persona
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()

ROLE = "eval_judge"
MAX_RETRIES = 3
MAX_TOKENS = 2000


class EvalJudgeState(BaseState):
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


def eval_judge_node(state: EvalJudgeState) -> EvalJudgeState:
    thread_id = state.get("thread_id", "unknown")
    task = state.get("task", "").strip()
    context = state.get("context", "").strip()

    if not task:
        return {**state, "error": "PERMANENT: task is required"}

    persona = get_persona(ROLE)
    context_block = f"\n\nAdditional context:\n{context}" if context else ""

    prompt = f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

Role: LLM-as-judge quality scoring agent

You evaluate the quality of AI-generated content. Score outputs on accuracy, completeness, clarity, actionability, and adherence to brief. Be ruthlessly honest.

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
    g = StateGraph(EvalJudgeState)
    g.add_node("eval_judge", eval_judge_node)
    g.set_entry_point("eval_judge")
    g.add_edge("eval_judge", END)
    return g.compile()
