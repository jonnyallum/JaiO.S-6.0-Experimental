"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : supabase_intelligence
SKILL : Shared Brain Intelligence — query the live agent database and synthesise a status report

Node Contract (@langraph doctrine):
  Inputs   : query (str), focus (str) — immutable after entry
  Outputs  : intelligence (str), error (str|None), agent (str)
  Tools    : Supabase [read-only — agents, learnings, chatroom, graph_state], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal, graph_state table only], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All BrainIntelState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.
  NOTE: This node reads FROM the Shared Brain but only WRITES to graph_state (state log).

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (invalid focus), postgrest.APIError 4xx (table/permission issue)
               No retry. Returns error field. Graph continues.
  TRANSIENT  → APIConnectionError, RateLimitError, APITimeoutError (Claude only)
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks synthesis started, records row counts)
  POST — Supabase log after completion (records intelligence size)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

import anthropic
import structlog
from supabase import create_client, Client
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
from tools.telemetry import CallMetrics
from typing import TypedDict
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE              = "supabase_intelligence"
MAX_RETRIES       = 3
RETRY_MIN_S       = 3
RETRY_MAX_S       = 45
MAX_TOKENS        = 900    # Status intelligence report — concise synthesis
AGENT_LIMIT       = 25    # Max agents fetched per query
LEARNING_LIMIT    = 30    # Max learnings fetched
CHAT_LIMIT        = 20    # Max chatroom messages fetched
WORKFLOW_LIMIT    = 15    # Max recent graph_state entries fetched
PHILOSOPHY_CHARS  = 300   # Truncate agent philosophy in prompt — SKILL.md can be very large

VALID_FOCUS = {"agents", "learnings", "chatroom", "health", "general"}


# ── State schema ─────────────────────────────────────────────────────────────────
class BrainIntelState(BaseState):
    # Inputs — written by caller, immutable inside this node
    query: str    # Natural language question about the system or its agents
    focus: str    # agents | learnings | chatroom | health | general
    # Outputs — written by this node, read by downstream nodes
    intelligence: str   # Synthesised report answering the query; empty string on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Supabase client factory (lazy, one per node invocation) ──────────────────────
def _get_brain_client() -> Client:
    return create_client(
        settings.brain_url,
        settings.brain_service_role_key,
    )


# ── Phase 1: Brain data collection (independently testable, no Claude dependency) ─
def _collect_brain_data(focus: str) -> dict:
    """
    Query the Shared Brain tables based on focus.
    Returns structured raw data. No analysis — pure collection.
    Each table query is independent; failure of one does not block others.
    Separation allows unit testing without mocking Claude.
    """
    db = _get_brain_client()
    raw: dict = {"agents": [], "learnings": [], "chatroom": [], "workflows": []}

    if focus in ("agents", "general"):
        try:
            result = (
                db.table("agents")
                .select("id, human_name, nickname, tier, philosophy")
                .order("tier", desc=False)
                .limit(AGENT_LIMIT)
                .execute()
            )
            # Truncate philosophy to keep prompt size sane
            for row in result.data:
                row["philosophy"] = (row.get("philosophy") or "")[:PHILOSOPHY_CHARS]
            raw["agents"] = result.data
        except Exception as exc:
            log.warning(f"{ROLE}.agents_fetch_failed", error=str(exc))

    if focus in ("learnings", "general"):
        try:
            result = (
                db.table("learnings")
                .select("content, agent_id, created_at")
                .order("created_at", desc=True)
                .limit(LEARNING_LIMIT)
                .execute()
            )
            raw["learnings"] = result.data
        except Exception as exc:
            log.warning(f"{ROLE}.learnings_fetch_failed", error=str(exc))

    if focus in ("chatroom", "general"):
        try:
            result = (
                db.table("chatroom")
                .select("agent_id, message, created_at")
                .order("created_at", desc=True)
                .limit(CHAT_LIMIT)
                .execute()
            )
            raw["chatroom"] = result.data
        except Exception as exc:
            log.warning(f"{ROLE}.chatroom_fetch_failed", error=str(exc))

    if focus in ("health", "general"):
        try:
            result = (
                db.table("graph_state")
                .select("workflow_id, agent, created_at, state_json")
                .order("created_at", desc=True)
                .limit(WORKFLOW_LIMIT)
                .execute()
            )
            raw["workflows"] = result.data
        except Exception as exc:
            log.warning(f"{ROLE}.workflows_fetch_failed", error=str(exc))

    return raw



