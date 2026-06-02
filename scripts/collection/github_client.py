"""GitHub CLI wrapper for PR archive collection.

Wraps `gh api` subprocess calls with retry + rate-limit awareness.
No paid API. Requires gh CLI + `gh auth login` complete.

Public class: GitHubCLIClient(owner, repo)
  - fetch_authenticated_user() -> dict
  - fetch_pull_requests(state='all') -> list[dict]
  - fetch_pull_request_detail(number) -> dict
  - fetch_pull_request_files(number) -> list[dict]
  - fetch_pull_request_reviews(number) -> list[dict]
  - fetch_pull_request_review_comments(number) -> list[dict]
  - fetch_pull_request_review_comments_for_review(number, review_id) -> list[dict]
  - fetch_pull_request_issue_comments(number) -> list[dict]
  - fetch_review_threads(number) -> dict   # GraphQL: isResolved/isOutdated/decision
  - fetch_rate_limit() -> dict   # /rate_limit; does not consume quota
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any


class GitHubCLIError(RuntimeError):
    """Raised when a `gh api` command fails."""


class GitHubCLIClient:
    DEFAULT_PER_PAGE = 100
    RETRY_BACKOFF_S = (1.0, 3.0, 10.0)  # 3 retries on transient errors

    def __init__(self, owner: str, repo: str, max_calls: int | None = None) -> None:
        self.owner = owner
        self.repo = repo
        self.calls_used = 0
        self.max_calls = max_calls

    # ── public API ───────────────────────────────────────────────────────

    def fetch_authenticated_user(self) -> dict[str, Any]:
        return self._run_api("user")

    def fetch_rate_limit(self) -> dict[str, Any]:
        """Query GitHub's documented 5000/hr authenticated REST budget.

        The /rate_limit endpoint itself does not count against the quota
        (GitHub docs), so this bypasses the local cap and calls_used counter.
        """
        try:
            result = subprocess.run(
                ["gh", "api", "rate_limit"],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise GitHubCLIError(f"rate_limit probe failed: {e}")
        if result.returncode != 0:
            raise GitHubCLIError((result.stderr or "rate_limit probe failed").strip())
        return json.loads(result.stdout)

    def fetch_pull_requests(self, state: str = "all") -> list[dict[str, Any]]:
        endpoint = (f"repos/{self.owner}/{self.repo}/pulls"
                    f"?state={state}&per_page={self.DEFAULT_PER_PAGE}")
        return self._run_api(endpoint, paginate=True)

    def fetch_pull_request_detail(self, number: int) -> dict[str, Any]:
        return self._run_api(f"repos/{self.owner}/{self.repo}/pulls/{number}")

    def fetch_pull_request_files(self, number: int) -> list[dict[str, Any]]:
        endpoint = (f"repos/{self.owner}/{self.repo}/pulls/{number}/files"
                    f"?per_page={self.DEFAULT_PER_PAGE}")
        return self._run_api(endpoint, paginate=True)

    def fetch_pull_request_reviews(self, number: int) -> list[dict[str, Any]]:
        endpoint = (f"repos/{self.owner}/{self.repo}/pulls/{number}/reviews"
                    f"?per_page={self.DEFAULT_PER_PAGE}")
        return self._run_api(endpoint, paginate=True)

    def fetch_pull_request_review_comments(self, number: int) -> list[dict[str, Any]]:
        endpoint = (f"repos/{self.owner}/{self.repo}/pulls/{number}/comments"
                    f"?per_page={self.DEFAULT_PER_PAGE}")
        return self._run_api(endpoint, paginate=True)

    def fetch_pull_request_review_comments_for_review(
        self, number: int, review_id: int
    ) -> list[dict[str, Any]]:
        """Comments scoped to one review. Unlike the PR-wide /comments endpoint,
        this returns an unsubmitted PENDING review's draft comments to its author."""
        endpoint = (f"repos/{self.owner}/{self.repo}/pulls/{number}"
                    f"/reviews/{review_id}/comments?per_page={self.DEFAULT_PER_PAGE}")
        return self._run_api(endpoint, paginate=True)

    def fetch_pull_request_issue_comments(self, number: int) -> list[dict[str, Any]]:
        endpoint = (f"repos/{self.owner}/{self.repo}/issues/{number}/comments"
                    f"?per_page={self.DEFAULT_PER_PAGE}")
        return self._run_api(endpoint, paginate=True)

    def fetch_review_threads(self, number: int) -> dict[str, Any]:
        """GraphQL review-thread state the REST API cannot provide: per-thread
        isResolved/isOutdated/resolvedBy + PR-level reviewDecision.

        Returns {"review_decision": str|None, "nodes": [<raw thread node>...]}.
        A thread's root REST comment id is node["comments"]["nodes"][0]["databaseId"]
        — the join key back to the REST /comments spine.
        """
        query = (
            "query($owner:String!,$name:String!,$number:Int!){"
            "repository(owner:$owner,name:$name){"
            "pullRequest(number:$number){"
            "reviewDecision "
            "reviewThreads(first:100){nodes{"
            "isResolved isOutdated isCollapsed resolvedBy{login} path line "
            "comments(first:1){nodes{databaseId author{login}}}"
            "}}}}}"
        )
        data = self._run_graphql(
            query, owner=self.owner, name=self.repo, number=number
        )
        pr = ((data.get("repository") or {}).get("pullRequest")) or {}
        threads = (pr.get("reviewThreads") or {}).get("nodes") or []
        return {"review_decision": pr.get("reviewDecision"), "nodes": threads}

    # ── internals ────────────────────────────────────────────────────────

    def _check_cap(self) -> None:
        if self.max_calls is not None and self.calls_used >= self.max_calls:
            raise GitHubCLIError(
                f"gh call cap exhausted ({self.calls_used}/{self.max_calls})"
            )

    def _run_api(self, endpoint: str, paginate: bool = False) -> Any:
        self._check_cap()
        cmd = ["gh", "api", endpoint]
        if paginate:
            cmd.extend(["--paginate", "--slurp"])

        last_err = None
        for attempt in range(len(self.RETRY_BACKOFF_S) + 1):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=120)
                self.calls_used += 1
                if result.returncode == 0:
                    raw = result.stdout.strip()
                    if not raw:
                        return [] if paginate else {}
                    payload = json.loads(raw)
                    return self._flatten_pages(payload) if paginate else payload
                # Non-zero: check if retryable (rate limit / 5xx)
                err = (result.stderr or "").strip()
                last_err = err
                lower = err.lower()
                retryable = ("rate limit" in lower or "secondary" in lower or
                             "5xx" in lower or "503" in lower or "502" in lower)
                if not retryable or attempt >= len(self.RETRY_BACKOFF_S):
                    break
                time.sleep(self.RETRY_BACKOFF_S[attempt])
            except subprocess.TimeoutExpired:
                last_err = "gh api timed out"
                if attempt >= len(self.RETRY_BACKOFF_S):
                    break
                time.sleep(self.RETRY_BACKOFF_S[attempt])
            except FileNotFoundError:
                raise GitHubCLIError(
                    "gh CLI not installed — macOS: brew install gh, "
                    "Ubuntu: sudo apt install gh, Windows: winget install GitHub.cli"
                )
        raise GitHubCLIError(last_err or f"gh api failed: {endpoint}")

    def _run_graphql(self, query: str, **variables: Any) -> dict[str, Any]:
        self._check_cap()
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            cmd.extend(["-F", f"{key}={value}"])

        last_err = None
        for attempt in range(len(self.RETRY_BACKOFF_S) + 1):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=120)
                self.calls_used += 1
                if result.returncode == 0:
                    raw = result.stdout.strip()
                    payload = json.loads(raw) if raw else {}
                    if payload.get("errors"):
                        raise GitHubCLIError(f"graphql errors: {payload['errors']}")
                    return payload.get("data") or {}
                err = (result.stderr or "").strip()
                last_err = err
                lower = err.lower()
                retryable = ("rate limit" in lower or "secondary" in lower or
                             "5xx" in lower or "503" in lower or "502" in lower)
                if not retryable or attempt >= len(self.RETRY_BACKOFF_S):
                    break
                time.sleep(self.RETRY_BACKOFF_S[attempt])
            except subprocess.TimeoutExpired:
                last_err = "gh api graphql timed out"
                if attempt >= len(self.RETRY_BACKOFF_S):
                    break
                time.sleep(self.RETRY_BACKOFF_S[attempt])
            except FileNotFoundError:
                raise GitHubCLIError(
                    "gh CLI not installed — macOS: brew install gh, "
                    "Ubuntu: sudo apt install gh, Windows: winget install GitHub.cli"
                )
        raise GitHubCLIError(last_err or "gh api graphql failed")

    @staticmethod
    def _flatten_pages(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return [payload]
        flat: list[dict[str, Any]] = []
        for page in payload:
            if isinstance(page, list):
                flat.extend(page)
            else:
                flat.append(page)
        return flat
