"""GitHub REST API wrapper for @hugo and other agents."""

import os
import base64 as _b64
import requests
from typing import Optional

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_BASE = "https://api.github.com"


class GitHubTools:
    """Lightweight GitHub REST API client.

    Works with public repos without a token (60 req/hr).
    Set GITHUB_TOKEN for 5,000 req/hr (recommended).
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if GITHUB_TOKEN:
            self.session.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    def _get(self, path: str, params: Optional[dict] = None):
        url = f"{GITHUB_BASE}{path}"
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_repo_info(self, owner: str, repo: str) -> dict:
        """Top-level repo metadata (stars, language, description, etc.)."""
        return self._get(f"/repos/{owner}/{repo}")

    def get_file_contents(self, owner: str, repo: str, path: str, ref: str = "main") -> dict:
        """Fetch and decode a file. Returns {content, sha, path}."""
        data = self._get(f"/repos/{owner}/{repo}/contents/{path}", {"ref": ref})
        content = _b64.b64decode(data["content"]).decode("utf-8")
        return {"content": content, "sha": data["sha"], "path": path}

    def list_commits(self, owner: str, repo: str, per_page: int = 10, sha: str = "") -> list:
        """Recent commits, newest first."""
        params: dict = {"per_page": per_page}
        if sha:
            params["sha"] = sha
        return self._get(f"/repos/{owner}/{repo}/commits", params)

    def list_open_issues(self, owner: str, repo: str, per_page: int = 10) -> list:
        """Open issues (GitHub excludes PRs from this endpoint)."""
        return self._get(
            f"/repos/{owner}/{repo}/issues",
            {"state": "open", "per_page": per_page},
        )

    def list_pull_requests(self, owner: str, repo: str, state: str = "open", per_page: int = 10) -> list:
        """List PRs by state: open | closed | all."""
        return self._get(
            f"/repos/{owner}/{repo}/pulls",
            {"state": state, "per_page": per_page},
        )

    def search_code(self, query: str, repo: Optional[str] = None) -> dict:
        """Search code. Use repo='owner/name' to scope to one repo."""
        q = f"{query} repo:{repo}" if repo else query
        return self._get("/search/code", {"q": q})
