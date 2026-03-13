"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : automation_architect
SKILL : Automation Architecture — workflow description → n8n node spec, trigger config, implementation plan

Node Contract (@langraph doctrine):
  Inputs   : workflow_description (str), tools_available (str),
             trigger_type (str), complexity (str) — immutable after entry
  Outputs  : automation_spec (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All AutomationState fields are thread-scoped only.

Loop Policy:
  NONE — single-pass node.

Failure Discrimination:
  PERMANENT  → ValueError (missing workflow_description)
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
  UNEXPECTED → Exception

Checkpoint Semantics:
  PRE  — before Claude call
  POST — after completion

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics
from typing import TypedDict
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

ROLE          = "automation_architect"
MAX_RETRIES   = 3
RETRY_MIN_S   = 3
RETRY_MAX_S   = 45
MAX_TOKENS    = 1800
DESC_CHARS    = 3000
TOOLS_CHARS   = 500

VALID_TRIGGERS = {
    "webhook",      # HTTP POST trigger
    "schedule",     # Cron-based
    "email",        # Inbound email
    "form",         # Form submission
    "manual",       # Manual run / test
    "event",        # Event from another workflow
    "database",     # DB row insert/update
}

VALID_COMPLEXITY = {"simple", "medium", "complex"}


class AutomationState(BaseState):
    # Inputs
    workflow_description: str   # Plain English description of what the automation should do
    tools_available: str        # Comma-separated: "n8n, Resend, Supabase, OpenAI, Slack, etc."
    trigger_type: str           # webhook | schedule | email | form | manual | event | database
    complexity: str             # simple | medium | complex
    # Output
    automation_spec: str        # Full spec with nodes, logic, config; empty on failure


# ── Phase 1 — prompt construction (pure, no I/O) ───────────────────────────────────

def _build_spec_prompt(state: "AutomationState", persona: dict) -> str:
    tools = state.get("tools_available", "n8n, Resend, Supabase")
    return f"""{persona['personality']}

Design a complete automation workflow specification for the request below.
Output a precise, implementation-ready spec — named nodes, exact logic, config values.
Assume the developer will build this directly from your spec. No vague placeholders. Max 750 words.

━━━ WORKFLOW ━━━
{state['workflow_description'][:DESC_CHARS]}

━━━ TOOLS AVAILABLE ━━━
{tools[:TOOLS_CHARS]}

━━━ TRIGGER ━━━
{state['trigger_type'].upper()}

━━━ COMPLEXITY ━━━
{state.get('complexity', 'medium').upper()}

━━━ DELIVER ━━━

## Automation Spec: {state['workflow_description'][:60]}...

### Trigger Configuration
[Exact trigger setup: endpoint URL pattern, cron expression, event name, etc.]

### Node Map
| # | Node Name | Type | Config | Output |
|---|---|---|---|---|
[List every node in execution order. Be specific on node type and key config values.]

### Logic & Branching
[Any conditionals, loops, or error branches. Exact conditions.]

### Data Mapping
[Key fields passed between nodes. Input → Transform → Output.]

### Error Handling
[What happens when each critical step fails. Retry policy. Alert channel.]

### Testing Checklist
- [ ] [Specific test case 1]
- [ ] [Specific test case 2]
- [ ] [Edge case]

### Estimated Build Time
[Realistic estimate: simple = 30 min, medium = 2h, complex = 4-8h]"""


_build_prompt = _build_spec_prompt  # spec alias — canonical name for 19-point compliance

# ── Phase 2 — Claude call (TRANSIENT errors retried) ────────────────────────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def automation_architect_node(state: AutomationState) -> dict:
    thread_id   = state.get("workflow_id") or str(uuid.uuid4())
    description = state.get("workflow_description", "")
    persona     = get_persona(ROLE)
    notifier    = TelegramNotifier()
    state_logger = SupabaseStateLogger()
    metrics     = CallMetrics(thread_id, ROLE)

    def _checkpoint(cid: str, payload: dict) -> None:
        state_logger.log_state(thread_id, cid, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id,
             trigger=state.get("trigger_type", "manual"))

    try:
        if not description.strip():
            raise ValueError("workflow_description is required.")
        trigger = state.get("trigger_type", "manual")
        if trigger not in VALID_TRIGGERS:
            raise ValueError(
                f"Invalid trigger_type '{trigger}'. "
                f"Must be one of: {', '.join(sorted(VALID_TRIGGERS))}"
            )
        complexity = state.get("complexity", "medium")
        if complexity not in VALID_COMPLEXITY:
            raise ValueError(
                f"Invalid complexity '{complexity}'. Must be: simple | medium | complex"
            )

        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_spec_prompt(state, persona)

        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"trigger": trigger, "complexity": complexity, "status": "generating"},
        )

        spec = _generate(claude, prompt, metrics)
        metrics.log()
        metrics.persist()

        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"trigger": trigger, "status": "completed", "spec_chars": len(spec)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, spec_chars=len(spec))
        return {"automation_spec": spec, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", error=msg)
        notifier.agent_error(ROLE, description[:80], msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"status": "permanent_failure", "error": msg})
        return {"automation_spec": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", error=msg)
        notifier.agent_error(ROLE, description[:80], msg)
        return {"automation_spec": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", error=msg)
        notifier.agent_error(ROLE, description[:80], msg)
        return {"automation_spec": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(AutomationState)
    g.add_node("automation_architect", automation_architect_node)
    g.set_entry_point("automation_architect")
    g.add_edge("automation_architect", END)
    return g.compile()
