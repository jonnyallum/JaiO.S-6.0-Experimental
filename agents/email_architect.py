"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : email_architect
SKILL : Email Architect — design and write complete email sequences with subject lines,
        body copy, and CTAs; hard-capped at EMAIL_LIMIT emails per sequence

Node Contract (@langraph doctrine):
  Inputs   : sequence_goal (str), audience (str), product (str), num_emails (int),
             tone (str), from_name (str) — immutable after entry
  Outputs  : email_sequence (str), email_count (int), error (str|None), agent (str)
  Tools    : Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]
             Telemetry: CallMetrics per invocation — tokens, cost_usd, latency_ms [non-fatal]

Thread Memory (checkpoint-scoped):
  All EmailSequenceState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single Claude call writes the entire sequence in one pass.
  EMAIL_LIMIT = 7 is a hard ceiling on num_emails — enforced before the Claude call.
  @langraph: multi-email sequences are a prompt engineering problem, not a graph loop
  problem. Producing all emails in one call ensures tonal and narrative consistency.
  A loop would produce N disconnected emails with no arc — worse quality, more cost.

Failure Discrimination:
  PERMANENT  → ValueError (missing required fields, invalid goal/tone, num_emails > EMAIL_LIMIT)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (records goal, num_emails, tone)
  POST — Supabase log after completion (records output size, email_count)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import re
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

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE            = "email_architect"
MAX_RETRIES     = 3
RETRY_MIN_S     = 3
RETRY_MAX_S     = 45
MAX_TOKENS      = 2500     # Multi-email sequences need generous token budget
AUDIENCE_CHARS  = 500
PRODUCT_CHARS   = 800
EMAIL_LIMIT     = 7        # Hard ceiling — @langraph loop policy: no sequence exceeds 7 emails
EMAIL_MIN       = 1

VALID_GOALS = {
    "nurture",        # Lead nurture — educate and build trust over time
    "onboarding",     # New customer welcome series
    "re_engagement",  # Win back cold or churned contacts
    "sales",          # Direct response — offer, urgency, close
    "launch",         # Product or feature launch sequence
    "abandonment",    # Cart or form abandonment recovery
    "follow_up",      # Post-call or post-demo follow-up
}

VALID_TONES = {"professional", "casual", "urgent", "celebratory", "empathetic", "direct"}

# Per-goal send-day and purpose map (supports planning without Claude)
_GOAL_PLANS = {
    "nurture":       [(1,"Welcome + problem acknowledgement"),(3,"Education 1"),(7,"Case study"),(14,"Objection handling"),(21,"Soft offer"),(28,"Hard offer"),(35,"Last chance")],
    "onboarding":    [(0,"Welcome + quick win"),(1,"Feature highlight 1"),(3,"Feature highlight 2"),(7,"Community/support"),(14,"Success story"),(21,"Upsell"),(30,"Check-in")],
    "re_engagement": [(0,"We miss you"),(3,"What changed"),(7,"Personal offer"),(10,"Urgency"),(14,"Last chance"),(17,"Goodbye + resubscribe"),(0,"")],
    "sales":         [(0,"Hook + big promise"),(2,"Problem agitation"),(4,"Solution reveal"),(6,"Social proof"),(8,"Objection crusher"),(9,"Urgency + offer"),(10,"Final call")],
    "launch":        [(7,"Teaser"),(5,"Reveal + waitlist"),(3,"Countdown"),(2,"Behind the scenes"),(1,"Launch day"),(0,"Last chance"),(1,"Recap")],
    "abandonment":   [(0,"Did you forget?"),(1,"Still thinking?"),(3,"What others say"),(5,"Final + incentive"),(0,""),(0,""),(0,"")],
    "follow_up":     [(0,"Thank you + recap"),(2,"Resources"),(5,"Next step"),(10,"Check-in"),(21,"Value add"),(0,""),(0,"")],
}


# ── State schema ─────────────────────────────────────────────────────────────────
class EmailSequenceState(BaseState):
    # Inputs — written by caller, immutable inside this node
    sequence_goal: str    # Goal type — see VALID_GOALS
    audience: str         # Who receives this — role, pain, awareness level
    product: str          # What is being communicated about
    num_emails: int       # Number of emails — hard-capped at EMAIL_LIMIT
    tone: str             # Communication tone — see VALID_TONES
    from_name: str        # Sender name for personalisation context
    # Outputs — written by this node, read by downstream nodes
    email_sequence: str   # Full sequence, delimited by ===EMAIL N===; empty on failure
    email_count: int      # Actual emails written; 0 on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: Sequence planning (pure, independently testable) ─────────────────────
def _plan_sequence(goal: str, num_emails: int) -> list:
    """
    Return a list of (day, purpose) tuples for each email. Pure function — no I/O.
    Separation allows unit testing without mocking Claude.
    """
    plan = _GOAL_PLANS.get(goal, [(i, f"Email {i+1}") for i in range(EMAIL_LIMIT)])
    return [(day, purpose) for day, purpose in plan[:num_emails] if purpose]


def _count_emails(sequence_text: str) -> int:
    """Count ===EMAIL N=== delimiters in output. Pure function."""
    return len(re.findall(r"===EMAIL \d+===", sequence_text))


