"""mission.cohort — family F (learner-vs-cohort comparison, mode cohort).

Repo-scoped, learner-scoped. Answers "동기들 대비 내 PR 어느 정도야?":
it places the learner's PR RELATIVE TO the cohort on the same mission — PR
size (additions+deletions), changed files, commit count, reviews received, and
review rounds — each as the learner's value vs the cohort median plus a 0-100
percentile rank. Pure peer-context, read-only. Not an eval harness.

  cohort      — the cohort distribution medians (author_login != learner AND
                NOT NULL AND NOT a bot). The yardstick.
  learner_prs — the learner's OWN PRs, each carrying size, review counts, and
                whether any review APPROVED it.
  comparison  — per-metric rows for the learner's most-recent PR: the learner
                value, the cohort median, and the percentile rank.

Reads state/repos/<repo>/archive/prs.sqlite3 — two tables:
  pull_requests_current        : PR size + author (cohort/learner split)
  pull_request_reviews_current : reviews each PR received, joined on
                                 pull_request_id == pull_requests_current.id

"reviews received" for a PR counts review rows authored by someone OTHER than
the PR author (mentor / peer); the author's own COMMENTED reviews do not count.
review_rounds = distinct submitted_at among those received reviews. approved =
any received review with state == 'APPROVED'.

Schema-drift safe: columns are probed via PRAGMA table_info and missing columns
degrade to safe defaults; an absent table/db yields status "missing_archive".
Artifact lands at state/repos/<repo>/cohort.json.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import statistics
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

# Columns we read off pull_requests_current; any missing degrades to NULL→0.
_PR_COLUMNS = (
    "id", "number", "author_login",
    "additions", "deletions", "changed_files", "commits_count",
    "html_url",
)
# Metrics compared against the cohort, in stable display order.
_METRICS = (
    "total_changes",
    "changed_files",
    "commits_count",
    "reviews_received",
    "review_rounds",
)

_EMPTY_COHORT = {
    "n": 0,
    "median_total_changes": 0,
    "median_changed_files": 0,
    "median_commits": 0,
    "median_reviews_received": 0,
    "median_review_rounds": 0,
}


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _median_int(values: list[int]) -> int:
    if not values:
        return 0
    return round(statistics.median(values))


def _percentile(cohort_values: list[int], learner_value: int) -> int:
    """Fraction of cohort PRs with value <= learner's value, as a 0-100 int."""
    if not cohort_values:
        return 0
    le = sum(1 for v in cohort_values if v <= learner_value)
    return round(100 * le / len(cohort_values))


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


