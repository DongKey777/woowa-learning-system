"""SQLite PR archive — read interface only.

Ingest reuses legacy `bin/bootstrap-repo` until Phase 9 migration. This
module exposes the minimum query surface needed by core/peer_pr.py +
core/context.py:

- list_prs(repo, author_login=None, limit=10)
- get_pr(repo, pr_number)
- get_diff_files(repo, pr_number)
- get_review_threads(repo, pr_number)

Schema assumed (matches legacy archive.py):
  pull_requests(number, title, author_login, additions, deletions, ...)
  pull_request_files_current(pr_number, path, additions, deletions, patch_text)
  reviews(pr_number, author_login, body, submitted_at)
  review_comments(pr_number, id, body, path, line, in_reply_to_id, author_login)
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
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def list_prs(
    repo: str,
    author_login: str | None = None,
    limit: int = 10,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> list[dict]:
    """Return recent PRs (descending by number)."""
    sql = "SELECT number, title, author_login, additions, deletions FROM pull_requests"
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
            "SELECT * FROM pull_requests WHERE number = ?", (pr_number,)
        ).fetchone()
        return dict(row) if row else None


def get_diff_files(
    repo: str, pr_number: int, state_root: Path = DEFAULT_STATE_ROOT
) -> list[dict]:
    with _open(repo, state_root) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT path, additions, deletions, patch_text "
                "FROM pull_request_files_current WHERE pr_number = ? "
                "ORDER BY path",
                (pr_number,),
            ).fetchall()
        ]


def get_review_threads(
    repo: str, pr_number: int, state_root: Path = DEFAULT_STATE_ROOT
) -> list[dict]:
    """Reconstruct review threads — top-level comment + its replies."""
    with _open(repo, state_root) as conn:
        all_comments = [
            dict(r)
            for r in conn.execute(
                "SELECT id, body, path, line, in_reply_to_id, author_login "
                "FROM review_comments WHERE pr_number = ? ORDER BY id",
                (pr_number,),
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
