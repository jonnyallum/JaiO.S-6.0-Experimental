"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : dependency_audit
SKILL : Dependency Audit — collect package manifests, identify risks, produce upgrade plan

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str), focus (str) — immutable after entry
  Outputs  : dependency_report (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All DependencyAuditState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (repo not found), GithubException 404, no manifests found
               No retry. Returns error field. Graph continues.
  TRANSIENT  → GithubException 403/429/5xx, APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks expensive operation started, enables replay diagnosis)
  POST — Supabase log after completion (records manifests found, report size)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import uuid
from datetime import datetime, timezone

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
ROLE         = "dependency_audit"
MAX_RETRIES  = 3
RETRY_MIN_S  = 3
RETRY_MAX_S  = 45
MAX_TOKENS   = 1200   # Audit report — structured table + recommendations
FILE_CHARS   = 4000   # Per-manifest truncation (package.json can be large)
FILE_LIMIT   = 4      # Max manifests included in a single prompt

# Manifest files ordered by diagnostic value — stop collecting after FILE_LIMIT found
MANIFEST_FILES = [
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "go.mod",
    "Gemfile",
    "Cargo.toml",
    "setup.py",
    "package-lock.json",   # included to detect lockfile presence/absence
    "poetry.lock",
    "Pipfile.lock",
]

# Lockfile names — presence matters, content is not included in prompt
LOCKFILES = {"package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock"}


# ── State schema ─────────────────────────────────────────────────────────────────
class DependencyAuditState(BaseState):
    # Inputs — written by caller, immutable inside this node
    repo_owner: str
    repo_name: str
    focus: str       # security | outdated | licences | general
    # Outputs — written by this node, read by downstream nodes
    dependency_report: str   # structured audit report; empty string on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: Manifest collection (independently testable, no Claude dependency) ──
def _collect_manifests(gh: GitHubTools, owner: str, repo: str) -> dict:
    """
    Fetch package manifest files from the repo root.
    Returns dict with manifest contents and lockfile presence flags.
    Stops collecting after FILE_LIMIT non-lockfile manifests to keep prompt tight.
    Separation allows unit testing without mocking Claude.
    """
    manifests: dict[str, str] = {}
    lockfiles_found: list[str] = []

    for filename in MANIFEST_FILES:
        content = gh.get_file_contents(owner, repo, filename)
        found = "not found" not in content.lower()

        if filename in LOCKFILES:
            if found:
                lockfiles_found.append(filename)
            continue  # lockfile content not included in prompt — only presence

        if found and len(manifests) < FILE_LIMIT:
            manifests[filename] = content[:FILE_CHARS]

    return {
        "manifests":       manifests,
        "lockfiles_found": lockfiles_found,
        "meta":            gh.get_repo_meta(owner, repo),
        "languages":       gh.get_languages(owner, repo),
    }


# ── Phase 2: Audit (Claude call, retried on transient errors only) ───────────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _audit(client: anthropic.Anthropic, prompt: str, metrics: "CallMetrics") -> str:
    """Single Claude call with explicit token budget. Retried on transient API errors only."""
    metrics.start()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    metrics.record(response)
    return response.content[0].text


def _build_audit_prompt(data: dict, focus: str, persona: dict) -> str:
    """Format manifest data into a structured audit prompt. Pure function — no I/O."""
    meta = data["meta"]

    manifests_section = "\n\n".join(
        f"━━━ {name} ━━━\n{content}" for name, content in data["manifests"].items()
    ) or "No package manifests found in repo root."

    lockfile_status = (
        f"Lockfiles present: {', '.join(data['lockfiles_found'])}"
        if data["lockfiles_found"]
        else "⚠️  No lockfiles detected — dependency versions are unpinned."
    )

    langs_md = ", ".join(
        f"{lang}: {round(bytes_ / 1024, 1)}KB"
        for lang, bytes_ in sorted(data["languages"].items(), key=lambda x: -x[1])
    ) or "Unknown"

    return f"""{persona['personality']}

Audit the dependencies of this repository and produce a structured report with specific, actionable recommendations.
Focus area: {focus.upper()}. Be precise — reference package names and versions. No fluff. Max 450 words.

━━━ REPOSITORY: {meta['full_name']} ━━━
Languages : {langs_md}
{lockfile_status}

PACKAGE MANIFESTS:
{manifests_section}

━━━ AUDIT FOCUS: {focus.upper()} ━━━

## Dependency Audit: {meta['full_name']}

### Dependency Overview
| Ecosystem | Manifest | Direct Deps | Notes |
|---|---|---|---|
[Fill one row per manifest found]

### Risk Flags
[List packages that are: significantly outdated, have known CVE patterns, use deprecated APIs, or are abandoned]

### Lockfile Assessment
[Is dependency resolution deterministic? Any pinning issues?]

### Recommendations (Priority Ordered)
1. [Most critical upgrade or fix]
2. ...

### Quick Wins
[Under 15 minutes: upgrades or config changes with immediate security/stability benefit]

### Verdict
[One paragraph — overall dependency health score /10 and key action]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def dependency_audit_node(state: DependencyAuditState) -> dict:
    """
    Dependency Audit node — single pass, no loop.

    Execution order:
      1. Validate inputs
      2. Collect manifests (Phase 1 — GitHub API, stops at FILE_LIMIT)
      3. Guard: raise ValueError if no manifests found (PERMANENT)
      4. PRE checkpoint (before Claude call)
      5. Audit (Phase 2 — Claude)
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: show me the checkpoint before you call production-ready.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    repo_slug    = f"{state['repo_owner']}/{state['repo_name']}"
    focus        = state.get("focus", "general")
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, repo=repo_slug, focus=focus)

    try:
        gh     = GitHubTools()
        claude   = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        metrics  = CallMetrics(thread_id, ROLE)

        # Phase 1 — collect manifests (PERMANENT failure: bad repo → ValueError)
        raw = _collect_manifests(gh, state["repo_owner"], state["repo_name"])

        # Guard — no manifests found is a PERMANENT failure (nothing to audit)
        if not raw["manifests"]:
            raise ValueError(
                f"No package manifests found in {repo_slug}. "
                f"Checked: {', '.join(MANIFEST_FILES[:8])}."
            )

        # PRE checkpoint — mark expensive operation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "focus": focus, "status": "auditing",
             "manifests_found": list(raw["manifests"].keys()),
             "lockfiles_found": raw["lockfiles_found"]},
        )

        # Phase 2 — audit (TRANSIENT failures retried by tenacity)
        prompt = _build_audit_prompt(raw, focus, persona)
        report = _audit(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion and output size
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "focus": focus, "status": "completed",
             "report_chars": len(report)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, report_chars=len(report))
        return {"dependency_report": report, "error": None,
                "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="no_manifests_or_invalid_repo",
                  error=msg, repo=repo_slug)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "permanent_failure", "error": msg})
        return {"dependency_report": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except GithubException as exc:
        msg = f"GitHub API error ({exc.status}): {(exc.data or {}).get('message', str(exc))}"
        log.error(f"{ROLE}.github_error", failure_mode="github_api",
                  status=exc.status, error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "github_error", "error": msg})
        return {"dependency_report": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"dependency_report": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"dependency_report": "", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}
