"""
Product Launch Strategist - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), context (str)
    Outputs: launch_output (str), timeline (str)
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

ROLE        = "product_launch_strategist"
MAX_RETRIES = 3
MAX_TOKENS  = 2400



_LAUNCH_PHASES = {
    "pre_launch":  "Build waitlist, seed content, establish positioning, beta testing",
    "soft_launch": "Limited release, gather feedback, fix critical issues, case studies",
    "hard_launch": "Full PR push, paid acquisition, partnerships, events",
    "post_launch": "Retention focus, upsell, community building, iterate on feedback",
}

_PRICING_MODELS = {
    "freemium":      "Free tier for adoption, paid for value. Works for PLG.",
    "subscription":  "Monthly/annual. Annual discount 15-20%. Reduce churn.",
    "usage_based":   "Pay per use. Aligns cost with value. Complex to predict.",
    "tiered":        "Good/Better/Best. Anchor on middle tier. 3 tiers max.",
    "enterprise":    "Custom pricing. Annual contracts. POC required.",
}


class ProductLaunchStrategistState(TypedDict, total=False):
    workflow_id:   str
    timestamp:     str
    agent:         str
    error:         str | None
    task:          str
    context:       str
    launch_output:      str
    timeline:      str


def _build_prompt(state: dict) -> str:
    persona = get_persona(ROLE)
    task    = state["task"]
    ctx     = state.get("context", "")

    return f"""You are a {persona['personality']} specialist.

ROLE: Product launch strategy specialist — go-to-market planning, launch sequencing, demand generation, pricing strategy, channel selection

TASK:
{task}

CONTEXT:
{ctx or "None provided"}

OUTPUT FORMAT:
## Product Launch Strategy

### Positioning
[Who it's for, what problem it solves, why now, why us]

### Launch Timeline
[Week-by-week plan across pre-launch, soft, hard, post-launch]

### Channel Strategy
[Primary and secondary channels with expected CAC and volume]

### Pricing Recommendation
[Model, tiers, anchoring strategy, competitive comparison]

### Success Metrics
[KPIs for each phase with targets and measurement plan]

### Risk Mitigation
[What could go wrong and contingency plans]
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


def product_launch_strategist_node(state: dict) -> dict:
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

    return {**state, "agent": ROLE, "launch_output": output, "timeline": "", "error": None}
