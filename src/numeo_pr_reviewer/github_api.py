import os
import re
from typing import Any

import httpx

from .schema import InlineComment

PR_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")


class GitHubError(Exception):
    pass


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = PR_URL_RE.match(url.strip())
    if not m:
        raise GitHubError(f"Invalid PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self, token: str | None = None, timeout: float = 30.0):
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise GitHubError("GITHUB_TOKEN not set")
        self.client = httpx.Client(
            base_url=self.BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "numeo-pr-reviewer",
            },
            timeout=timeout,
        )

    def _get(self, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
        r = self.client.get(path, headers=headers or {})
        if r.status_code >= 400:
            raise GitHubError(f"GET {path} -> {r.status_code}: {r.text}")
        return r

    def _post(self, path: str, json: dict[str, Any]) -> httpx.Response:
        r = self.client.post(path, json=json)
        if r.status_code >= 400:
            raise GitHubError(f"POST {path} -> {r.status_code}: {r.text}")
        return r

    def get_pr(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}").json()

    def get_pr_diff(
        self, owner: str, repo: str, number: int, max_lines: int = 5000
    ) -> tuple[str, bool]:
        r = self._get(
            f"/repos/{owner}/{repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        diff = r.text
        lines = diff.splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n[diff truncated at {max_lines} lines]", True
        return diff, False

    def get_changed_files(self, owner: str, repo: str, number: int) -> list[str]:
        files = self._get(f"/repos/{owner}/{repo}/pulls/{number}/files?per_page=100").json()
        return [f["filename"] for f in files]

    def post_review(
        self,
        owner: str,
        repo: str,
        number: int,
        event: str,
        body: str,
        comments: list[InlineComment],
    ) -> dict[str, Any]:
        if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            raise GitHubError(f"Invalid review event: {event}")
        payload: dict[str, Any] = {"event": event, "body": body}
        if comments:
            payload["comments"] = [
                {"path": c.path, "line": c.line, "side": "RIGHT", "body": c.body}
                for c in comments
            ]
        return self._post(f"/repos/{owner}/{repo}/pulls/{number}/reviews", payload).json()

    def post_issue_comment(
        self, owner: str, repo: str, number: int, body: str
    ) -> dict[str, Any]:
        return self._post(
            f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body}
        ).json()

    def request_reviewers(
        self, owner: str, repo: str, number: int, reviewers: list[str]
    ) -> dict[str, Any]:
        if not reviewers:
            return {}
        return self._post(
            f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            {"reviewers": reviewers},
        ).json()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
