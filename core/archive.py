"""SQLite PR archive — read interface only.

Reads the paradigm-v2 collection schema written by scripts/collection/db.py
(see scripts/collection/schema.sql). The minimum query surface needed by
core/peer_pr.py + mission/peer_pr.py:

- list_prs(repo, author_login=None, limit=10)
- get_pr(repo, pr_number)
- get_diff_files(repo, pr_number)
- get_review_threads(repo, pr_number)

Real schema (the *_current tables — number is the GitHub PR number, `id` is the
archive surrogate key the child tables join on):
  pull_requests_current(id, number, title, author_login, additions, deletions, ...)
  pull_request_files_current(pull_request_id → pull_requests_current.id,
                             path, additions, deletions, patch_text, status)
  pull_request_review_comments_current(pull_request_id, github_comment_id,
                             user_login, path, line, body,
                             in_reply_to_github_comment_id, ...)

The legacy flat schema (pull_requests / review_comments / files keyed by
pr_number) this module assumed before never matched what collection actually
writes, so the whole peer-PR surface read empty against real archives. Queries
now resolve the PR number → surrogate id first, then filter the child tables by
pull_request_id, and alias the GitHub comment-id columns back to the (id,
in_reply_to_id, author_login) names the thread stitcher expects.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"


def _archive_path(repo: str, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    return state_root / "repos" / repo / "archive" / "prs.sqlite3"


@contextmanager
def _open(repo: str, state_root: Path = DEFAULT_STATE_ROOT):
    p = _archive_path(repo, state_root)
    if not p.exists():
        raise FileNotFoundError(f"no archive for repo {repo!r} at {p}")
    conn = sqlite3.connect(str(p))
    # The archive is read while bin/sync-prs may be writing it; wait for the
    # writer instead of erroring out with "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _pr_id(conn: sqlite3.Connection, pr_number: int) -> int | None:
    """Resolve a GitHub PR number to the archive surrogate id the child tables
    join on. None when the PR is not in the archive."""
    row = conn.execute(
        "SELECT id FROM pull_requests_current WHERE number = ?", (pr_number,)
    ).fetchone()
    return int(row["id"]) if row else None


def list_prs(
    repo: str,
    author_login: str | None = None,
    limit: int = 10,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> list[dict]:
    """Return recent PRs (descending by number)."""
    sql = ("SELECT number, title, author_login, additions, deletions "
           "FROM pull_requests_current")
    params: list = []
    if author_login:
        sql += " WHERE author_login = ?"
        params.append(author_login)
    sql += " ORDER BY number DESC LIMIT ?"
    params.append(limit)
    with _open(repo, state_root) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_pr(repo: str, pr_number: int, state_root: Path = DEFAULT_STATE_ROOT) -> dict | None:
    with _open(repo, state_root) as conn:
        row = conn.execute(
            "SELECT * FROM pull_requests_current WHERE number = ?", (pr_number,)
        ).fetchone()
        return dict(row) if row else None


def get_diff_files(
    repo: str, pr_number: int, state_root: Path = DEFAULT_STATE_ROOT
) -> list[dict]:
    with _open(repo, state_root) as conn:
        pr_id = _pr_id(conn, pr_number)
        if pr_id is None:
            return []
        return [
            dict(r)
            for r in conn.execute(
                "SELECT path, additions, deletions, patch_text "
                "FROM pull_request_files_current WHERE pull_request_id = ? "
                "ORDER BY path",
                (pr_id,),
            ).fetchall()
        ]


def get_review_threads(
    repo: str, pr_number: int, state_root: Path = DEFAULT_STATE_ROOT
) -> list[dict]:
    """Reconstruct review threads — top-level comment + its replies.

    The collection schema keys replies by GitHub comment ids
    (github_comment_id / in_reply_to_github_comment_id) and names the author
    user_login; we alias them to (id, in_reply_to_id, author_login) so the
    stitcher below — and core/prompt.py's renderer — stay unchanged.
    """
    with _open(repo, state_root) as conn:
        pr_id = _pr_id(conn, pr_number)
        if pr_id is None:
            return []
        all_comments = [
            dict(r)
            for r in conn.execute(
                "SELECT github_comment_id AS id, body, path, line, "
                "in_reply_to_github_comment_id AS in_reply_to_id, "
                "user_login AS author_login "
                "FROM pull_request_review_comments_current "
                "WHERE pull_request_id = ? ORDER BY github_comment_id",
                (pr_id,),
            ).fetchall()
        ]
    threads: dict[int, dict] = {}
    for c in all_comments:
        if c["in_reply_to_id"] is None:
            threads[c["id"]] = {"root": c, "replies": []}
    for c in all_comments:
        rid = c["in_reply_to_id"]
        if rid is not None and rid in threads:
            threads[rid]["replies"].append(c)
    return list(threads.values())
