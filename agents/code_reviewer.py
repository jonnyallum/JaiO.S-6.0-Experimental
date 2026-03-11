"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : code_reviewer
SKILL : Code Review — fetch specific files, produce file-level review with line-referenced findings

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str), file_paths (list[str]), focus (str) — immutable after entry
  Outputs  : code_review (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All CodeReviewState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (repo not found, no readable files), GithubException 404
               No retry. Returns error field. Graph continues.
  TRANSIENT  → GithubException 403/429/5xx, APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks expensive operation started, enables replay diagnosis)
  POST — Supabase log after completion (records files reviewed, report size)

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
ROLE         = "code_reviewer"
MAX_RETRIES  = 3
RETRY_MIN_S  = 3
RETRY_MAX_S  = 45
MAX_TOKENS   = 1400   # File-level reviews need enough depth for line references
VALID_FOCUS   = {"security", "performance", "readability", "testing", "general"}

FILE_CHARS   = 3500   # Per-file truncation — keeps prompt under context limits
FILE_LIMIT   = 5      # Max files reviewed in one pass


# ── State schema ─────────────────────────────────────────────────────────────────
class CodeReviewState(BaseState):
    # Inputs — written by caller, immutable inside this node
    repo_owner: str
    repo_name: str
    file_paths: list      # Specific file paths to review, e.g. ["src/api/auth.ts", "lib/db.py"]
    focus: str            # bugs | security | style | performance | general
    # Outputs — written by this node, read by downstream nodes
    code_review: str      # structured review with findings; empty string on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: File collection (independently testable, no Claude dependency) ──────
def _collect_files(gh: GitHubTools, owner: str, repo: str, paths: list) -> dict:
    """
    Fetch the requested source files from the repo.
    Caps at FILE_LIMIT files, skips files that return not-found.
    Returns dict with file contents and repo metadata for context.
    Separation allows unit testing without mocking Claude.
    """
    files: dict[str, str] = {}
    skipped: list[str] = []

    for path in paths[:FILE_LIMIT]:
        content = gh.get_file_contents(owner, repo, path)
        if "not found" not in content.lower():
            files[path] = content[:FILE_CHARS]
        else:
            skipped.append(path)

    return {
        "files":   files,
        "skipped": skipped,
        "meta":    gh.get_repo_meta(owner, repo),
    }



# ── Phase 2: Review (Claude call, retried on transient errors only) ──────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _review(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _build_review_prompt(data: dict, focus: str, persona: dict) -> str:
    """Format collected files into a structured review prompt. Pure function — no I/O."""
    meta = data["meta"]

    files_section = "\n\n".join(
        f"━━━ {path} ━━━\n{content}" for path, content in data["files"].items()
    )

    skipped_note = (
        f"\n⚠️  Skipped (not found): {', '.join(data['skipped'])}"
        if data["skipped"] else ""
    )

    return f"""{persona['personality']}


_build_prompt = _build_review_prompt  # spec alias — canonical name for 19-point compliance
Review the following source files and produce a precise, line-referenced code review.
Focus: {focus.upper()}. Reference specific line numbers and function names. No fluff. Max 500 words.

━━━ REPOSITORY: {meta['full_name']} ━━━
Stack     : {meta['language']}
Focus     : {focus}{skipped_note}

SOURCE FILES:
{files_section}

━━━ REVIEW FOCUS: {focus.upper()} ━━━

## Code Review: {meta['full_name']}

### Files Reviewed
[List each file and one-line summary of its purpose]

### Findings
[Numbered list — each finding must include: file name, approximate line range, severity (CRITICAL/HIGH/MEDIUM/LOW), description, and recommended fix]

### Positive Patterns
[What is done well — be specific, no generic praise]

### Recommendations (Priority Ordered)
1. [Most critical change]
2. ...

### Verdict
[One paragraph — overall code quality score /10 and the single most important action to take]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def code_reviewer_node(state: CodeReviewState) -> dict:
    """
    Code Review node — single pass, no loop.

    Execution order:
      1. Validate inputs (at least one file path provided)
      2. Collect files (Phase 1 — GitHub API, caps at FILE_LIMIT)
      3. Guard: raise ValueError if no readable files found (PERMANENT)
      4. PRE checkpoint (before Claude call)
      5. Review (Phase 2 — Claude)
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: show me the checkpoint before you call production-ready.
    """
    thread_id  = state.get("workflow_id") or str(uuid.uuid4())
    repo_slug  = f"{state['repo_owner']}/{state['repo_name']}"
    focus      = state.get("focus", "general")
    file_paths = state.get("file_paths") or []
    persona    = get_persona(ROLE)
    notifier   = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, repo=repo_slug,
             files=len(file_paths), focus=focus)

    try:
        # Input guard — no file paths is a PERMANENT failure (nothing to review)
        if not file_paths:
            raise ValueError(
                f"No file_paths provided for {repo_slug}. "
                "Pass at least one file path to review."
            )

        gh     = GitHubTools()
        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # Phase 1 — collect files (PERMANENT failure: bad repo → ValueError)
        raw = _collect_files(gh, state["repo_owner"], state["repo_name"], file_paths)

        # Guard — no readable files found
        if not raw["files"]:
            raise ValueError(
                f"None of the requested files were found in {repo_slug}: "
                f"{', '.join(file_paths[:FILE_LIMIT])}"
            )

        # PRE checkpoint — mark expensive operation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "focus": focus, "status": "reviewing",
             "files_found": list(raw["files"].keys()),
             "files_skipped": raw["skipped"]},
        )

        # Phase 2 — review (TRANSIENT failures retried by tenacity)
        prompt = _build_review_prompt(raw, focus, persona)
        review = _review(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion and output size
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "focus": focus, "status": "completed",
             "files_reviewed": len(raw["files"]),
             "review_chars": len(review)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id,
                 files_reviewed=len(raw["files"]), review_chars=len(review))
        return {"code_review": review, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_input",
                  error=msg, repo=repo_slug)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "permanent_failure", "error": msg})
        return {"code_review": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except GithubException as exc:
        msg = f"GitHub API error ({exc.status}): {(exc.data or {}).get('message', str(exc))}"
        log.error(f"{ROLE}.github_error", failure_mode="github_api",
                  status=exc.status, error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "github_error", "error": msg})
        return {"code_review": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"code_review": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"code_review": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}