def _fetch(conn: sqlite3.Connection, table: str, want_cols: tuple[str, ...]
           ) -> list[sqlite3.Row]:
    """SELECT want_cols from table, back-filling absent columns as NULL."""
    cols = _present_columns(conn, table)
    if not cols:
        return []
    select_cols = ", ".join(
        (c if c in cols else f"NULL AS {c}") for c in want_cols)
    try:
        return conn.execute(f"SELECT {select_cols} FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return []


def _reviews_login_col(conn: sqlite3.Connection) -> str | None:
    """Reviews table drifts: member/auth expose the reviewer as reviewer_login,
    waiting as user_login. Return whichever exists (reviewer_login preferred),
    else None. Detected, never assumed — back-filling the wrong name to NULL
    silently defeats the self-review filter and over-counts received reviews."""
    cols = _present_columns(conn, "pull_request_reviews_current")
    if "reviewer_login" in cols:
        return "reviewer_login"
    if "user_login" in cols:
        return "user_login"
    return None


def _fetch_reviews(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Reviews with the reviewer column normalized to reviewer_login across the
    member (reviewer_login) and waiting (user_login) schemas."""
    login_col = _reviews_login_col(conn)
    if login_col is None:
        return []
    try:
        return conn.execute(
            f"SELECT pull_request_id, {login_col} AS reviewer_login, state, "
            "submitted_at FROM pull_request_reviews_current"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _review_index(review_rows: list[sqlite3.Row], pr_author_by_id: dict
                  ) -> dict:
    """Map pull_request_id -> {received, rounds, approved} for non-author
    reviews. Reviews by the PR's own author (self-COMMENTED) are skipped."""
    index: dict[int, dict] = {}
    for r in review_rows:
        pr_id = r["pull_request_id"]
        if pr_id is None:
            continue
        pr_id = _int(pr_id)
        author = pr_author_by_id.get(pr_id)
        reviewer = r["reviewer_login"]
        if reviewer is not None and author is not None and reviewer == author:
            continue  # self-review, not a received review
        bucket = index.setdefault(
            pr_id, {"count": 0, "submitted": set(), "approved": False})
        bucket["count"] += 1
        if r["submitted_at"] is not None:
            bucket["submitted"].add(str(r["submitted_at"]))
        if r["state"] is not None and str(r["state"]) == "APPROVED":
            bucket["approved"] = True
    return index


def _pr_metrics(row: sqlite3.Row, review_index: dict) -> dict:
    pr_id = _int(row["id"])
    additions = _int(row["additions"])
    deletions = _int(row["deletions"])
    changed_files = _int(row["changed_files"])
    commits_count = _int(row["commits_count"])
    rv = review_index.get(pr_id, {"count": 0, "submitted": set(),
                                  "approved": False})
    return {
        "number": _int(row["number"]),
        "total_changes": additions + deletions,
        "changed_files": changed_files,
        "commits_count": commits_count,
        "reviews_received": rv["count"],
        "review_rounds": len(rv["submitted"]),
        "approved": bool(rv["approved"]),
        "html_url": row["html_url"] or "",
    }


def analyze_repo(repo: str, learner_login: str = "DongKey777",
                 state_root: Path = DEFAULT_STATE_ROOT,
                 now: float | None = None) -> dict:
    """Build the cohort comparison artifact for one repo. Read-only."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    def _empty(status: str, cohort: dict | None = None) -> dict:
        return {
            "repo": repo,
            "status": status,
            "learner_login": learner_login,
            "cohort": dict(cohort) if cohort is not None else dict(_EMPTY_COHORT),
            "learner_prs": [],
            "comparison": [],
            "counts": {"cohort_prs": cohort["n"] if cohort else 0,
                       "learner_prs": 0},
            "generated_at": generated_at,
        }

    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return _empty("missing_archive")

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if not _present_columns(conn, "pull_requests_current"):
            return _empty("missing_archive")
        pr_rows = _fetch(conn, "pull_requests_current", _PR_COLUMNS)
        review_rows = _fetch_reviews(conn)
    finally:
        conn.close()

    pr_author_by_id = {
        _int(r["id"]): r["author_login"] for r in pr_rows
    }
    review_index = _review_index(review_rows, pr_author_by_id)

    # Cohort: PRs not authored by the learner and not bot-authored.
    cohort_rows = [
        r for r in pr_rows
        if r["author_login"] is not None
        and r["author_login"] != learner_login
        and "[bot]" not in str(r["author_login"])
    ]
    cohort_metrics = [_pr_metrics(r, review_index) for r in cohort_rows]
    n = len(cohort_metrics)

    if n > 0:
        cohort = {
            "n": n,
            "median_total_changes": _median_int(
                [m["total_changes"] for m in cohort_metrics]),
            "median_changed_files": _median_int(
                [m["changed_files"] for m in cohort_metrics]),
            "median_commits": _median_int(
                [m["commits_count"] for m in cohort_metrics]),
            "median_reviews_received": _median_int(
                [m["reviews_received"] for m in cohort_metrics]),
            "median_review_rounds": _median_int(
                [m["review_rounds"] for m in cohort_metrics]),
        }
    else:
        cohort = dict(_EMPTY_COHORT)

    learner_rows = [r for r in pr_rows if r["author_login"] == learner_login]
    learner_metrics = [_pr_metrics(r, review_index) for r in learner_rows]
    learner_metrics.sort(key=lambda m: m["number"], reverse=True)

    # Status resolution: cohort emptiness vs learner emptiness. db/table were
    # present (else we already returned missing_archive), so empties are the
    # more-specific statuses.
    if n == 0:
        return _empty("no_cohort")
    if not learner_metrics:
        return _empty("no_learner_pr", cohort)

    learner_prs = [
        {k: m[k] for k in ("number", "total_changes", "changed_files",
                           "commits_count", "reviews_received",
                           "review_rounds", "approved", "html_url")}
        for m in learner_metrics[:20]
    ]

    # Comparison uses the learner's most-recent PR (already sorted desc).
    recent = learner_metrics[0]
    metric_to_cohort_median = {
        "total_changes": cohort["median_total_changes"],
        "changed_files": cohort["median_changed_files"],
        "commits_count": cohort["median_commits"],
        "reviews_received": cohort["median_reviews_received"],
        "review_rounds": cohort["median_review_rounds"],
    }
    cohort_value_lists = {
        "total_changes": [m["total_changes"] for m in cohort_metrics],
        "changed_files": [m["changed_files"] for m in cohort_metrics],
        "commits_count": [m["commits_count"] for m in cohort_metrics],
        "reviews_received": [m["reviews_received"] for m in cohort_metrics],
        "review_rounds": [m["review_rounds"] for m in cohort_metrics],
    }
    comparison = [
        {
            "metric": metric,
            "learner_value": recent[metric],
            "cohort_median": metric_to_cohort_median[metric],
            "percentile": _percentile(cohort_value_lists[metric],
                                      recent[metric]),
        }
        for metric in _METRICS
    ]

    return {
        "repo": repo,
        "status": "ok",
        "learner_login": learner_login,
        "cohort": cohort,
        "learner_prs": learner_prs,
        "comparison": comparison,
        "counts": {"cohort_prs": n, "learner_prs": len(learner_metrics)},
        "generated_at": generated_at,
    }


def save_cohort(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "cohort.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
