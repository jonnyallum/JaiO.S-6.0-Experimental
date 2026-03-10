"""@hugo — GitHub Intelligence Specialist

Analyzes GitHub repositories and returns actionable intelligence.
Uses GitHub REST API + Claude Sonnet for synthesis.

Phase 1: Real implementation replacing the stub.
"""

from typing import Optional
from typing_extensions import TypedDict
import structlog
import anthropic

from tools.github_tools import GitHubTools

log = structlog.get_logger()

CLAUDE_MODEL = "claude-sonnet-4-6"
README_MAX_CHARS = 3000
INTEL_MAX_TOKENS = 2000


class HugoState(TypedDict):
    """State for @hugo GitHub intelligence tasks."""
    repo_owner: str
    repo_name: str
    query: str
    intelligence: str
    error: Optional[str]


def hugo_node(state: HugoState) -> dict:
    """
    @hugo — GitHub Intelligence Specialist

    Fetches live GitHub data (readme, commits, issues) then calls Claude
    to synthesise actionable intelligence for the given query.

    Raises on failure — LangGraph catches and handles.
    """
    log.info(
        "hugo_started",
        repo=f"{state['repo_owner']}/{state['repo_name']}",
        query=state["query"],
    )

    github = GitHubTools()

    # --- 1. Repo metadata -------------------------------------------------
    repo_info = github.get_repo_info(state["repo_owner"], state["repo_name"])

    # --- 2. README --------------------------------------------------------
    try:
        readme = github.get_file_contents(
            state["repo_owner"], state["repo_name"], "README.md"
        )
        readme_content = readme["content"][:README_MAX_CHARS]
    except Exception:
        try:
            readme = github.get_file_contents(
                state["repo_owner"], state["repo_name"], "readme.md"
            )
            readme_content = readme["content"][:README_MAX_CHARS]
        except Exception:
            readme_content = "(README not found)"

    # --- 3. Recent commits ------------------------------------------------
    commits = github.list_commits(state["repo_owner"], state["repo_name"], per_page=10)
    commit_lines = [
        f"- {c['sha'][:7]} | {c['commit']['message'].splitlines()[0][:80]} "
        f"| {c['commit']['author']['name']} | {c['commit']['author']['date'][:10]}"
        for c in commits[:10]
    ]

    # --- 4. Open issues ---------------------------------------------------
    try:
        issues = github.list_open_issues(
            state["repo_owner"], state["repo_name"], per_page=5
        )
        issue_lines = [f"- #{i['number']} {i['title']}" for i in issues[:5]]
    except Exception:
        issue_lines = []

    # --- 5. Open PRs ------------------------------------------------------
    try:
        prs = github.list_pull_requests(
            state["repo_owner"], state["repo_name"], per_page=5
        )
        pr_lines = [f"- #{p['number']} {p['title']}" for p in prs[:5]]
    except Exception:
        pr_lines = []

    # --- 6. Build context for Claude --------------------------------------
    NL = "\n"
    context = f"""Repository: {state['repo_owner']}/{state['repo_name']}
Description: {repo_info.get('description') or 'none'}
Language: {repo_info.get('language') or 'unknown'}
Stars: {repo_info.get('stargazers_count', 0)} | Forks: {repo_info.get('forks_count', 0)} | Open issues: {repo_info.get('open_issues_count', 0)}
Default branch: {repo_info.get('default_branch', 'main')}
Last pushed: {repo_info.get('pushed_at', '')[:10]}

README (first {README_MAX_CHARS} chars):
{readme_content}

Recent commits (last 10):
{NL.join(commit_lines) if commit_lines else 'none'}

Open issues (top 5):
{NL.join(issue_lines) if issue_lines else 'none'}

Open pull requests (top 5):
{NL.join(pr_lines) if pr_lines else 'none'}

Query: {state['query']}"""

    # --- 7. Ask Claude ----------------------------------------------------
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=INTEL_MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": (
                "You are @hugo, a GitHub Intelligence Specialist in JaiOS 6. "
                "Analyze the repository data below and answer the query with specific, actionable intelligence. "
                "Use only facts from the data provided. Be concise and structured.\n\n"
                f"{context}"
            ),
        }],
    )

    intelligence = response.content[0].text

    log.info(
        "hugo_completed",
        repo=f"{state['repo_owner']}/{state['repo_name']}",
        intelligence_chars=len(intelligence),
    )

    return {"intelligence": intelligence, "error": None}
