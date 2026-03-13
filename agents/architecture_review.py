"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : architecture_review
SKILL : Architecture Review — collect config files, synthesise quality report

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str), focus (str) — immutable after entry
  Outputs  : architecture_report (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram alert on error [non-fatal]

Thread Memory (checkpoint-scoped):
  All ArchitectureReviewState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. Retry is HTTP-level only (tenacity, transient errors).
  @langraph: do not add iterative refinement without an explicit budget + stop rule.

Failure Discrimination:
  PERMANENT  → ValueError (invalid repo), GithubException 404
               No retry. Returns error field. Graph continues.
  TRANSIENT  → GithubException 403/429/5xx, APIConnectionError, RateLimitError, APITimeoutError
               Tenacity retries up to MAX_RETRIES with exponential backoff.
  UNEXPECTED → Exception — logged, returned as error, graph does not crash.

Checkpoint Semantics:
  PRE  — Supabase log before Claude call (marks expensive operation started, enables replay diagnosis)
  POST — Supabase log after completion (records output size and status for observability)

Persona injected at runtime via personas/config.py — skill file contains no identity.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
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
from typing import TypedDict
from langgraph.graph import StateGraph, END

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE         = "architecture_review"
MAX_RETRIES  = 3
RETRY_MIN_S  = 3
RETRY_MAX_S  = 45
MAX_TOKENS   = 1200   # Architecture reports need depth but not 4k tokens
VALID_REVIEW_FOCUS = {"scalability", "security", "performance", "maintainability", "cost", "general"}

FILE_CHARS   = 2500   # Per-file truncation limit
FILE_LIMIT   = 6      # Max config files included in prompt

# Files to inspect — ordered by diagnostic value
ARCH_FILES = [
    "README.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "tsconfig.json",
    "next.config.ts",
    "next.config.js",
    "vite.config.ts",
    "Dockerfile",
    "docker-compose.yml",
    "setup.py",
]


# ── State schema ─────────────────────────────────────────────────────────────────
class ArchitectureReviewState(BaseState):
    # Inputs — written by caller, immutable inside this node
    repo_owner: str
    repo_name: str
    focus: str          # architecture | performance | patterns | dependencies | general
    # Outputs — written by this node, read by downstream nodes
    architecture_report: str  # structured report; empty string on failure
    # BaseState provides: workflow_id (thread ID), timestamp, agent, error


# ── Phase 1: Data collection (independently testable, no Claude dependency) ──────
def _collect_arch_files(gh: GitHubTools, owner: str, repo: str) -> dict:
    """
    Fetch config files + repo structure from GitHub API.
    Returns a dict of raw data. No analysis — pure collection.
    Stops collecting after FILE_LIMIT successful files to keep prompt tight.
    Separation allows unit testing without mocking Claude.
    """
    file_contents: dict[str, str] = {}
    for filepath in ARCH_FILES:
        if len(file_contents) >= FILE_LIMIT:
            break
        content = gh.get_file_contents(owner, repo, filepath)
        if "not found" not in content.lower():
            file_contents[filepath] = content[:FILE_CHARS]

    return {
        "files":     file_contents,
        "structure": gh.get_repo_structure(owner, repo),
        "languages": gh.get_languages(owner, repo),
        "meta":      gh.get_repo_meta(owner, repo),
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


def _build_prompt(data: dict, focus: str, persona: dict) -> str:
    """Format collected data into a structured prompt. Pure function — no I/O."""
    meta = data["meta"]

    files_section = "\n\n".join(
        f"━━━ {path} ━━━\n{content}" for path, content in data["files"].items()
    ) or "No config files found."

    langs_md = ", ".join(
        f"{lang}: {round(bytes_ / 1024, 1)}KB"
        for lang, bytes_ in sorted(data["languages"].items(), key=lambda x: -x[1])
    ) or "Unknown"

    return f"""{persona['personality']}

Review this codebase and return a structured architecture assessment with specific, implementable recommendations.
Be precise — reference file names and line patterns where relevant. No fluff. Max 500 words.

━━━ REPOSITORY: {meta['full_name']} ━━━
Description : {meta['description']}
Languages   : {langs_md}
Focus       : {focus}

DIRECTORY STRUCTURE:
{data['structure']}

CONFIG & MANIFEST FILES:
{files_section}

━━━ REVIEW FOCUS: {focus.upper()} ━━━

## Architecture Review: {meta['full_name']}

### Tech Stack Assessment
[Is the stack appropriate for the use case?]

### Strengths
[What is done well — be specific]

### Issues & Risks
[Specific problems with file references where possible]

### Recommendations (Priority Ordered)
1. [Most critical]
2. ...

### Quick Wins
[Changes achievable in under 1 hour with high impact]

### Verdict
[One paragraph summary with a quality score /10]"""


# ── Main node ─────────────────────────────────────────────────────────────────────
def architecture_review_node(state: ArchitectureReviewState) -> dict:
    """
    Architecture Review node — single pass, no loop.

    Execution order:
      1. Validate inputs
      2. Collect data (Phase 1 — GitHub API, stops at FILE_LIMIT)
      3. PRE checkpoint (before expensive Claude call)
      4. Review (Phase 2 — Claude)
      5. POST checkpoint (after completion)
      6. Return state patch

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

        # Phase 1 — collect (PERMANENT failure: bad repo → ValueError)
        raw = _collect_arch_files(gh, state["repo_owner"], state["repo_name"])

        # PRE checkpoint — mark expensive operation started for replay diagnosis
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "focus": focus, "status": "synthesising",
             "files_collected": len(raw["files"])},
        )

        # Phase 2 — review (TRANSIENT failures retried by tenacity)
        prompt = _build_prompt(raw, focus, persona)
        report = _review(claude, prompt, metrics)

        metrics.log()
        metrics.persist()

        # POST checkpoint — record completion and output size
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "focus": focus, "status": "completed",
             "report_chars": len(report)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, report_chars=len(report))
        return {"architecture_report": report, "error": None, "workflow_id": thread_id, "agent": ROLE}

    # ── PERMANENT failures — no retry, return cleanly ─────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_repo", error=msg, repo=repo_slug)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "invalid_repo", "error": msg})
        return {"architecture_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except GithubException as exc:
        msg = f"GitHub API error ({exc.status}): {(exc.data or {}).get('message', str(exc))}"
        log.error(f"{ROLE}.github_error", failure_mode="github_api", status=exc.status, error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        _checkpoint(f"{ROLE}_err_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                    {"repo": repo_slug, "status": "github_error", "error": msg})
        return {"architecture_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", failure_mode="claude_api", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"architecture_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures — log everything, never crash the graph ───────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"architecture_report": "", "error": msg, "workflow_id": thread_id, "agent": ROLE}


# ── Backwards-compatibility aliases ──────────────────────────────────────────────
sebastian_node = architecture_review_node
SebastianState = ArchitectureReviewState


# ── LangGraph wrapper ────────────────────────────────────────────────────────

def build_graph():
    """Compile this agent as a standalone LangGraph StateGraph."""
    g = StateGraph(ArchitectureReviewState)
    g.add_node("architecture_review", architecture_review_node)
    g.set_entry_point("architecture_review")
    g.add_edge("architecture_review", END)
    return g.compile()


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — builds graph and invokes."""
    graph = build_graph().compile()
    result = await graph.ainvoke(state)
    return result