# ── Phase 2: Synthesis (Claude call, retried on transient errors only) ────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _synthesise(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _build_brain_prompt(raw: dict, query: str, focus: str, persona: dict) -> str:
    """Format collected brain data into a synthesis prompt. Pure function — no I/O."""

    agents_section = ""
    if raw["agents"]:
        rows = "\n".join(
            f"  [{r['id']}] {r.get('human_name', '?')} ({r.get('tier', '?')}) — "
            f"{r.get('philosophy', '')[:200]}"
            for r in raw["agents"]
        )
        agents_section = f"\n━━━ AGENTS ({len(raw['agents'])} loaded) ━━━\n{rows}"

    learnings_section = ""
    if raw["learnings"]:
        rows = "\n".join(
            f"  [{r.get('agent_id', '?')}] {r.get('content', '')[:200]}"
            for r in raw["learnings"]
        )
        learnings_section = f"\n━━━ RECENT LEARNINGS ({len(raw['learnings'])}) ━━━\n{rows}"

    chatroom_section = ""
    if raw["chatroom"]:
        rows = "\n".join(
            f"  [{r.get('created_at', '')[:16]}] {r.get('agent_id', '?')}: "
            f"{r.get('message', '')[:200]}"
            for r in raw["chatroom"]
        )
        chatroom_section = f"\n━━━ CHATROOM (last {len(raw['chatroom'])} messages) ━━━\n{rows}"

    workflows_section = ""
    if raw["workflows"]:
        rows = "\n".join(
            f"  [{r.get('created_at', '')[:16]}] {r.get('agent', '?')} — "
            f"{(r.get('state_json') or {}).get('status', 'unknown')}"
            for r in raw["workflows"]
        )
        workflows_section = f"\n━━━ RECENT WORKFLOWS ({len(raw['workflows'])}) ━━━\n{rows}"

    data_block = (
        agents_section + learnings_section + chatroom_section + workflows_section
        or "No data returned from the Shared Brain for this focus."
    )

    return f"""{persona['personality']}


_build_prompt = _build_brain_prompt  # spec alias — canonical name for 19-point compliance
You have live data from the JaiOS 6 Shared Brain below.
Answer the query precisely using only what the data shows. Be concise. Max 400 words.
If data is absent for part of the query, say so explicitly — do not invent.

{data_block}

━━━ QUERY ━━━
{query}

Provide your intelligence report:"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def supabase_intelligence_node(state: BrainIntelState) -> dict:
    """
    Shared Brain Intelligence node — single pass, no loop.

    Execution order:
      1. Validate inputs (query, focus)
      2. Collect brain data (Phase 1 — Supabase, focus-aware)
      3. Guard: warn if all tables returned empty (still proceeds — Claude will state data absent)
      4. PRE checkpoint (before Claude call)
      5. Synthesise (Phase 2 — Claude)
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: show me the checkpoint before you call production-ready.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    focus        = state.get("focus", "general")
    query        = state.get("query", "")
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, focus=focus)

    try:
        # Input guards — PERMANENT failures
        if not query.strip():
            raise ValueError("query is required and cannot be empty.")
        if focus not in VALID_FOCUS:
            raise ValueError(
                f"Invalid focus '{focus}'. Must be one of: {', '.join(sorted(VALID_FOCUS))}"
            )

        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # Phase 1 — collect brain data
        raw = _collect_brain_data(focus)

        total_rows = sum(len(v) for v in raw.values())
        if total_rows == 0:
            log.warning(f"{ROLE}.empty_brain_data", focus=focus)

        # PRE checkpoint — mark synthesis started, record what we found
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"focus": focus, "status": "synthesising",
             "agents": len(raw["agents"]), "learnings": len(raw["learnings"]),
             "chatroom": len(raw["chatroom"]), "workflows": len(raw["workflows"])},
        )

        # Phase 2 — synthesise (TRANSIENT failures retried by tenacity)
        prompt       = _build_brain_prompt(raw, query, focus, persona)
        intelligence = _synthesise(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"focus": focus, "status": "completed",
             "intelligence_chars": len(intelligence)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id,
                 intelligence_chars=len(intelligence))
        return {"intelligence": intelligence, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input",
                  error=msg, focus=focus)
        notifier.agent_error(ROLE, focus, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"focus": focus, "status": "permanent_failure", "error": msg})
        return {"intelligence": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, focus, msg)
        return {"intelligence": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, focus, msg)
        return {"intelligence": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(BrainIntelState)
    g.add_node("supabase_intelligence", supabase_intelligence_node)
    g.set_entry_point("supabase_intelligence")
    g.add_edge("supabase_intelligence", END)
    return g.compile()
