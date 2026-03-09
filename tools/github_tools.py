"""
GitHub Tools — real GitHub REST API via PyGithub.
Used by github_intelligence, security_audit, architecture_review skills.
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
    """Retry on rate limits and server errors. Never retry 404/401."""
    if isinstance(exc, GithubException):
        return exc.status in (403, 429, 500, 502, 503, 504)
    return False


class GitHubTools:
    """
    Wrapper around PyGithub for Antigravity skill use.
    ValueError raised for missing resources (404).
    GithubException raised for API errors (auto-retried on transient codes).

    Note: PyGithub PaginatedList does not support Python slice syntax reliably.
    All pagination is handled with itertools.islice.
    """

    def __init__(self):
        self._client = Github(settings.github_token)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception(_is_transient_github_error),
        reraise=True,
    )
    def get_repo(self, owner: str, name: str):
        """Fetch repo object. Raises ValueError if not found."""
        try:
            return self._client.get_repo(f"{owner}/{name}")
        except UnknownObjectException:
            raise ValueError(f"Repository {owner}/{name} not found or not accessible.")

    def get_file_contents(
        self,
        owner: str,
        repo_name: str,
        path: str,
        ref: str = "main",
    ) -> str:
        """
        Fetch decoded file content.
        Returns directory listing if path is a dir.
        Returns a placeholder string if the file doesn't exist.
        """
        repo = self.get_repo(owner, repo_name)
        try:
            content = repo.get_contents(path, ref=ref)
            if isinstance(content, list):
                return "\n".join(
                    f"{'📁' if c.type == 'dir' else '📄'} {c.name}"
                    for c in content
                )
            return content.decoded_content.decode("utf-8", errors="replace")
        except UnknownObjectException:
            return f"[{path} not found in {repo_name}@{ref}]"

    def list_commits(
        self,
        owner: str,
        repo_name: str,
        per_page: int = 15,
    ) -> list[dict]:
        """Fetch recent commits with metadata."""
        repo = self.get_repo(owner, repo_name)
        result = []
        for commit in itertools.islice(repo.get_commits(), per_page):
            try:
                stats = commit.stats
                additions = stats.additions
                deletions = stats.deletions
            except Exception:
                additions = deletions = 0
            result.append(
                {
                    "sha": commit.sha[:8],
                    "message": commit.commit.message.split("\n")[0][:120],
                    "author": commit.commit.author.name,
                    "date": commit.commit.author.date.strftime("%Y-%m-%d %H:%M"),
                    "additions": additions,
                    "deletions": deletions,
                }
            )
        return result

    def list_pull_requests(
        self,
        owner: str,
        repo_name: str,
        state: str = "open",
        limit: int = 10,
    ) -> list[dict]:
        """Fetch pull requests with metadata."""
        repo = self.get_repo(owner, repo_name)
        result = []
        for pr in itertools.islice(repo.get_pulls(state=state), limit):
            result.append(
                {
                    "number": pr.number,
                    "title": pr.title[:100],
                    "state": pr.state,
                    "author": pr.user.login,
                    "created_at": pr.created_at.strftime("%Y-%m-%d"),
                    "labels": [label.name for label in pr.labels],
                    "body_preview": (pr.body or "")[:200],
                    "files_changed": pr.changed_files,
                    "additions": pr.additions,
                    "deletions": pr.deletions,
                }
            )
        return result

    def list_issues(
        self,
        owner: str,
        repo_name: str,
        state: str = "open",
        limit: int = 10,
    ) -> list[dict]:
        """Fetch issues, excluding PRs."""
        repo = self.get_repo(owner, repo_name)
        result = []
        count = 0
        for issue in repo.get_issues(state=state):
            if count >= limit:
                break
            if issue.pull_request:
                continue
            result.append(
                {
                    "number": issue.number,
                    "title": issue.title[:100],
                    "state": issue.state,
                    "author": issue.user.login,
                    "labels": [label.name for label in issue.labels],
                    "created_at": issue.created_at.strftime("%Y-%m-%d"),
                    "body_preview": (issue.body or "")[:200],
                    "comments": issue.comments,
                }
            )
            count += 1
        return result

    def get_repo_structure(self, owner: str, repo_name: str, max_depth: int = 2) -> str:
        """Return a tree-style listing of the repo's directory structure."""
        repo = self.get_repo(owner, repo_name)

        def _walk(path: str = "", depth: int = 0) -> list[str]:
            if depth >= max_depth:
                return []
            try:
                items = repo.get_contents(path)
            except Exception:
                return []
            lines = []
            for item in sorted(items, key=lambda x: (x.type != "dir", x.name)):
                indent = "  " * depth
                if item.type == "dir":
                    lines.append(f"{indent}📁 {item.name}/")
                    lines.extend(_walk(item.path, depth + 1))
                else:
                    lines.append(f"{indent}📄 {item.name}")
            return lines

        return "\n".join(_walk()[:150])

    def search_code(
        self,
        query: str,
        repo_fullname: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search code across GitHub or within a specific repo."""
        search_query = query
        if repo_fullname:
            search_query += f" repo:{repo_fullname}"
        result = []
        for item in itertools.islice(self._client.search_code(search_query), limit):
            result.append(
                {
                    "name": item.name,
                    "path": item.path,
                    "repository": item.repository.full_name,
                    "url": item.html_url,
                }
            )
        return result

    def get_languages(self, owner: str, repo_name: str) -> dict[str, int]:
        """Return language breakdown (bytes per language)."""
        repo = self.get_repo(owner, repo_name)
        return repo.get_languages()

    def get_topics(self, owner: str, repo_name: str) -> list[str]:
        """Return repo topics/tags."""
        repo = self.get_repo(owner, repo_name)
        return repo.get_topics()

    def get_repo_meta(self, owner: str, repo_name: str) -> dict:
        """Compact repo metadata for agent prompts."""
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
