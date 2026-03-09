"""
Skill: Security Audit
Role: security_audit

Performs security audits on GitHub repositories.
Checks for exposed secrets, vulnerable dependencies, insecure patterns,
and missing security hygiene.

Persona injected at runtime via personas/config.py.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from typing_extensions import TypedDict

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.github_tools import GitHubTools
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()
ROLE = "security_audit"

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


class SecurityAuditState(BaseState):
    repo_owner: str
    repo_name: str
    security_report: str
    risk_level: str  # LOW | MEDIUM | HIGH | CRITICAL


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=45),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _ask_claude(client: anthropic.Anthropic, prompt: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def security_audit_node(state: SecurityAuditState) -> dict:
    """
    Security Audit skill node.
    Fetches security-relevant files from GitHub and returns a risk-scored audit report.
    """
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    checkpoint_id = f"{ROLE}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    repo_slug = f"{state['repo_owner']}/{state['repo_name']}"
    persona = get_persona(ROLE)

    log.info(f"{ROLE}.started", workflow_id=workflow_id, repo=repo_slug)

    notifier = TelegramNotifier()
    state_logger = SupabaseStateLogger()

    try:
        gh = GitHubTools()
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        # Fetch security-relevant files
        file_contents: dict[str, str] = {}
        for filepath in AUDIT_FILES:
            content = gh.get_file_contents(state["repo_owner"], state["repo_name"], filepath)
            if "not found" not in content.lower():
                file_contents[filepath] = content[:2000]

        structure = gh.get_repo_structure(state["repo_owner"], state["repo_name"])
        commits = gh.list_commits(state["repo_owner"], state["repo_name"], per_page=10)
        recent_authors = list({c["author"] for c in commits})

        files_section = "\n\n".join(
            f"━━━ {path} ━━━\n{content}" for path, content in file_contents.items()
        ) or "No security-relevant files accessible."

        prompt = f"""{persona['personality']}

Perform a security audit on this repository. Be specific and actionable. Assign a risk level.

━━━ REPOSITORY: {repo_slug} ━━━
DIRECTORY STRUCTURE:
{structure}

RECENT CONTRIBUTORS:
{', '.join(recent_authors)}

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

        report = _ask_claude(claude, prompt)

        # Parse risk level from report
        risk_level = "MEDIUM"
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if f"Risk Level:** {level}" in report or f"**{level}**" in report:
                risk_level = level
                break

        if risk_level in ("HIGH", "CRITICAL"):
            notifier.alert(
                f"🔴 Security audit: <b>{risk_level}</b>\nRepo: <code>{repo_slug}</code>"
            )

        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id=checkpoint_id,
            agent=ROLE,
            state={"repo": repo_slug, "risk_level": risk_level, "status": "completed"},
        )

        log.info(f"{ROLE}.completed", risk_level=risk_level, repo=repo_slug)
        return {
            "security_report": report,
            "risk_level": risk_level,
            "error": None,
            "workflow_id": workflow_id,
        }

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.invalid_repo", error=msg)
        return {"security_report": "", "risk_level": "UNKNOWN", "error": msg}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected_error", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"security_report": "", "risk_level": "UNKNOWN", "error": msg}


# Backwards-compatibility alias
sam_node = security_audit_node
SamState = SecurityAuditState
