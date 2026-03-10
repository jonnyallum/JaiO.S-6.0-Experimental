"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : brief_writer
SKILL : Brief Writer — context + goal → structured client brief, proposal, or scope of work

Node Contract (@langraph doctrine):
  Inputs   : client_name (str), brief_type (str), context (str), goal (str),
             budget_hint (str), timeline_hint (str) — immutable after entry
  Outputs  : brief (str), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All BriefWriterState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (missing required fields: client_name, context, goal)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks generation started)
  POST — Supabase log after completion (records brief type, output size)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE            = "brief_writer"
MAX_RETRIES     = 3
RETRY_MIN_S     = 3
RETRY_MAX_S     = 45
MAX_TOKENS      = 1600   # Briefs need depth — proposals can run 600-900 words
CONTEXT_CHARS   = 3000   # Context truncation limit
GOAL_CHARS      = 1000   # Goal truncation limit

VALID_BRIEF_TYPES = {
    "proposal",       # Sales proposal with pricing, deliverables, ROI case
    "scope_of_work",  # Technical SOW with milestones and acceptance criteria
    "discovery",      # Discovery session brief with questions and objectives
    "onboarding",     # New client onboarding document with next steps
    "report",         # Progress/results report for an existing engagement
}


# ── State schema ─────────────────────────────────────────────────────────────────
class BriefWriterState(BaseState):
    # Inputs — written by caller, immutable inside this node
    client_name: str      # Client or prospect name
    brief_type: str       # proposal | scope_of_work | discovery | onboarding | report
    context: str          # Background: industry, pain points, current situation
    goal: str             # What the brief needs to achieve or communicate
    budget_hint: str      # Optional budget range hint for proposals (e.g. "£500-750/mo")
    timeline_hint: str    # Optional timeline hint (e.g. "4 weeks", "ongoing retainer")
    # Outputs — written by this node, read by downstream nodes
    brief: str            # Structured document ready to send/refine; empty string on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Template map — structure hints for each brief type ───────────────────────────
_BRIEF_TEMPLATES: dict[str, str] = {
    "proposal": """
## Proposal: [Service Name] for {client}

### Executive Summary
[2-3 sentences — problem, solution, outcome]

### The Problem We're Solving
[Specific pain points for this client]

### Our Solution
[What we deliver — specific, not generic]

### Deliverables
| Item | Description | Included |
|---|---|---|

### Investment
[Pricing: setup fee + monthly retainer. Tie to ROI where possible]

### Why Us
[3 bullet points — specific proof, not claims]

### Next Steps
[Clear CTA — what happens after they say yes]
""",
    "scope_of_work": """
## Scope of Work: {client}

### Project Overview
[One paragraph — objective and success definition]

### Deliverables & Milestones
| # | Deliverable | Acceptance Criteria | Due |
|---|---|---|---|

### Out of Scope
[Be explicit — what is NOT included]

### Dependencies
[What we need from the client to hit milestones]

### Change Management
[How out-of-scope requests are handled]

### Sign-off
[What constitutes project completion]
""",
    "discovery": """
## Discovery Brief: {client}

### Objectives
[What we need to understand by end of the session]

### Agenda (60 min)
1. [Topic — 10 min]
2. ...

### Key Questions
[Numbered list — the most important questions to answer]

### Pre-read for Client
[What they should bring / prepare]

### Success Criteria
[How we know the discovery was successful]
""",
    "onboarding": """
## Onboarding Plan: {client}

### Welcome
[Warm, specific — reference what they signed up for]

### What Happens Next
| Week | Focus | Owner | Deliverable |
|---|---|---|---|

### Access & Credentials Required
[Checklist — what we need from them]

### Points of Contact
[Who handles what on our side]

### 30-Day Success Milestone
[One clear, measurable outcome for day 30]
""",
    "report": """
## Progress Report: {client}

### Period
[Date range]

### Summary
[3 sentences — what we did, what it achieved, what's next]

### Work Completed
| Item | Status | Impact |
|---|---|---|

### Metrics
| Metric | Baseline | Current | Change |
|---|---|---|---|

### Issues & Risks
[Any blockers or items needing client decision]

### Next Period
[Planned work for next reporting period]
""",
}


def _build_brief_prompt(state: "BriefWriterState", persona: dict) -> str:
    """Build the brief generation prompt. Pure function — no I/O."""
    brief_type  = state.get("brief_type", "proposal")
    template    = _BRIEF_TEMPLATES.get(brief_type, _BRIEF_TEMPLATES["proposal"])
    budget_line = f"\nBudget range  : {state['budget_hint']}" if state.get("budget_hint") else ""
    timeline_line = f"\nTimeline      : {state['timeline_hint']}" if state.get("timeline_hint") else ""

    return f"""{persona['personality']}

Write a professional {brief_type.replace('_', ' ')} for the client below.
Use the template structure provided. Fill every section with specific, substantive content.
No lorem ipsum, no generic filler. If a field is unknown, write a sensible placeholder in [brackets].
Output only the finished document — no commentary.

━━━ CLIENT ━━━
Name          : {state['client_name']}{budget_line}{timeline_line}

━━━ CONTEXT ━━━
{state['context'][:CONTEXT_CHARS]}

━━━ GOAL ━━━
{state['goal'][:GOAL_CHARS]}

━━━ TEMPLATE ━━━
{template.format(client=state['client_name'])}"""


# ── Phase 2: Write (Claude call, retried on transient errors only) ────────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _write(client: anthropic.Anthropic, prompt: str) -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ── Main node ─────────────────────────────────────────────────────────────────────
def brief_writer_node(state: BriefWriterState) -> dict:
    """
    Brief Writer node — single pass, no loop.

    Execution order:
      1. Validate required inputs (client_name, context, goal)
      2. Build prompt (pure function)
      3. PRE checkpoint (before Claude call)
      4. Write (Phase 2 — Claude)
      5. POST checkpoint (after completion)
      6. Return state patch

    @langraph: show me the checkpoint before you call production-ready.
    """
    thread_id  = state.get("workflow_id") or str(uuid.uuid4())
    brief_type = state.get("brief_type", "proposal")
    client_name = state.get("client_name", "")
    persona    = get_persona(ROLE)
    notifier   = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id,
             client=client_name, brief_type=brief_type)

    try:
        # Input guards — PERMANENT failures
        if not client_name.strip():
            raise ValueError("client_name is required and cannot be empty.")
        if not state.get("context", "").strip():
            raise ValueError("context is required — provide client background and situation.")
        if not state.get("goal", "").strip():
            raise ValueError("goal is required — describe what this brief needs to achieve.")
        if brief_type not in VALID_BRIEF_TYPES:
            raise ValueError(
                f"Invalid brief_type '{brief_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_BRIEF_TYPES))}"
            )

        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        # Build prompt (pure — no I/O)
        prompt = _build_brief_prompt(state, persona)

        # PRE checkpoint — mark generation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"client": client_name, "brief_type": brief_type, "status": "writing"},
        )

        # Phase 2 — write (TRANSIENT failures retried by tenacity)
        brief = _write(claude, prompt)

        # POST checkpoint — record completion and output size
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"client": client_name, "brief_type": brief_type,
             "status": "completed", "brief_chars": len(brief)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id,
                 client=client_name, brief_type=brief_type, brief_chars=len(brief))
        return {"brief": brief, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input",
                  error=msg, client=client_name)
        notifier.agent_error(ROLE, client_name, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"client": client_name, "brief_type": brief_type,
                     "status": "permanent_failure", "error": msg})
        return {"brief": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        return {"brief": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, client_name, msg)
        return {"brief": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}
