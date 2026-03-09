"""
GitHub Tools — real GitHub REST API via PyGithub.
Used by github_intelligence, security_audit, architecture_review skills.

Performance notes:
- commit.stats requires one HTTP call per commit — never use it in bulk fetches.
- PaginatedList does not support slice syntax — always use itertools.islice.
- repo.get_contents() for tree walk is one call per directory level.
- get_repo() is cached per GitHubTools instance — one /repos call per workflow run.
"""
import itertools
from typing import Optional

import structlog
from github import Github, GithubException, UnknownObjectException
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings

log = structlog.get_logger()


def _is_transient_github_error(exc: Exception) -> bool:
    if isinstance(exc, GithubException):
        return exc.status in (403, 429, 500, 502, 503, 504)
    return False


class GitHubTools:
    def __init__(self):
        self._client = Github(settings.github_token)
        self._repo_cache: dict = {}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception(_is_transient_github_error),
        reraise=True,
    )
    def get_repo(self, owner: str, name: str):
        """Fetch and cache the repo object — one API call per unique repo per instance."""
        key = f"{owner}/{name}"
        if key not in self._repo_cache:
            try:
                self._repo_cache[key] = self._client.get_repo(key)
            except UnknownObjectException:
                raise ValueError(f"Repository {key} not found or not accessible.")
        return self._repo_cache[key]

    def get_file_contents(self, owner: str, repo_name: str, path: str, ref: str = "main") -> str:
        repo = self.get_repo(owner, repo_name)
        try:
            content = repo.get_contents(path, ref=ref)
            if isinstance(content, list):
                return "\n".join(
                    f"{'📁' if c.type == 'dir' else '📄'} {c.name}" for c in content
                )
            return content.decoded_content.decode("utf-8", errors="replace")
        except UnknownObjectException:
            return f"[{path} not found in {repo_name}@{ref}]"

    def list_commits(self, owner: str, repo_name: str, per_page: int = 10) -> list[dict]:
        """
        Fetch recent commits. Does NOT fetch stats (commit.stats = 1 API call each).
        Keeps bulk fetches fast.
        """
        repo = self.get_repo(owner, repo_name)
        result = []
        for commit in itertools.islice(repo.get_commits(), per_page):
            result.append({
                "sha": commit.sha[:8],
                "message": commit.commit.message.split("\n")[0][:120],
                "author": commit.commit.author.name,
                "date": commit.commit.author.date.strftime("%Y-%m-%d %H:%M"),
            })
        return result

    def list_pull_requests(self, owner: str, repo_name: str, state: str = "open", limit: int = 10) -> list[dict]:
        repo = self.get_repo(owner, repo_name)
        result = []
        for pr in itertools.islice(repo.get_pulls(state=state), limit):
            result.append({
                "number": pr.number,
                "title": pr.title[:100],
                "state": pr.state,
                "author": pr.user.login,
                "created_at": pr.created_at.strftime("%Y-%m-%d"),
                "labels": [label.name for label in pr.labels],
                "body_preview": (pr.body or "")[:200],
            })
        return result

    def list_issues(self, owner: str, repo_name: str, state: str = "open", limit: int = 10) -> list[dict]:
        repo = self.get_repo(owner, repo_name)
        result = []
        count = 0
        for issue in repo.get_issues(state=state):
            if count >= limit:
                break
            if issue.pull_request:
                continue
            result.append({
                "number": issue.number,
                "title": issue.title[:100],
                "state": issue.state,
                "author": issue.user.login,
                "labels": [label.name for label in issue.labels],
                "created_at": issue.created_at.strftime("%Y-%m-%d"),
                "body_preview": (issue.body or "")[:200],
                "comments": issue.comments,
            })
            count += 1
        return result

    def get_repo_structure(self, owner: str, repo_name: str) -> str:
        """
        Returns root-level file listing only (no recursive walk).
        Recursive walk costs one API call per directory — too slow for Phase 1.
        """
        repo = self.get_repo(owner, repo_name)
        try:
            items = repo.get_contents("")
            lines = []
            for item in sorted(items, key=lambda x: (x.type != "dir", x.name)):
                prefix = "📁" if item.type == "dir" else "📄"
                lines.append(f"{prefix} {item.name}")
            return "\n".join(lines)
        except Exception as exc:
            return f"[Could not fetch structure: {exc}]"

    def search_code(self, query: str, repo_fullname: Optional[str] = None, limit: int = 10) -> list[dict]:
        search_query = query
        if repo_fullname:
            search_query += f" repo:{repo_fullname}"
        result = []
        for item in itertools.islice(self._client.search_code(search_query), limit):
            result.append({
                "name": item.name,
                "path": item.path,
                "repository": item.repository.full_name,
                "url": item.html_url,
            })
        return result

    def get_languages(self, owner: str, repo_name: str) -> dict[str, int]:
        return self.get_repo(owner, repo_name).get_languages()

    def get_topics(self, owner: str, repo_name: str) -> list[str]:
        return self.get_repo(owner, repo_name).get_topics()

    def get_repo_meta(self, owner: str, repo_name: str) -> dict:
        repo = self.get_repo(owner, repo_name)
        return {
            "full_name": repo.full_name,
            "description": repo.description or "",
            "stars": repo.stargazers_count,
            "forks": repo.forks_count,
            "open_issues": repo.open_issues_count,
            "default_branch": repo.default_branch,
            "language": repo.language or "unknown",
            "created_at": repo.created_at.strftime("%Y-%m-%d"),
            "updated_at": repo.updated_at.strftime("%Y-%m-%d"),
            "topics": repo.get_topics(),
            "size_kb": repo.size,
        }
