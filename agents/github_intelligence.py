"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : github_intelligence
SKILL : GitHub Intelligence — fetch, analyse, synthesise

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str), query (str) — immutable after entry
  Outputs  : intelligence (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All GitHubIntelState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (repo not found), GithubException 404
               No retry. Returns error field. Graph continues.
  TRANSIENT  → GithubException 403/429/5xx, APIConnectionError, RateLimitError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks expensive operation started, enables replay diagnosis)
  POST — Supabase log after completion (records output size and status for observability)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
import structlog
from github import GithubException
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.github_tools import GitHubTools
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger
from tools.telemetry import CallMetrics

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE         = "github_intelligence"
MAX_RETRIES  = 3
RETRY_MIN_S  = 3
RETRY_MAX_S  = 45
MAX_TOKENS   = 800
VALID_FOCUS   = {"security", "dependencies", "activity", "contributors", "issues", "general"}

COMMIT_LIMIT = 15
PR_LIMIT     = 10
ISSUE_LIMIT  = 10
README_CHARS = 2000


# ── State schema ─────────────────────────────────────────────────────────────────
class GitHubIntelState(BaseState):
    # Inputs — written by caller, immutable inside this node
    repo_owner: str
    repo_name: str
    query: str
    # Outputs — written by this node, read by downstream nodes
    intelligence: str   # synthesised report; empty string on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Transient error discriminator (@langraph: explicit, not catch-all) ───────────
def _is_transient_github(exc: Exception) -> bool:
    return isinstance(exc, GithubException) and exc.status in (403, 429, 500, 502, 503, 504)


# ── Phase 1: Data collection (independently testable, no Claude dependency) ──────
def _collect_data(gh: GitHubTools, owner: str, repo: str) -> dict:
    """
    Fetch all repo intelligence from GitHub API.
    Returns a dict of raw data. No analysis here — pure collection.
    Separation allows unit testing without mocking Claude.
    """
    return {
        "meta":      gh.get_repo_meta(owner, repo),
        "readme":    gh.get_file_contents(owner, repo, "README.md"),
        "structure": gh.get_repo_structure(owner, repo),
        "languages": gh.get_languages(owner, repo),
        "commits":   gh.list_commits(owner, repo, per_page=COMMIT_LIMIT),
        "prs":       gh.list_pull_requests(owner, repo, state="open", limit=PR_LIMIT),
        "issues":    gh.list_issues(owner, repo, state="open", limit=ISSUE_LIMIT),
    }


# ── Phase 2: Synthesis (Claude call, retried on transient errors only) ───────────
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


def _build_prompt(data: dict, query: str, persona: dict) -> str:
    """Format collected data into a structured prompt. Pure function — no I/O."""
    meta = data["meta"]

    commits_md = "\n".join(
        f"  [{c['sha']}] {c['date']} — {c['author']}: {c['message']}"
        for c in data["commits"]
    )
    prs_md = (
        "\n".join(
            f"  #{p['number']} [{p['author']}] {p['title']} (opened {p['created_at']})"
            for p in data["prs"]
        ) or "  No open pull requests."
    )
    issues_md = (
        "\n".join(
            f"  #{i['number']} [{', '.join(i['labels']) or 'unlabelled'}] {i['title']} ({i['comments']} comments)"
            for i in data["issues"]
        ) or "  No open issues."
    )
    langs_md = ", ".join(
        f"{lang}: {bytes_:,} bytes"
        for lang, bytes_ in sorted(data["languages"].items(), key=lambda x: -x[1])
    ) or "Unknown"

    return f"""{persona['personality']}

Analyse this GitHub repository and answer the query precisely.
Reference specific commits, files, and issues where relevant. No fluff. Be concise — max 400 words.

━━━ REPOSITORY: {meta['full_name']} ━━━
Description  : {meta['description']}
Language     : {meta['language']} | {langs_md}
Stars        : {meta['stars']} | Forks: {meta['forks']} | Open Issues: {meta['open_issues']}
Branch       : {meta['default_branch']} | Topics: {', '.join(meta['topics']) or 'none'}
Created      : {meta['created_at']} | Last updated: {meta['updated_at']}

━━━ README (first {README_CHARS} chars) ━━━
{data['readme'][:README_CHARS]}

━━━ DIRECTORY STRUCTURE ━━━
{data['structure']}

━━━ RECENT COMMITS (last {COMMIT_LIMIT}) ━━━
{commits_md}

━━━ OPEN PULL REQUESTS ━━━
{prs_md}

━━━ OPEN ISSUES ━━━
{issues_md}

━━━ QUERY ━━━
{query}

Provide your intelligence report (concise, max 400 words):"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def github_intelligence_node(state: GitHubIntelState) -> dict:
    """
    GitHub Intelligence node — single pass, no loop.

    Execution order:
      1. Validate inputs
      2. Collect data (Phase 1 — GitHub API)
      3. PRE checkpoint (before expensive Claude call)
      4. Synthesise (Phase 2 — Claude)
      5. POST checkpoint (after completion)
      6. Return state patch

    @langraph: show me the checkpoint before you call the checkpoint production-ready.
    """
    thread_id     = state.get("workflow_id") or str(uuid.uuid4())
    repo_slug     = f"{state['repo_owner']}/{state['repo_name']}"
    persona       = get_persona(ROLE)
    notifier      = TelegramNotifier()
    state_logger  = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, repo=repo_slug)

    try:
        gh     = GitHubTools()
        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # Phase 1 — collect (PERMANENT failure: bad repo → ValueError)
        raw = _collect_data(gh, state["repo_owner"], state["repo_name"])

        # PRE checkpoint — mark expensive operation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "query": state["query"], "status": "synthesising",
             "commits": len(raw["commits"]), "prs": len(raw["prs"]), "issues": len(raw["issues"])},
        )

        # Phase 2 — synthesise (TRANSIENT failures retried by tenacity)
        prompt       = _build_prompt(raw, state["query"], persona)
        intelligence = _synthesise(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion and output size
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "query": state["query"], "status": "completed",
             "intelligence_chars": len(intelligence)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, intelligence_chars=len(intelligence))
        return {"intelligence": intelligence, "error": None, "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_repo", error=msg, repo=repo_slug)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "invalid_repo", "error": msg})
        return {"intelligence": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except GithubException as exc:
        msg = f"GitHub API error ({exc.status}): {(exc.data or {}).get('message', str(exc))}"
        log.error(f"{ROLE}.github_error", failure_mode="github_api", status=exc.status, error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "github_error", "error": msg})
        return {"intelligence": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"intelligence": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"intelligence": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── Backwards-compatibility aliases ──────────────────────────────────────────────
hugh_node = github_intelligence_node
HugoState = GitHubIntelState
