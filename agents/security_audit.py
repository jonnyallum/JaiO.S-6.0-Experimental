"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AGENT : security_audit
SKILL : Security Audit — fetch security files, risk-score, report

Node Contract (@langraph doctrine):
  Inputs   : repo_owner (str), repo_name (str) — immutable after entry
  Outputs  : security_report (str), risk_level (str), error (str|None), agent (str)
  Tools    : GitHubTools [read-only], Anthropic [read-only]
  Effects  : Supabase state log [non-fatal], Telegram HIGH/CRITICAL alert [non-fatal]

Thread Memory (checkpoint-scoped):
  All SecurityAuditState fields are thread-scoped only.
  No cross-thread writes. No long-term store updates.

Loop Policy:
  NONE — single-pass node. No iterative refinement.
  @langraph: a security audit is not improved by re-prompting. It is improved by better data.
  If re-scoring is needed, that is a separate quality_validation node, not a loop here.

Failure Discrimination:
  PERMANENT  → ValueError (repo not found) — no retry, return UNKNOWN risk.
  TRANSIENT  → GithubException 403/429/5xx, APIConnectionError, RateLimitError — tenacity retries.
  UNEXPECTED → Exception — logged, returned as UNKNOWN risk, graph does not crash.

Checkpoint Semantics:
  PRE  — before Claude call: records files fetched, enables replay diagnosis on timeout.
  POST — after completion: records risk_level and report size for observability.

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
from tools.github_tools import GitHubTools
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()

# ── Budget constants (@langraph: all limits named, never magic numbers) ──────────
ROLE        = "security_audit"
MAX_RETRIES = 3
RETRY_MIN_S = 3
RETRY_MAX_S = 45
MAX_TOKENS  = 1500          # Security reports need more depth than intel summaries
FILE_CHARS  = 2000          # Max chars per file fetched

# Files that reveal security posture — ordered by signal value
AUDIT_FILES = [
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    "SECURITY.md",
    ".github/workflows",
]

RISK_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


# ── State schema ─────────────────────────────────────────────────────────────────
class SecurityAuditState(BaseState):
    # Inputs — written by caller, immutable inside this node
    repo_owner: str
    repo_name: str
    # Outputs — written by this node, read by downstream nodes
    security_report: str   # full audit report; empty string on failure
    risk_level: str        # LOW | MEDIUM | HIGH | CRITICAL | UNKNOWN


# ── Phase 1: Data collection (independently testable, no Claude dependency) ──────
def _collect_security_files(gh: GitHubTools, owner: str, repo: str) -> dict:
    """
    Fetch security-relevant files and repo metadata.
    Returns raw dict. No analysis — pure collection.
    Only includes files that exist (skips 404s silently).
    """
    file_contents: dict[str, str] = {}
    for filepath in AUDIT_FILES:
        content = gh.get_file_contents(owner, repo, filepath)
        if "not found" not in content.lower():
            file_contents[filepath] = content[:FILE_CHARS]

    commits = gh.list_commits(owner, repo, per_page=10)
    return {
        "structure":      gh.get_repo_structure(owner, repo),
        "file_contents":  file_contents,
        "recent_authors": list({c["author"] for c in commits}),
    }


# ── Phase 2: Risk scoring (Claude call, retried on transient errors only) ────────
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=RETRY_MIN_S, max=RETRY_MAX_S),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _score_risk(client: anthropic.Anthropic, prompt: str) -> str:
    """Single Claude call for risk scoring. Retried on transient API errors only."""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _build_prompt(repo_slug: str, data: dict, persona: dict) -> str:
    """Format collected security data into a structured audit prompt. Pure function — no I/O."""
    files_section = "\n\n".join(
        f"━━━ {path} ━━━\n{content}" for path, content in data["file_contents"].items()
    ) or "No security-relevant files accessible."

    return f"""{persona['personality']}

Perform a security audit on this repository. Be specific and actionable. Assign a risk level.

━━━ REPOSITORY: {repo_slug} ━━━
DIRECTORY STRUCTURE:
{data['structure']}

RECENT CONTRIBUTORS:
{', '.join(data['recent_authors'])}

SECURITY-RELEVANT FILES:
{files_section}

━━━ AUDIT CHECKLIST ━━━
Evaluate each and report findings:
1. Exposed Secrets — API keys, tokens, passwords in code or config
2. Dependency Vulnerabilities — outdated packages with known CVEs
3. Security Files — .gitignore, SECURITY.md, .env.example present and correct?
4. Docker/Container — base image pinning, non-root user, secret handling
5. Access Control — hardcoded credentials, insufficient auth patterns
6. Code Hygiene — eval(), exec(), SQL concatenation, XSS vectors
7. CI/CD Security — secrets in workflow files, dependency pinning

━━━ REQUIRED OUTPUT FORMAT ━━━
## Security Audit: {repo_slug}
**Risk Level:** [LOW | MEDIUM | HIGH | CRITICAL]

### Findings
[Specific findings per category, with file references]

### Recommended Actions
[Numbered, most critical first]

### Verdict
[1-2 sentence summary]"""


