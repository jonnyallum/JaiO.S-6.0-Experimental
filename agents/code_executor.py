"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : code_executor
 SKILL : Code Executor — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : code (str), language (str), task (str — what the code should do)
 Output keys : analysis (str), corrected_code (str), issues (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Sandboxed code execution and validation agent

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

ROLE = "code_executor"
MAX_RETRIES = 3
MAX_TOKENS = 2000


class CodeExecutorState(BaseState):
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


def code_executor_node(state: CodeExecutorState) -> CodeExecutorState:
    thread_id = state.get("thread_id", "unknown")
    task = state.get("task", "").strip()
    context = state.get("context", "").strip()

    if not task:
        return {**state, "error": "PERMANENT: task is required"}

    persona = get_persona(ROLE)
    context_block = f"\n\nAdditional context:\n{context}" if context else ""

    prompt = f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

Role: Sandboxed code execution and validation agent

You write, review, and mentally execute code snippets. Validate logic, catch bugs, suggest improvements. Never execute unsafe operations.

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
    g = StateGraph(CodeExecutorState)
    g.add_node("code_executor", code_executor_node)
    g.set_entry_point("code_executor")
    g.add_edge("code_executor", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
