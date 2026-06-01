"""mission.temporal — family I (PR iteration-latency / cadence, mode temporal).

Repo-scoped, learner-scoped. Answers "내 PR 리뷰 주기 어때?" / "어디서 오래
멈췄지?": it surfaces the TIME dynamics of the learner's PR review cycle —
how long from PR open → first mentor review, the gaps between successive
mentor review rounds, time to merge, and STALLS (unusually long gaps where the
PR sat waiting for or after a mentor touch).

  prs — the learner's OWN PRs (author_login == learner), each carrying the
        opened/first-review/merge latencies, the count of mentor review rounds,
        the ordered hour-gaps between successive mentor touches, and any stalls.

"Mentor activity" = reviews + inline review comments authored by anyone who is
NOT the learner (the learner's own self-reviews/comments are ignored). Review
rounds count only mentor `pull_request_reviews_current` rows; round gaps and
stalls are computed over the merged+sorted set of mentor review and comment
timestamps.

Pure read-only over state/repos/<repo>/archive/prs.sqlite3. Schema-drift safe:
columns are probed via PRAGMA table_info and missing columns / tables degrade to
safe defaults. Timestamps are ISO8601 strings (may be NULL/empty / trailing
"Z"); every parse is guarded and unparseable values are treated as missing.
Artifact lands at state/repos/<repo>/temporal.json (mirrors pr_meta's per-repo
save).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

# A gap longer than this many hours between successive mentor touches counts as
# a stall (the PR sat waiting).
STALL_THRESHOLD_H = 72.0

_PR_COLUMNS = (
    "id", "number", "author_login", "created_at", "merged_at",
    "closed_at", "state", "html_url",
)
_COMMENT_COLUMNS = ("pull_request_id", "user_login", "created_at")


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_merged(merged_at) -> bool:
    return merged_at is not None and str(merged_at).strip() != ""


def _parse_ts(value) -> dt.datetime | None:
    """Parse an ISO8601 timestamp; trailing 'Z' → '+00:00'. Missing on error."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _hours_between(a: dt.datetime, b: dt.datetime) -> float:
    return round((b - a).total_seconds() / 3600.0, 1)


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


def _reviews_login_col(conn: sqlite3.Connection) -> str | None:
    """Reviews table drifts across repos: member/auth expose the reviewer as
    reviewer_login, waiting as user_login. Return whichever exists (reviewer_login
    preferred), or None if the table/both are absent. Picking the wrong name
    silently drops every formal review (_select_backfilled NULLs it), so this is
    detected, never assumed."""
    cols = _present_columns(conn, "pull_request_reviews_current")
    if "reviewer_login" in cols:
        return "reviewer_login"
    if "user_login" in cols:
        return "user_login"
    return None


