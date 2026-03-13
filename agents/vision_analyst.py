"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : vision_analyst
 SKILL : Vision Analyst — JaiOS 6 Skill Node (Multi-Modal)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 Input keys  : task (str), image_url (str — URL or base64 data URI),
               analysis_type (str — "describe"|"qa"|"audit"|"extract")
 Output keys : analysis (str), findings (str), confidence (int 1-10)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 Uses Claude Vision API for image understanding.

 Failure Discrimination
 PERMANENT  — empty task, missing image_url
 TRANSIENT  — Anthropic 529/overload, image fetch timeout
 UNEXPECTED — any other exception
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
from __future__ import annotations

from state.base import BaseState
import base64
import urllib.request
from typing import TypedDict

import anthropic
import structlog
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langgraph.graph import StateGraph, END

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint
from tools.supabase_tools import SupabaseStateLogger  # checkpoint alias

log = structlog.get_logger()

ROLE = "vision_analyst"
MAX_RETRIES = 3
MAX_TOKENS = 4000

ANALYSIS_PROMPTS = {
    "describe": "Describe this image in detail. Cover layout, colors, text, objects, and overall impression.",
    "qa": "Answer the user's question about this image accurately and thoroughly.",
    "audit": "Audit this image for quality issues: alignment, contrast, readability, accessibility, brand consistency. Score each dimension 1-10.",
    "extract": "Extract all visible text, data, numbers, and structured information from this image. Return in a clean, organized format.",
}


class VisionState(BaseState):
    workflow_id: str
    timestamp: str
    agent: str
    task: str
    image_url: str
    analysis_type: str
    analysis: str
    findings: str
    confidence: int
    error: str | None


def _resolve_image(image_url: str) -> dict:
    """Convert URL or base64 to Claude Vision content block."""
    if image_url.startswith("data:image/"):
        # Already base64 data URI
        parts = image_url.split(",", 1)
        media_type = parts[0].split(":")[1].split(";")[0]
        data = parts[1] if len(parts) > 1 else ""
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    else:
        # Fetch from URL
        return {"type": "image", "source": {"type": "url", "url": image_url}}


def _is_transient(exc: BaseException) -> bool:
    """TRANSIENT = 429 rate limit or 529 overload — safe to retry."""
    from anthropic import APIStatusError
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((APIStatusError,)),
)
def _vision_query(task: str, image_url: str, analysis_type: str, persona: dict) -> dict:
    """Send image + prompt to Claude Vision."""
    client = anthropic.Anthropic()
    system = persona.get("system_prompt", "You are a visual analysis specialist.")
    base_prompt = ANALYSIS_PROMPTS.get(analysis_type, ANALYSIS_PROMPTS["describe"])
    full_prompt = f"{base_prompt}\n\nUser request: {task}"

    image_block = _resolve_image(image_url)

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                image_block,
                {"type": "text", "text": full_prompt},
            ],
        }],
    )

    text = resp.content[0].text
    return {"analysis": text, "findings": text[:500], "confidence": 8}
_generate = _vision_query  # spec alias



def _vision_node(state: VisionState) -> dict:
    task = state.get("task", "")
    image_url = state.get("image_url", "")

    if not task.strip():
        return {"analysis": "", "findings": "", "confidence": 0, "error": "PERMANENT: empty task"}
    if not image_url.strip():
        return {"analysis": "", "findings": "", "confidence": 0, "error": "PERMANENT: no image_url"}

    metrics = CallMetrics(ROLE)
    persona = get_persona(ROLE)
    analysis_type = state.get("analysis_type", "describe")

    checkpoint("PRE", state["workflow_id"], ROLE, {"analysis_type": analysis_type, "has_image": True})

    try:
        result = _vision_query(task, image_url, analysis_type, persona)
        metrics.record_success()
        checkpoint("POST", state["workflow_id"], ROLE, {"analysis_len": len(result["analysis"])})
        return {**result, "agent": ROLE,
 "error": None}
    except APIStatusError as e:
        metrics.record_failure(str(e))
        return {"analysis": "", "findings": "", "confidence": 0, "error": f"TRANSIENT: {e.status_code}"}
    except Exception as e:
        metrics.record_failure(str(e))
        return {"analysis": "", "findings": "", "confidence": 0, "error": f"UNEXPECTED: {str(e)[:200]}"}


def build_vision_graph():
    g = StateGraph(VisionState)
    g.add_node("vision", _vision_node)
    g.set_entry_point("vision")
    g.add_edge("vision", END)
    return g.compile()


def vision_analyst_node(state: dict) -> dict:
    graph = build_vision_graph()
    return graph.invoke(state)


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — delegates to node function."""
    return _vision_node(state)
