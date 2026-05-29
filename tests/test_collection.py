"""Unit tests for scripts/collection — Phase U prelude.

No real network calls — github_client wrapped via monkeypatching
subprocess.run.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.collection import collect_prs  # noqa: E402
from scripts.collection.db import ArchiveDatabase  # noqa: E402
from scripts.collection.github_client import GitHubCLIClient, GitHubCLIError  # noqa: E402


# ── github_client tests (mocked subprocess) ─────────────────────────────


def _patch_gh(monkeypatch, responses: dict[str, str]) -> list[list[str]]:
    """Patch subprocess.run so 'gh api ENDPOINT' returns responses[ENDPOINT]."""
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, stdout, returncode=0, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def _run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] != "gh" or cmd[1] != "api":
            return _Result("", 1, "unexpected")
        endpoint = cmd[2]
        if endpoint in responses:
            return _Result(responses[endpoint])
        return _Result("", 1, f"no mock for {endpoint}")

    monkeypatch.setattr("subprocess.run", _run)
    return calls


def test_github_client_fetch_user(monkeypatch) -> None:
    _patch_gh(monkeypatch, {"user": json.dumps({"login": "test_user"})})
    c = GitHubCLIClient("org", "repo")
    u = c.fetch_authenticated_user()
    assert u == {"login": "test_user"}
    assert c.calls_used == 1


def test_github_client_paginate_flattens(monkeypatch) -> None:
    pages = [[{"id": 1}, {"id": 2}], [{"id": 3}]]
    _patch_gh(monkeypatch, {
        "repos/o/r/pulls?state=all&per_page=100": json.dumps(pages)
    })
    c = GitHubCLIClient("o", "r")
    prs = c.fetch_pull_requests()
    assert prs == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_github_client_max_calls_enforced(monkeypatch) -> None:
    _patch_gh(monkeypatch, {"user": json.dumps({"login": "u"})})
    c = GitHubCLIClient("o", "r", max_calls=1)
    c.fetch_authenticated_user()
    with pytest.raises(GitHubCLIError, match="cap exhausted"):
        c.fetch_authenticated_user()


# ── db.py tests ──────────────────────────────────────────────────────────


def test_db_initialize_schema_creates_tables(tmp_path: Path) -> None:
    db = ArchiveDatabase(tmp_path / "test.sqlite")
    db.initialize_schema()
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r["name"] for r in rows]
    for expected in ("repositories", "collection_runs", "pull_requests_current",
                      "pull_request_files_current",
                      "pull_request_review_comments_current"):
        assert expected in names
    db.close()


def test_db_upsert_repository_idempotent(tmp_path: Path) -> None:
    db = ArchiveDatabase(tmp_path / "t.sqlite")
    db.initialize_schema()
    id1 = db.upsert_repository("o", "r", "o/r")
    id2 = db.upsert_repository("o", "r", "o/r", track="t1")
    assert id1 == id2
    db.close()


def test_db_upsert_pull_request_idempotent(tmp_path: Path) -> None:
    db = ArchiveDatabase(tmp_path / "t.sqlite")
    db.initialize_schema()
    repo_id = db.upsert_repository("o", "r", "o/r")
    run_id = db.start_collection_run(repo_id, "full")
    pr = {
        "id": 1001, "number": 5, "title": "feat: x", "state": "open",
        "user": {"login": "dev"}, "body": "...",
        "base": {"ref": "main"}, "head": {"ref": "feature", "sha": "abc"},
        "additions": 10, "deletions": 2,
    }
    pr_id_1 = db.upsert_pull_request(repo_id, run_id, pr)
    pr_id_2 = db.upsert_pull_request(repo_id, run_id, pr)
    assert pr_id_1 == pr_id_2
    db.close()


def test_db_upsert_review_comment_dedup(tmp_path: Path) -> None:
    db = ArchiveDatabase(tmp_path / "t.sqlite")
    db.initialize_schema()
    repo_id = db.upsert_repository("o", "r", "o/r")
    run_id = db.start_collection_run(repo_id, "full")
    pr_id = db.upsert_pull_request(repo_id, run_id, {
        "id": 1, "number": 1, "title": "t", "state": "open",
        "user": {"login": "a"}, "base": {}, "head": {},
    })
    db.upsert_pr_review_comment(pr_id, {
        "id": 9001, "user": {"login": "m"}, "body": "first",
        "path": "X.java", "line": 1,
    })
    db.upsert_pr_review_comment(pr_id, {
        "id": 9001, "user": {"login": "m"}, "body": "updated",
        "path": "X.java", "line": 1,
    })
    rows = db.conn.execute(
        "SELECT body FROM pull_request_review_comments_current"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["body"] == "updated"
    db.close()


# ── collect_prs orchestrator tests ───────────────────────────────────────


def test_collect_filter_since_filters_old_prs() -> None:
    prs = [
        {"number": 1, "updated_at": "2026-01-01T00:00:00Z"},
        {"number": 2, "updated_at": "2026-05-01T00:00:00Z"},
        {"number": 3, "updated_at": "2026-05-25T00:00:00Z"},
    ]
    out = collect_prs._filter_prs(prs, since="2026-04-01T00:00:00Z", limit=None)
    assert [p["number"] for p in out] == [2, 3]


def test_collect_filter_limit() -> None:
    prs = [{"number": i, "updated_at": "2026-05-25T00:00:00Z"} for i in range(10)]
    out = collect_prs._filter_prs(prs, since=None, limit=3)
    assert len(out) == 3


def test_collect_filter_sorts_ascending_before_limit() -> None:
    # GitHub returns newest-created first; limit must take the OLDEST-updated so
    # the cursor advances over a contiguous prefix with no gaps.
    prs = [
        {"number": 3, "updated_at": "2026-05-03T00:00:00Z"},
        {"number": 1, "updated_at": "2026-05-01T00:00:00Z"},
        {"number": 2, "updated_at": "2026-05-02T00:00:00Z"},
    ]
    out = collect_prs._filter_prs(prs, since=None, limit=2)
    assert [p["number"] for p in out] == [1, 2]


def test_collect_filter_title_contains() -> None:
    prs = [
        {"number": 1, "title": "step1 feature", "updated_at": "2026-05-25T00:00:00Z"},
        {"number": 2, "title": "step2 fix", "updated_at": "2026-05-25T00:00:00Z"},
        {"number": 3, "title": "[STEP1] revert", "updated_at": "2026-05-25T00:00:00Z"},
    ]
    out = collect_prs._filter_prs(prs, since=None, limit=None,
                                    title_contains="step1")
    assert [p["number"] for p in out] == [1, 3]


def test_collect_end_to_end_mocked(monkeypatch, tmp_path: Path) -> None:
    """Mocked collect run — verifies orchestration writes DB rows correctly."""
    db_path = tmp_path / "prs.sqlite3"
    responses = {
        "user": json.dumps({"login": "tester"}),
        "repos/o/r/pulls?state=all&per_page=100": json.dumps([[
            {"id": 100, "number": 50, "updated_at": "2026-05-25T00:00:00Z"},
        ]]),
        "repos/o/r/pulls/50": json.dumps({
            "id": 100, "number": 50, "title": "step1: bean", "state": "open",
            "user": {"login": "DongKey777"}, "body": "...", "base": {"ref": "main"},
            "head": {"ref": "step1", "sha": "abc"}, "additions": 5, "deletions": 1,
            "created_at": "2026-05-20T00:00:00Z",
        }),
        "repos/o/r/pulls/50/files?per_page=100": json.dumps([[
            {"filename": "X.java", "patch": "+@RestController", "status": "modified",
             "additions": 1, "deletions": 0, "changes": 1, "sha": "abcsha"},
        ]]),
        "repos/o/r/pulls/50/reviews?per_page=100": json.dumps([[]]),
        "repos/o/r/pulls/50/comments?per_page=100": json.dumps([[
            {"id": 9001, "user": {"login": "hyeonic"}, "body": "트랜잭션 경계",
             "path": "X.java", "line": 1, "created_at": "2026-05-21T00:00:00Z",
             "in_reply_to_id": None},
        ]]),
        "repos/o/r/issues/50/comments?per_page=100": json.dumps([[]]),
    }
    _patch_gh(monkeypatch, responses)
    report = collect_prs.collect(owner="o", repo="r", db_path=db_path)
    assert report.finished_status == "succeeded"
    assert report.prs_processed == 1
    assert report.review_comments_upserted == 1

    # Verify DB
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    prs_n = conn.execute("SELECT COUNT(*) FROM pull_requests_current").fetchone()[0]
    files_n = conn.execute("SELECT COUNT(*) FROM pull_request_files_current").fetchone()[0]
    rc_n = conn.execute("SELECT COUNT(*) FROM pull_request_review_comments_current").fetchone()[0]
    assert prs_n == 1 and files_n == 1 and rc_n == 1
    conn.close()


# ── budget + cursor regression tests ─────────────────────────────────────


def _pr_detail(number: int, updated_at: str) -> str:
    return json.dumps({
        "id": 1000 + number, "number": number, "title": f"step1 #{number}",
        "state": "open", "user": {"login": "DongKey777"}, "body": "",
        "base": {"ref": "main"}, "head": {"ref": "step1", "sha": f"sha{number}"},
        "additions": 1, "deletions": 0, "created_at": updated_at,
        "updated_at": updated_at,
    })


def test_fetch_rate_limit_does_not_consume_budget(monkeypatch) -> None:
    _patch_gh(monkeypatch, {
        "rate_limit": json.dumps({"resources": {"core": {"remaining": 4800}}}),
    })
    c = GitHubCLIClient("o", "r")
    rl = c.fetch_rate_limit()
    assert rl["resources"]["core"]["remaining"] == 4800
    assert c.calls_used == 0  # /rate_limit must not count against the cap


def test_collect_budget_derived_from_rate_limit(monkeypatch, tmp_path: Path) -> None:
    _patch_gh(monkeypatch, {
        "user": json.dumps({"login": "t"}),
        "rate_limit": json.dumps({"resources": {"core": {"remaining": 1234}}}),
        "repos/o/r/pulls?state=all&per_page=100": json.dumps([[]]),
    })
    report = collect_prs.collect(owner="o", repo="r", db_path=tmp_path / "d.sqlite3")
    assert report.gh_call_budget == 1234 - collect_prs.RATE_LIMIT_MARGIN
    assert report.finished_status == "succeeded"


def test_collect_partial_advances_cursor(monkeypatch, tmp_path: Path) -> None:
    """A capped run must record the last fully-collected PR's updated_at as the
    cursor (not wall-clock), so the next run resumes instead of restarting."""
    pulls = [[
        {"number": 3, "updated_at": "2026-05-03T00:00:00Z"},
        {"number": 1, "updated_at": "2026-05-01T00:00:00Z"},
        {"number": 2, "updated_at": "2026-05-02T00:00:00Z"},
    ]]
    responses = {
        "user": json.dumps({"login": "t"}),
        "repos/o/r/pulls?state=all&per_page=100": json.dumps(pulls),
    }
    for n, ts in [(1, "2026-05-01T00:00:00Z"), (2, "2026-05-02T00:00:00Z")]:
        responses[f"repos/o/r/pulls/{n}"] = _pr_detail(n, ts)
        responses[f"repos/o/r/pulls/{n}/files?per_page=100"] = json.dumps([[]])
        responses[f"repos/o/r/pulls/{n}/reviews?per_page=100"] = json.dumps([[]])
        responses[f"repos/o/r/pulls/{n}/comments?per_page=100"] = json.dumps([[]])
        responses[f"repos/o/r/issues/{n}/comments?per_page=100"] = json.dumps([[]])
    _patch_gh(monkeypatch, responses)

    db_path = tmp_path / "d.sqlite3"
    # auth(1) + list(1) + 5/PR: fully covers #1 and #2, then #3 detail hits cap.
    report = collect_prs.collect(owner="o", repo="r", db_path=db_path,
                                  max_calls=12)
    assert report.finished_status == "partial"
    assert report.prs_processed == 2

    conn = sqlite3.connect(str(db_path))
    finished_at = conn.execute(
        "SELECT finished_at FROM collection_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()
    # Cursor = updated_at of #2 (last clean PR), not #3 and not wall-clock.
    assert finished_at == "2026-05-02T00:00:00Z"