def _parse_risk_level(report: str) -> str:
    """Extract risk level from report text. Returns MEDIUM as safe default."""
    for level in RISK_LEVELS:
        if f"Risk Level:** {level}" in report or f"**{level}**" in report:
            return level
    return "MEDIUM"


# ── Main node ─────────────────────────────────────────────────────────────────────
def security_audit_node(state: SecurityAuditState) -> dict:
    """
    Security Audit node — single pass, no loop.

    Execution order:
      1. Collect security files (Phase 1 — GitHub API)
      2. PRE checkpoint (before expensive Claude call)
      3. Score risk (Phase 2 — Claude)
      4. Parse risk level from report
      5. Alert on HIGH/CRITICAL (non-fatal)
      6. POST checkpoint (after completion)
      7. Return state patch

    @langraph: a loop here would be "infinite jazz" — re-scoring the same
    files produces diminishing returns. Quality gate belongs in quality_validation.
    """
    thread_id    = state.get("workflow_id") or str(uuid.uuid4())
    repo_slug    = f"{state['repo_owner']}/{state['repo_name']}"
    persona      = get_persona(ROLE)
    notifier     = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    def _checkpoint(checkpoint_id: str, payload: dict) -> None:
        state_logger.log_state(thread_id, checkpoint_id, ROLE, payload)

    log.info(f"{ROLE}.started", thread_id=thread_id, repo=repo_slug)

    try:
        gh     = GitHubTools()
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        # Phase 1 — collect (PERMANENT failure: bad repo → ValueError)
        raw = _collect_security_files(gh, state["repo_owner"], state["repo_name"])

        # PRE checkpoint — mark expensive operation started, record what was fetched
        _checkpoint(
            f"{ROLE}_pre_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "status": "scoring",
             "files_fetched": list(raw["file_contents"].keys())},
        )

        # Phase 2 — score risk (TRANSIENT failures retried by tenacity)
        prompt     = _build_prompt(repo_slug, raw, persona)
        report     = _score_risk(claude, prompt)
        risk_level = _parse_risk_level(report)

        # Alert on high severity — non-fatal, never blocks return
        if risk_level in ("HIGH", "CRITICAL"):
            notifier.alert(
                f"🔴 Security audit: <b>{risk_level}</b>\nRepo: <code>{repo_slug}</code>"
            )

        # POST checkpoint — record outcome for observability
        _checkpoint(
            f"{ROLE}_post_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            {"repo": repo_slug, "risk_level": risk_level, "status": "completed",
             "report_chars": len(report)},
        )

        log.info(f"{ROLE}.completed", thread_id=thread_id, risk_level=risk_level)
        return {
            "security_report": report,
            "risk_level":      risk_level,
            "error":           None,
            "workflow_id":     thread_id,
            "agent":           ROLE,
        }

    # ── PERMANENT failures ────────────────────────────────────────────────────────
    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.permanent_failure", failure_mode="invalid_repo", error=msg)
        return {"security_report": "", "risk_level": "UNKNOWN", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}

    # ── UNEXPECTED failures ───────────────────────────────────────────────────────
    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected", failure_mode="unexpected", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"security_report": "", "risk_level": "UNKNOWN", "error": msg,
                "workflow_id": thread_id, "agent": ROLE}


# ── Backwards-compatibility aliases ──────────────────────────────────────────────
sam_node = security_audit_node
SamState = SecurityAuditState
