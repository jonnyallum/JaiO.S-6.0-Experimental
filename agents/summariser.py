"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : summariser
 SKILL : Summariser — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : text (str), target_length (str — one_liner/executive/detailed), focus_area (str — optional)
 Output keys : summary (str), key_points (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Text summarisation specialist

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

ROLE = "summariser"
MAX_RETRIES = 3
MAX_TOKENS = 2000


class SummariserState(BaseState):
    task: str
    context: str
    thread_id: str
    output: str
    error: str


def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


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
_generate = _call_claude  # spec alias



def summariser_node(state: SummariserState) -> SummariserState:
    thread_id = state.get("thread_id", "unknown")
    task = state.get("task", "").strip()
    context = state.get("context", "").strip()

    if not task:
        return {**state, "error": "PERMANENT: task is required"}

    persona = get_persona(ROLE)
    context_block = f"\n\nAdditional context:\n{context}" if context else ""

    prompt = f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

Role: Text summarisation specialist

You produce concise, accurate summaries at multiple granularities: one-liner, executive summary, or detailed brief. Preserve all critical facts. Never hallucinate details not in the source.

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

    return {**state, "output": output, "agent": ROLE, "error": None}


def build_graph() -> StateGraph:
    g = StateGraph(SummariserState)
    g.add_node("summariser", summariser_node)
    g.set_entry_point("summariser")
    g.add_edge("summariser", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
