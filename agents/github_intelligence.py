"""
Skill: GitHub Intelligence
Role: github_intelligence

Fetches real GitHub data (README, structure, commits, PRs, issues)
and uses Claude Sonnet to synthesise actionable intelligence.

Persona (name/nickname/personality) is injected at runtime via personas/config.py.
This file contains only the skill — never the identity.
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
from typing_extensions import TypedDict

from config.settings import settings
from personas.config import get_persona
from state.base import BaseState
from tools.github_tools import GitHubTools
from tools.notification_tools import TelegramNotifier
from tools.supabase_tools import SupabaseStateLogger

log = structlog.get_logger()
ROLE = "github_intelligence"


class GitHubIntelState(BaseState):
    repo_owner: str
    repo_name: str
    query: str
    intelligence: str


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=45),
    retry=retry_if_exception_type(
        (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError)
    ),
    reraise=True,
)
def _ask_claude(client: anthropic.Anthropic, prompt: str, max_tokens: int = 800) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def github_intelligence_node(state: GitHubIntelState) -> dict:
    """
    GitHub Intelligence skill node.
    Fetches repo data from GitHub and returns a Claude-synthesised intelligence report.
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

        # ── Gather all repo intelligence ──────────────────────────────────────────────
        meta = gh.get_repo_meta(state["repo_owner"], state["repo_name"])
        readme = gh.get_file_contents(state["repo_owner"], state["repo_name"], "README.md")
        structure = gh.get_repo_structure(state["repo_owner"], state["repo_name"])
        languages = gh.get_languages(state["repo_owner"], state["repo_name"])
        commits = gh.list_commits(state["repo_owner"], state["repo_name"], per_page=15)
        prs = gh.list_pull_requests(state["repo_owner"], state["repo_name"], state="open")
        issues = gh.list_issues(state["repo_owner"], state["repo_name"], state="open")

        # ── Format for prompt ────────────────────────────────────────────────────────
        # Note: list_commits does NOT include additions/deletions (avoids commit.stats HTTP calls)
        commits_md = "\n".join(
            f"  [{c['sha']}] {c['date']} — {c['author']}: {c['message']}"
            for c in commits
        )
        # Note: list_pull_requests does NOT include files_changed/additions/deletions
        prs_md = (
            "\n".join(
                f"  #{p['number']} [{p['author']}] {p['title']} (opened {p['created_at']})"
                for p in prs
            )
            or "  No open pull requests."
        )
        issues_md = (
            "\n".join(
                f"  #{i['number']} [{', '.join(i['labels']) or 'unlabelled'}] {i['title']} ({i['comments']} comments)"
                for i in issues
            )
            or "  No open issues."
        )
        langs_md = ", ".join(
            f"{lang}: {bytes_:,} bytes"
            for lang, bytes_ in sorted(languages.items(), key=lambda x: -x[1])
        ) or "Unknown"

        prompt = f"""{persona['personality']}

Analyse this GitHub repository and answer the query precisely.
Reference specific commits, files, and issues where relevant. No fluff. Be concise — max 400 words.

━━━ REPOSITORY: {meta['full_name']} ━━━
Description  : {meta['description']}
Language     : {meta['language']} | {langs_md}
Stars        : {meta['stars']} | Forks: {meta['forks']} | Open Issues: {meta['open_issues']}
Branch       : {meta['default_branch']} | Topics: {', '.join(meta['topics']) or 'none'}
Created      : {meta['created_at']} | Last updated: {meta['updated_at']}

━━━ README (first 2,000 chars) ━━━
{readme[:2000]}

━━━ DIRECTORY STRUCTURE ━━━
{structure}

━━━ RECENT COMMITS (last 15) ━━━
{commits_md}

━━━ OPEN PULL REQUESTS ━━━
{prs_md}

━━━ OPEN ISSUES ━━━
{issues_md}

━━━ QUERY ━━━
{state['query']}

Provide your intelligence report (concise, max 400 words):"""

        intelligence = _ask_claude(claude, prompt)

        # ── Persist state to Supabase ─────────────────────────────────────────────
        state_logger.log_state(
            workflow_id=workflow_id,
            checkpoint_id=checkpoint_id,
            agent=ROLE,
            state={
                "repo": repo_slug,
                "query": state["query"],
                "status": "completed",
                "intelligence_chars": len(intelligence),
                "commits_fetched": len(commits),
                "prs_fetched": len(prs),
                "issues_fetched": len(issues),
            },
        )

        log.info(f"{ROLE}.completed", workflow_id=workflow_id, intelligence_chars=len(intelligence))
        return {"intelligence": intelligence, "error": None, "workflow_id": workflow_id, "agent": ROLE}

    except ValueError as exc:
        msg = str(exc)
        log.error(f"{ROLE}.invalid_repo", error=msg, repo=repo_slug)
        notifier.agent_error(ROLE, repo_slug, msg)
        state_logger.log_state(workflow_id, checkpoint_id, ROLE, {"repo": repo_slug, "status": "invalid_repo", "error": msg})
        return {"intelligence": "", "error": msg, "workflow_id": workflow_id}

    except GithubException as exc:
        msg = f"GitHub API error ({exc.status}): {(exc.data or {}).get('message', str(exc))}"
        log.error(f"{ROLE}.github_error", status=exc.status, error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        state_logger.log_state(workflow_id, checkpoint_id, ROLE, {"repo": repo_slug, "status": "github_error", "error": msg})
        return {"intelligence": "", "error": msg, "workflow_id": workflow_id}

    except anthropic.APIError as exc:
        msg = f"Claude API error: {exc}"
        log.error(f"{ROLE}.claude_error", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"intelligence": "", "error": msg, "workflow_id": workflow_id}

    except Exception as exc:
        msg = f"Unexpected error in {ROLE}: {exc}"
        log.exception(f"{ROLE}.unexpected_error", error=msg)
        notifier.agent_error(ROLE, repo_slug, msg)
        return {"intelligence": "", "error": msg, "workflow_id": workflow_id}


# ── Backwards-compatibility alias (referenced in Phase 1 brief) ─────────────────
hugh_node = github_intelligence_node
HugoState = GitHubIntelState
