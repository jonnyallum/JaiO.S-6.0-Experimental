"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : image_prompt_engineer
 SKILL : Image Prompt Engineer — JaiOS 6 Skill Node
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 ─────────────
 Input keys  : concept (str), style (str — optional), model (str — dall-e/midjourney/stable-diffusion)
 Output keys : prompt (str), negative_prompt (str), parameters (str)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Image generation prompt crafting agent

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

ROLE = "image_prompt_engineer"
MAX_RETRIES = 3
MAX_TOKENS = 2000


class ImagePromptEngineerState(BaseState):
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


def image_prompt_engineer_node(state: ImagePromptEngineerState) -> ImagePromptEngineerState:
    thread_id = state.get("thread_id", "unknown")
    task = state.get("task", "").strip()
    context = state.get("context", "").strip()

    if not task:
        return {**state, "error": "PERMANENT: task is required"}

    persona = get_persona(ROLE)
    context_block = f"\n\nAdditional context:\n{context}" if context else ""

    prompt = f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

Role: Image generation prompt crafting agent

You craft precise, detailed prompts for image generation models (DALL-E, Midjourney, Stable Diffusion). Specify style, composition, lighting, mood, and technical parameters.

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
    g = StateGraph(ImagePromptEngineerState)
    g.add_node("image_prompt_engineer", image_prompt_engineer_node)
    g.set_entry_point("image_prompt_engineer")
    g.add_edge("image_prompt_engineer", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
