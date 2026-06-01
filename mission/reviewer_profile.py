"""mission.reviewer_profile — F3 family C (reviewer-style surface, mode reviewer_profile).

Repo-scoped, reviewer-scoped. Answers "이 멘토 어떤 스타일이야?" / "내 리뷰어
패턴 정리해줘": given a repo + a reviewer login, it summarizes that reviewer's
habits from the archived PR reviews — how many root review-comments they leave,
which topics recur, which files become hotspots, their review-state mix
(APPROVED / CHANGES_REQUESTED / COMMENTED), and a few sample threads.

When reviewer_login is omitted, it auto-resolves to the top mentor on the
LEARNER's own PRs (the user_login with the most root review-comments on the
learner's pull requests).

Schema drift across repos is handled defensively:
  - the reviews table exposes the reviewer either as reviewer_login (member) or
    user_login (waiting); the actual column is detected from sqlite_master /
    table_info, never assumed.
  - submitted_at may be absent / NULL, so first_seen/last_seen fall back to the
    review-comment created_at range.
All optional-column queries are wrapped in try/except sqlite3.OperationalError.

Pure read-only over state/repos/<repo>/archive/prs.sqlite3. Artifact lands at
state/repos/<repo>/reviewer_profile.json (mirrors pr_review's per-repo save).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

_EXCERPT_CHARS = 150
_TOP_N = 10

# Korean 2+ syllables OR a CamelCase/identifier-ish ASCII token (3+ chars).
TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Z][A-Za-z]{2,}")

# Inherited noise from bin/reviewer plus the additional Korean stopwords the
# spec calls out (filler / non-topic words, not real review subjects).
STOP = frozenset(s.lower() for s in {
    "코드", "리뷰", "메서드", "클래스", "변수", "내용", "부분",
    "the", "and", "for", "you", "PR", "Issue", "Major", "Minor",
    "Refactor", "Note", "Tip", "Comment", "Fix", "Suggestion",
    # spec-mandated additional stopwords:
    "같아요", "어떻게", "있나요", "이렇게", "그리고", "그런데",
    "합니다", "같습니다", "부분", "경우", "생각", "코드",
    # demonstratives + politeness fillers (real tokens but not review topics —
    # otherwise "어떠한"/"궁금합니다" dominate the topic list over real subjects):
    "어떠한", "이러한", "그러한", "저러한", "어떤지",
    "궁금합니다", "궁금해요", "생각해요", "생각합니다", "같은데",
    "같아서", "좋을까요", "좋겠어요", "괜찮을", "어떨까요",
    "무엇인가요", "무엇일까요", "있을까요", "고려해볼", "어떨지",
})


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names for `table`, or empty set if the table is absent."""
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _reviews_login_col(conn: sqlite3.Connection) -> str | None:
    """Detect which column carries the reviewer login in the reviews table.

    member schema uses reviewer_login; waiting uses user_login. Returns None if
    the reviews table (or a usable login column) is absent."""
    if not _has_table(conn, "pull_request_reviews_current"):
        return None
    cols = _table_columns(conn, "pull_request_reviews_current")
    if "reviewer_login" in cols:
        return "reviewer_login"
    if "user_login" in cols:
        return "user_login"
    return None