# ── Phase 2: Sequence writing (Claude call, retried on transient errors only) ────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _write_sequence(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call writing the full sequence. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text.strip()


def _build_prompt(state: "EmailSequenceState", plan: list, persona: dict) -> str:
    """Format sequence plan and context into a writing prompt. Pure function — no I/O."""
    plan_md = "\n".join(
        f"  Email {i+1} (Day {day}): {purpose}"
        for i, (day, purpose) in enumerate(plan)
    )
    num = len(plan)

    return f"""{persona['personality']}

Write a {num}-email {state['sequence_goal'].replace('_', ' ')} sequence. Tone: {state['tone'].upper()}.
Every email must be complete and send-ready — no placeholders except [FIRST NAME].
Separate emails with: ===EMAIL N=== (N = email number).

━━━ SEQUENCE BRIEF ━━━
Goal      : {state['sequence_goal'].replace('_', ' ').title()}
From      : {state.get('from_name', 'The Team')}
Audience  : {state.get('audience', '')[:AUDIENCE_CHARS]}
Product   : {state.get('product', '')[:PRODUCT_CHARS]}
Tone      : {state['tone']}

━━━ EMAIL PLAN ━━━
{plan_md}

━━━ FORMAT PER EMAIL ━━━
===EMAIL N===
**Subject:** [max 50 chars — no clickbait]
**Preview text:** [40-90 chars — complements subject, not a repeat]
**Send day:** Day X

[Opening line — do not start with "I" or recipient name]

[Body — 100-250 words. One idea per email. Specific, conversational. No filler.]

[CTA — one clear action]

---

━━━ RULES ━━━
- Write all {num} emails in full — do not summarise or skip any
- Each subject line tests a different angle
- Vary the CTA — not every email sells
- No generic filler. No [INSERT X HERE] except [FIRST NAME]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def email_architect_node(state: EmailSequenceState) -> dict:
    """
    Email Architect node — single pass, no loop.

    Execution order:
      1. Validate inputs (required fields, valid goal/tone, num_emails <= EMAIL_LIMIT)
      2. Plan sequence structure (Phase 1 — pure, no Claude)
      3. PRE checkpoint (before Claude call)
      4. Write sequence (Phase 2 — Claude, full sequence in one call)
      5. Count emails written (pure function)
      6. metrics.log() + metrics.persist() [non-fatal]
      7. POST checkpoint (after completion)
      8. Return state patch

    @langraph: EMAIL_LIMIT enforced at input validation — never inside a generation loop.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    goal         = state.get("sequence_goal", "")
    tone         = state.get("tone", "professional")
    num_emails   = state.get("num_emails", 3)
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, goal=goal,
             num_emails=num_emails, tone=tone)

    try:
        # Input guards — PERMANENT failures
        if not state.get("audience", "").strip():
            raise ValueError("audience is required — describe who receives these emails.")
        if not state.get("product", "").strip():
            raise ValueError("product is required — describe what is being communicated.")
        if goal not in VALID_GOALS:
            raise ValueError(
                f"Invalid sequence_goal '{goal}'. "
                f"Must be one of: {', '.join(sorted(VALID_GOALS))}"
            )
        if tone not in VALID_TONES:
            raise ValueError(
                f"Invalid tone '{tone}'. Must be one of: {', '.join(sorted(VALID_TONES))}"
            )
        # Hard ceiling — PERMANENT failure, not a silent clamp (@langraph loop policy)
        if not (EMAIL_MIN <= num_emails <= EMAIL_LIMIT):
            raise ValueError(
                f"num_emails must be {EMAIL_MIN}-{EMAIL_LIMIT} (got {num_emails}). "
                f"EMAIL_LIMIT={EMAIL_LIMIT} is a hard ceiling per @langraph loop policy."
            )

        claude  = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics = CallMetrics(thread_id, ROLE)

        # Phase 1 — plan sequence (pure, independently testable)
        plan = _plan_sequence(goal, num_emails)
        log.info(f"{ROLE}.planned", emails_planned=len(plan))

        # PRE checkpoint — mark sequence writing started
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"goal": goal, "num_emails": num_emails, "tone": tone,
             "status": "writing", "emails_planned": len(plan)},
        )

        # Phase 2 — write sequence (TRANSIENT failures retried by tenacity)
        prompt         = _build_prompt(state, plan, persona)
        email_sequence = _write_sequence(claude, prompt, metrics)
        email_count    = _count_emails(email_sequence)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"goal": goal, "status": "completed", "email_count": email_count,
             "sequence_chars": len(email_sequence)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id,
                 email_count=email_count, chars=len(email_sequence))
        return {"email_sequence": email_sequence, "email_count": email_count,
                "error": None, "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input", error=msg, goal=goal)
        notifier.agent_error(ROLE, goal, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"goal": goal, "status": "permanent_failure", "error": msg})
        return {"email_sequence": "", "email_count": 0, "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, goal, msg)
        return {"email_sequence": "", "email_count": 0, "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, goal, msg)
        return {"email_sequence": "", "email_count": 0, "error": msg,
                "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(EmailSequenceState)
    g.add_node("email_architect", email_architect_node)
    g.set_entry_point("email_architect")
    g.add_edge("email_architect", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
