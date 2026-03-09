"""
Skill: Architecture Review
Role: architecture_review

Reviews codebases for architectural quality, tech stack decisions,
and engineering best practices. Returns a structured review with
concrete recommendations.

Persona injected at runtime via personas/config.py.
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
from typing_extensions import TypedDict

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.github_tools import GitHubTools
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()
ROLE = "architecture_review"

ARCH_FILES = [
    "README.md",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "tsconfig.json",
    "next.config.js",
    "next.config.ts",
    "vite.config.ts",
    "Dockerfile",
    "docker-compose.yml",
    "setup.py",
]


class ArchitectureReviewState(BaseState):
    repo_owner: str
    repo_name: str
    focus: str           # architecture | performance | patterns | dependencies | general
    architecture_report: str


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


def architecture_review_node(state: ArchitectureReviewState) -> dict:
    """
    Architecture Review skill node.
    Fetches config files and repo structure, returns a structured architecture report.
    """
    workflow_id = state.get("workflow_id") or str(uuid.uuid4())
    checkpoint_id = f"{ROLE}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    repo_slug = f"{state['repo_owner']}/{state['repo_name']}"
    focus = state.get("focus", "general")
    persona = get_persona(ROLE)

    log.info(f"{ROLE}.started", workflow_id=workflow_id, repo=repo_slug, focus=focus)

    state_logger = SupabaseStateLogger()

    try:
        gh = GitHubTools()
        claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        file_contents: dict[str, str] = {}
        for filepath in ARCH_FILES:
            content = gh.get_file_contents(state["repo_owner"], state["repo_name"], filepath)
            if "not found" not in content.lower():
                file_contents[filepath] = content[:3000]

        structure = gh.get_repo_structure(state["repo_owner"], state["repo_name"])
        languages = gh.get_languages(state["repo_owner"], state["repo_name"])
        meta = gh.get_repo_meta(state["repo_owner"], state["repo_name"])

        files_section = "\n\n".join(
            f"━━━ {path} ━━━\n{content}" for path, content in file_contents.items()
        ) or "No config files found."

        langs_md = ", ".join(
            f"{lang}: {round(bytes_ / 1024, 1)}KB"
            for lang, bytes_ in sorted(languages.items(), key=lambda x: -x[1])
        )

        prompt = f"""{persona['personality']}

Review this codebase and provide an architecture assessment with specific, implementable recommendations.

━━━ REPOSITORY: {meta['full_name']} ━━━
Description : {meta['description']}
Languages   : {langs_md}
Focus       : {focus}

DIRECTORY STRUCTURE:
{structure}

CONFIG & MANIFEST FILES:
{files_section}

━━━ REVIEW FOCUS: {focus.upper()} ━━━

## Architecture Review: {repo_slug}

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

        report = _ask_claude(claude, prompt)

        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id=checkpoint_id,
            agent=ROLE,
            state={"repo": repo_slug, "focus": focus, "status": "completed", "report_chars": len(report)},
        )

        log.info(f"{ROLE}.completed", repo=repo_slug, report_chars=len(report))
        return {"architecture_report": report, "error": None, "workflow_id": workflow_id}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.invalid_repo", error=msg)
        return {"architecture_report": "", "error": msg}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected_error", error=msg)
        return {"architecture_report": "", "error": msg}


# Backwards-compatibility aliases
sebastian_node = architecture_review_node
SebastianState = ArchitectureReviewState
