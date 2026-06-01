"""mission.pr_meta — family J (PR metadata hygiene, mode pr_meta).

Repo-scoped, learner-scoped. Answers "내 PR 위생 어때?" / "PR 너무 큰가?":
it assesses the learner's PR *metadata hygiene* purely from SQLite columns —
PR body quality, PR size relative to the cohort, and commit cohesion. No diff
parsing, no per-commit table (there is none); commits_count is the only commit
signal and merged is derived from merged_at (there is NO merged column).

  cohort — average additions / changed_files / commits over the cohort
           (author_login != learner AND NOT LIKE '%[bot]'). The yardstick the
           learner's PR size is measured against.
  prs    — the learner's OWN PRs, each carrying body_chars, size, commit
           cohesion, and a list of hygiene flags.

Flags per PR:
  no_body             — body is empty
  thin_body           — 0 < body_chars < THIN_BODY_CHARS
  large_pr            — total_changes > LARGE_PR_FACTOR * cohort.avg_additions
                        (when cohort n>0); else > LARGE_PR_ABS_FALLBACK
  low_commit_cohesion — files_per_commit > LOW_COHESION_FILES_PER_COMMIT
                        (each commit touches many files = unfocused)

Pure read-only over state/repos/<repo>/archive/prs.sqlite3. Schema-drift safe:
columns are probed via PRAGMA table_info and missing columns degrade to safe
defaults. Artifact lands at state/repos/<repo>/pr_meta.json (mirrors
pr_review's per-repo save).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

THIN_BODY_CHARS = 50
LARGE_PR_FACTOR = 1.5
LARGE_PR_ABS_FALLBACK = 3000
LOW_COHESION_FILES_PER_COMMIT = 8

# Columns we read; any missing in a drifted schema degrades to 0/empty.
_PR_COLUMNS = (
    "number", "title", "body", "author_login", "merged_at",
    "additions", "deletions", "changed_files", "commits_count", "html_url",
)


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_merged(merged_at) -> bool:
    return merged_at is not None and str(merged_at).strip() != ""


def _flags_for_pr(body_chars: int, total_changes: int, files_per_commit: float,
                  commits_count: int, cohort: dict) -> list[str]:
    flags: list[str] = []
    if body_chars == 0:
        flags.append("no_body")
    elif body_chars < THIN_BODY_CHARS:
        flags.append("thin_body")

    if cohort["n"] > 0:
        large = total_changes > LARGE_PR_FACTOR * cohort["avg_additions"]
    else:
        large = total_changes > LARGE_PR_ABS_FALLBACK
    if large:
        flags.append("large_pr")

    if commits_count > 0 and files_per_commit > LOW_COHESION_FILES_PER_COMMIT:
        flags.append("low_commit_cohesion")
    return flags


def _present_columns(conn: sqlite3.Connection) -> set[str]:
    """Column set on pull_requests_current, empty if the table is absent."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "pull_requests_current" not in tables:
        return set()
    try:
        return {r[1] for r in conn.execute(
            "PRAGMA table_info(pull_requests_current)")}
    except sqlite3.OperationalError:
        return set()


def analyze_repo(repo: str, learner_login: str = "DongKey777",
                 state_root: Path = DEFAULT_STATE_ROOT,
                 now: float | None = None) -> dict:
    """Build the pr_meta artifact for one repo. Read-only, schema-drift safe."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    empty_cohort = {"avg_additions": 0, "avg_changed_files": 0,
                    "avg_commits": 0, "n": 0}

    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return {
            "repo": repo,
            "status": "missing_archive",
            "learner_login": learner_login,
            "cohort": dict(empty_cohort),
            "prs": [],
            "counts": {"prs": 0, "flagged": 0},
            "generated_at": generated_at,
        }

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cols = _present_columns(conn)
        if not cols:
            return {
                "repo": repo,
                "status": "missing_archive",
                "learner_login": learner_login,
                "cohort": dict(empty_cohort),
                "prs": [],
                "counts": {"prs": 0, "flagged": 0},
                "generated_at": generated_at,
            }

        # Select only columns that actually exist; back-fill the rest as NULL.
        select_cols = ", ".join(
            (c if c in cols else f"NULL AS {c}") for c in _PR_COLUMNS)
        try:
            rows = conn.execute(
                f"SELECT {select_cols} FROM pull_requests_current"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        conn.close()

    # Cohort: PRs not authored by the learner and not bot-authored.
    cohort_rows = [
        r for r in rows
        if r["author_login"] is not None
        and r["author_login"] != learner_login
        and "[bot]" not in str(r["author_login"])
    ]
    n = len(cohort_rows)
    if n > 0:
        cohort = {
            "avg_additions": round(sum(_int(r["additions"]) for r in cohort_rows) / n),
            "avg_changed_files": round(sum(_int(r["changed_files"]) for r in cohort_rows) / n),
            "avg_commits": round(sum(_int(r["commits_count"]) for r in cohort_rows) / n),
            "n": n,
        }
    else:
        cohort = dict(empty_cohort)

    learner_rows = [r for r in rows if r["author_login"] == learner_login]
    learner_rows.sort(key=lambda r: _int(r["number"]))

    prs: list[dict] = []
    flagged = 0
    for r in learner_rows:
        additions = _int(r["additions"])
        deletions = _int(r["deletions"])
        changed_files = _int(r["changed_files"])
        commits_count = _int(r["commits_count"])
        total_changes = additions + deletions
        body_chars = len(r["body"]) if r["body"] else 0
        files_per_commit = (round(changed_files / commits_count, 2)
                            if commits_count > 0 else 0)
        flags = _flags_for_pr(body_chars, total_changes, files_per_commit,
                              commits_count, cohort)
        if flags:
            flagged += 1
        prs.append({
            "number": _int(r["number"]),
            "title": r["title"] or "",
            "merged": _is_merged(r["merged_at"]),
            "body_chars": body_chars,
            "additions": additions,
            "deletions": deletions,
            "total_changes": total_changes,
            "changed_files": changed_files,
            "commits_count": commits_count,
            "files_per_commit": files_per_commit,
            "flags": flags,
            "html_url": r["html_url"] or "",
        })

    return {
        "repo": repo,
        "status": "ok",
        "learner_login": learner_login,
        "cohort": cohort,
        "prs": prs,
        "counts": {"prs": len(prs), "flagged": flagged},
        "generated_at": generated_at,
    }


def save_pr_meta(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "pr_meta.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
