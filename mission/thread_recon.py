"""mission.thread_recon — family G (review-thread reconstruction, mode thread_recon).

Repo-scoped, learner-scoped, read-only. Answers "내 PR 리뷰 대화 흐름 보여줘":
it stitches isolated review comments back into reply-CHAINS so a learner can
read a full back-and-forth (root comment → replies in order) instead of a flat
list of disconnected comments.

A reply points at its parent via in_reply_to_github_comment_id (= the parent's
github_comment_id). A root comment has that field NULL/empty. A *thread* is one
root plus every comment whose reply-chain resolves back to that root, ordered by
created_at. Threads are built ONLY on the learner's OWN PRs
(pull_requests_current.author_login == learner_login) and a thread must carry at
least one reply (reply_count >= 1); a single isolated comment is not a thread.

Pure read-only over state/repos/<repo>/archive/prs.sqlite3
(table pull_request_review_comments_current, joined to pull_requests_current for
the PR number). Schema-drift safe: columns are probed via PRAGMA table_info and
missing columns degrade to NULL. Artifact lands at
state/repos/<repo>/thread_recon.json (mirrors pr_meta's per-repo save).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

MAX_THREADS = 20
MAX_MESSAGES_PER_THREAD = 12
EXCERPT_LIMIT = 160

# Review-comment columns we read; any missing in a drifted schema → NULL.
_COMMENT_COLUMNS = (
    "pull_request_id", "github_comment_id", "in_reply_to_github_comment_id",
    "user_login", "path", "line", "body", "created_at",
)
# PR columns we read for the number map.
_PR_COLUMNS = ("id", "number", "author_login")


def _excerpt(body, limit: int = EXCERPT_LIMIT) -> str:
    """Collapse newlines to spaces, strip, truncate with a trailing ellipsis."""
    if not body:
        return ""
    text = " ".join(str(body).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _key(value):
    """Normalize a comment id to a stable string key (ids arrive as int)."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _present_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column set on `table`, empty if the table is absent."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return set()
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def _select_cols(present: set[str], wanted) -> str:
    return ", ".join((c if c in present else f"NULL AS {c}") for c in wanted)


def analyze_repo(repo: str, learner_login: str = "DongKey777",
                 state_root: Path = DEFAULT_STATE_ROOT,
                 now: float | None = None) -> dict:
    """Build the thread_recon artifact for one repo. Read-only, drift safe."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    empty_counts = {"prs_with_threads": 0, "threads": 0, "total_messages": 0}

    def _missing() -> dict:
        return {
            "repo": repo,
            "status": "missing_archive",
            "learner_login": learner_login,
            "threads": [],
            "counts": dict(empty_counts),
            "generated_at": generated_at,
        }

    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return _missing()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        pr_cols = _present_columns(conn, "pull_requests_current")
        comment_cols = _present_columns(
            conn, "pull_request_review_comments_current")
        if not pr_cols or not comment_cols:
            return _missing()

        pr_select = _select_cols(pr_cols, _PR_COLUMNS)
        try:
            pr_rows = conn.execute(
                f"SELECT {pr_select} FROM pull_requests_current"
            ).fetchall()
        except sqlite3.OperationalError:
            pr_rows = []

        comment_select = _select_cols(comment_cols, _COMMENT_COLUMNS)
        try:
            comment_rows = conn.execute(
                f"SELECT {comment_select} "
                "FROM pull_request_review_comments_current"
            ).fetchall()
        except sqlite3.OperationalError:
            comment_rows = []
    finally:
        conn.close()

    # Map learner-owned PR ids → PR number; threads are built only on these.
    learner_pr_number: dict = {}
    for r in pr_rows:
        if r["author_login"] == learner_login and r["id"] is not None:
            learner_pr_number[r["id"]] = r["number"]

    # Keep only comments on the learner's own PRs.
    own_comments = [r for r in comment_rows
                    if r["pull_request_id"] in learner_pr_number]

    # Index comments by their github_comment_id so replies can resolve parents.
    by_id: dict = {}
    for r in own_comments:
        cid = _key(r["github_comment_id"])
        if cid is not None:
            by_id[cid] = r

    # Resolve each comment's reply-chain back to its root id.
    def _root_id(comment) -> str | None:
        cid = _key(comment["github_comment_id"])
        if cid is None:
            return None
        seen = set()
        cur = comment
        while True:
            cur_id = _key(cur["github_comment_id"])
            parent_id = _key(cur["in_reply_to_github_comment_id"])
            if parent_id is None:
                return cur_id
            if parent_id in seen or parent_id not in by_id:
                # Broken / dangling parent — treat this node as the root.
                return cur_id
            seen.add(cur_id)
            cur = by_id[parent_id]

    # Group comments by resolved root id.
    groups: dict = {}
    for r in own_comments:
        root = _root_id(r)
        if root is None:
            continue
        groups.setdefault(root, []).append(r)

    threads: list[dict] = []
    prs_with_threads: set = set()
    total_messages = 0

    for root_id, members in groups.items():
        # A thread needs the root present plus at least one reply.
        root = by_id.get(root_id)
        if root is None:
            continue
        if len(members) < 2:  # root only, no reply → not a thread
            continue

        ordered = sorted(members, key=lambda r: (str(r["created_at"] or ""),
                                                 _key(r["github_comment_id"]) or ""))
        reply_count = len(ordered) - 1
        participants = sorted({r["user_login"] for r in ordered
                               if r["user_login"]})
        last_at = ordered[-1]["created_at"]

        messages = [{
            "author": r["user_login"] or "",
            "body_excerpt": _excerpt(r["body"]),
            "created_at": r["created_at"] or "",
            "is_learner": r["user_login"] == learner_login,
        } for r in ordered[:MAX_MESSAGES_PER_THREAD]]

        threads.append({
            "pr_number": learner_pr_number.get(root["pull_request_id"]),
            "path": root["path"] or "",
            "line": root["line"],
            "root_author": root["user_login"] or "",
            "root_excerpt": _excerpt(root["body"]),
            "reply_count": reply_count,
            "participants": participants,
            "last_at": last_at or "",
            "messages": messages,
        })
        prs_with_threads.add(root["pull_request_id"])
        total_messages += len(ordered)

    # Longest (most replies) first, then most-recent.
    threads.sort(key=lambda t: (t["reply_count"], str(t["last_at"])),
                 reverse=True)
    threads = threads[:MAX_THREADS]

    return {
        "repo": repo,
        "status": "ok",
        "learner_login": learner_login,
        "threads": threads,
        "counts": {
            "prs_with_threads": len(prs_with_threads),
            "threads": len(threads),
            "total_messages": total_messages,
        },
        "generated_at": generated_at,
    }


def save_thread_recon(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "thread_recon.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