def _resolve_top_mentor(conn: sqlite3.Connection, learner_login: str) -> str | None:
    """user_login with the most root review-comments on the learner's own PRs."""
    try:
        row = conn.execute(
            "SELECT c.user_login AS login, COUNT(*) AS n "
            "FROM pull_request_review_comments_current c "
            "JOIN pull_requests_current p ON p.id = c.pull_request_id "
            "WHERE p.author_login = ? "
            "AND c.in_reply_to_github_comment_id IS NULL "
            "AND c.user_login != ? "
            "GROUP BY c.user_login ORDER BY n DESC, c.user_login ASC LIMIT 1",
            (learner_login, learner_login),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or not row["login"]:
        return None
    return row["login"]


def _comments_on_learner_pr(conn: sqlite3.Connection, reviewer_login: str,
                            learner_login: str) -> int:
    """Root review-comments by this reviewer on the learner's own PRs."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n "
            "FROM pull_request_review_comments_current c "
            "JOIN pull_requests_current p ON p.id = c.pull_request_id "
            "WHERE c.user_login = ? AND p.author_login = ? "
            "AND c.in_reply_to_github_comment_id IS NULL",
            (reviewer_login, learner_login),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["n"]) if row else 0


def _review_aggregates(conn: sqlite3.Connection, reviewer_login: str
                       ) -> tuple[dict[str, int], int, str | None, str | None]:
    """(review_states, prs_reviewed, first_submitted, last_submitted).

    Tolerates absence of the reviews table or submitted_at column."""
    states = {"APPROVED": 0, "CHANGES_REQUESTED": 0, "COMMENTED": 0}
    prs_reviewed = 0
    first_sub = last_sub = None

    login_col = _reviews_login_col(conn)
    if login_col is None:
        return states, prs_reviewed, first_sub, last_sub

    cols = _table_columns(conn, "pull_request_reviews_current")
    has_state = "state" in cols
    has_pid = "pull_request_id" in cols
    has_submitted = "submitted_at" in cols

    if has_state:
        try:
            for r in conn.execute(
                f"SELECT state, COUNT(*) AS n "
                f"FROM pull_request_reviews_current WHERE {login_col} = ? "
                f"GROUP BY state",
                (reviewer_login,),
            ):
                st = (r["state"] or "").upper()
                if st in states:
                    states[st] += int(r["n"])
                elif st:
                    states[st] = states.get(st, 0) + int(r["n"])
        except sqlite3.OperationalError:
            pass

    if has_pid:
        try:
            row = conn.execute(
                f"SELECT COUNT(DISTINCT pull_request_id) AS n "
                f"FROM pull_request_reviews_current WHERE {login_col} = ?",
                (reviewer_login,),
            ).fetchone()
            prs_reviewed = int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            prs_reviewed = 0

    if has_submitted:
        try:
            row = conn.execute(
                f"SELECT MIN(submitted_at) AS lo, MAX(submitted_at) AS hi "
                f"FROM pull_request_reviews_current WHERE {login_col} = ? "
                f"AND submitted_at IS NOT NULL",
                (reviewer_login,),
            ).fetchone()
            if row:
                first_sub, last_sub = row["lo"], row["hi"]
        except sqlite3.OperationalError:
            pass

    return states, prs_reviewed, first_sub, last_sub


def analyze(repo: str, reviewer_login: str | None = None,
            learner_login: str = "DongKey777",
            state_root: Path = DEFAULT_STATE_ROOT,
            now: float | None = None) -> dict:
    """Build the reviewer_profile artifact for one repo + reviewer. Read-only."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    def _empty(status: str, login: str | None) -> dict:
        return {
            "repo": repo,
            "status": status,
            "reviewer_login": login,
            "nickname": login,
            "comments_total": 0,
            "comments_on_learner_pr": 0,
            "prs_reviewed": 0,
            "review_states": {"APPROVED": 0, "CHANGES_REQUESTED": 0,
                              "COMMENTED": 0},
            "first_seen": None,
            "last_seen": None,
            "recurring_topics": [],
            "hotspot_files": [],
            "sample_threads": [],
            "counts": {"recurring_topics": 0, "hotspot_files": 0,
                       "sample_threads": 0},
            "generated_at": generated_at,
        }

    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return _empty("missing_archive", reviewer_login)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "pull_request_review_comments_current"):
            return _empty("missing_archive", reviewer_login)

        resolved = reviewer_login
        if resolved is None:
            resolved = _resolve_top_mentor(conn, learner_login)
            if resolved is None:
                return _empty("no_data", None)

        # Root review-comments by this reviewer across the whole repo.
        try:
            rows = conn.execute(
                "SELECT body, path, line, created_at, pull_request_id "
                "FROM pull_request_review_comments_current "
                "WHERE user_login = ? "
                "AND in_reply_to_github_comment_id IS NULL "
                "ORDER BY created_at DESC",
                (resolved,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        if not rows:
            return _empty("unknown_reviewer", resolved)

        topic_counter: Counter = Counter()
        path_counter: Counter = Counter()
        samples: list[dict] = []
        comment_dates: list[str] = []
        for r in rows:
            body = r["body"] or ""
            toks = [t for t in TOKEN_RE.findall(body)
                    if t.lower() not in STOP and len(t) >= 3]
            topic_counter.update(toks)
            if r["path"]:
                path_counter[r["path"].rsplit("/", 1)[-1]] += 1
            if r["created_at"]:
                comment_dates.append(r["created_at"])
            if len(samples) < 3:
                samples.append({
                    "path": r["path"],
                    "line": r["line"],
                    "body_excerpt": body[:_EXCERPT_CHARS],
                })

        states, prs_reviewed, first_sub, last_sub = _review_aggregates(
            conn, resolved)
        on_learner = _comments_on_learner_pr(conn, resolved, learner_login)

        # first/last_seen: prefer review submitted_at range, else comment range.
        comment_lo = min(comment_dates) if comment_dates else None
        comment_hi = max(comment_dates) if comment_dates else None
        first_seen = first_sub or comment_lo
        last_seen = last_sub or comment_hi

        recurring = [{"topic": t, "n": n}
                     for t, n in topic_counter.most_common(_TOP_N)]
        hotspots = [{"file": f, "n": n}
                    for f, n in path_counter.most_common(_TOP_N)]

        return {
            "repo": repo,
            "status": "ok",
            "reviewer_login": resolved,
            "nickname": resolved,
            "comments_total": len(rows),
            "comments_on_learner_pr": on_learner,
            "prs_reviewed": prs_reviewed,
            "review_states": states,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "recurring_topics": recurring,
            "hotspot_files": hotspots,
            "sample_threads": samples,
            "counts": {
                "recurring_topics": len(recurring),
                "hotspot_files": len(hotspots),
                "sample_threads": len(samples),
            },
            "generated_at": generated_at,
        }
    finally:
        conn.close()


def save_reviewer_profile(report: dict,
                          state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "reviewer_profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