def _select_backfilled(conn: sqlite3.Connection, table: str,
                       columns: tuple[str, ...]) -> list[sqlite3.Row]:
    """SELECT `columns` from `table`, back-filling absent columns as NULL.

    Returns [] if the table is missing or the query errors.
    """
    cols = _present_columns(conn, table)
    if not cols:
        return []
    select_cols = ", ".join(
        (c if c in cols else f"NULL AS {c}") for c in columns)
    try:
        return conn.execute(f"SELECT {select_cols} FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return []


def _empty_aggregate() -> dict:
    return {
        "n_prs": 0,
        "avg_time_to_first_review_h": None,
        "avg_time_to_merge_h": None,
        "avg_review_rounds": 0,
        "total_stalls": 0,
    }


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def analyze_repo(repo: str, learner_login: str = "DongKey777",
                 state_root: Path = DEFAULT_STATE_ROOT,
                 now: float | None = None) -> dict:
    """Build the temporal artifact for one repo. Read-only, schema-drift safe."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return {
            "repo": repo,
            "status": "missing_archive",
            "learner_login": learner_login,
            "prs": [],
            "aggregate": _empty_aggregate(),
            "counts": {"prs": 0, "prs_with_stalls": 0},
            "generated_at": generated_at,
        }

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        pr_rows = _select_backfilled(conn, "pull_requests_current", _PR_COLUMNS)
        if not pr_rows and not _present_columns(conn, "pull_requests_current"):
            return {
                "repo": repo,
                "status": "missing_archive",
                "learner_login": learner_login,
                "prs": [],
                "aggregate": _empty_aggregate(),
                "counts": {"prs": 0, "prs_with_stalls": 0},
                "generated_at": generated_at,
            }
        review_login_col = _reviews_login_col(conn)
        if review_login_col is None:
            review_rows = []
        else:
            try:
                review_rows = conn.execute(
                    f"SELECT pull_request_id, {review_login_col} AS user_login, "
                    "submitted_at FROM pull_request_reviews_current"
                ).fetchall()
            except sqlite3.OperationalError:
                review_rows = []
        comment_rows = _select_backfilled(
            conn, "pull_request_review_comments_current", _COMMENT_COLUMNS)
    finally:
        conn.close()

    # Mentor reviews per pull_request_id: only reviews authored by non-learner.
    mentor_reviews: dict[int, list[dt.datetime]] = {}
    for r in review_rows:
        if r["user_login"] is None or r["user_login"] == learner_login:
            continue
        ts = _parse_ts(r["submitted_at"])
        if ts is None:
            continue
        mentor_reviews.setdefault(_int(r["pull_request_id"]), []).append(ts)

    # Mentor inline comments per pull_request_id.
    mentor_comments: dict[int, list[dt.datetime]] = {}
    for r in comment_rows:
        if r["user_login"] is None or r["user_login"] == learner_login:
            continue
        ts = _parse_ts(r["created_at"])
        if ts is None:
            continue
        mentor_comments.setdefault(_int(r["pull_request_id"]), []).append(ts)

    learner_rows = [r for r in pr_rows if r["author_login"] == learner_login]

    enriched: list[dict] = []
    for r in learner_rows:
        pr_id = _int(r["id"])
        number = _int(r["number"])
        opened = _parse_ts(r["created_at"])
        merged = _parse_ts(r["merged_at"])

        reviews = sorted(mentor_reviews.get(pr_id, []))
        comments = mentor_comments.get(pr_id, [])
        # Merged + sorted mentor activity timestamps (reviews + comments).
        activity = sorted(reviews + comments)

        review_rounds = len(reviews)

        first_review = activity[0] if activity else None
        first_review_at = first_review.isoformat() if first_review else None
        if opened is not None and first_review is not None:
            time_to_first_review_h = _hours_between(opened, first_review)
        else:
            time_to_first_review_h = None

        round_gaps_h: list[float] = []
        stalls: list[dict] = []
        for prev, cur in zip(activity, activity[1:]):
            gap = _hours_between(prev, cur)
            round_gaps_h.append(gap)
            if gap > STALL_THRESHOLD_H:
                stalls.append({"after": prev.isoformat(), "gap_h": gap})

        if opened is not None and merged is not None:
            time_to_merge_h = _hours_between(opened, merged)
        else:
            time_to_merge_h = None

        enriched.append({
            "number": number,
            "merged": _is_merged(r["merged_at"]),
            "opened_at": opened.isoformat() if opened else None,
            "first_review_at": first_review_at,
            "time_to_first_review_h": time_to_first_review_h,
            "review_rounds": review_rounds,
            "time_to_merge_h": time_to_merge_h,
            "round_gaps_h": round_gaps_h,
            "stalls": stalls,
            "html_url": r["html_url"] or "",
            "_opened_sort": opened,
        })

    # Most-recent opened first; PRs with no opened_at sort last.
    enriched.sort(
        key=lambda p: (p["_opened_sort"] is not None, p["_opened_sort"]),
        reverse=True,
    )

    prs: list[dict] = []
    for p in enriched[:20]:
        p.pop("_opened_sort", None)
        prs.append(p)

    # Aggregate over all learner PRs (not just the capped 20), non-null safe.
    ttfr = [p["time_to_first_review_h"] for p in enriched
            if p["time_to_first_review_h"] is not None]
    ttm = [p["time_to_merge_h"] for p in enriched
           if p["time_to_merge_h"] is not None]
    rounds = [p["review_rounds"] for p in enriched]
    total_stalls = sum(len(p["stalls"]) for p in enriched)
    prs_with_stalls = sum(1 for p in enriched if p["stalls"])

    aggregate = {
        "n_prs": len(enriched),
        "avg_time_to_first_review_h": _avg(ttfr),
        "avg_time_to_merge_h": _avg(ttm),
        "avg_review_rounds": (round(sum(rounds) / len(rounds), 1)
                              if rounds else 0),
        "total_stalls": total_stalls,
    }

    return {
        "repo": repo,
        "status": "ok",
        "learner_login": learner_login,
        "prs": prs,
        "aggregate": aggregate,
        "counts": {"prs": len(enriched), "prs_with_stalls": prs_with_stalls},
        "generated_at": generated_at,
    }


def save_temporal(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "temporal.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
