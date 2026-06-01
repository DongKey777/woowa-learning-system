"""Tests for mission.temporal — family I (mode temporal)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.temporal import (  # noqa: E402
    STALL_THRESHOLD_H,
    analyze_repo,
    save_temporal,
)

LEARNER = "DongKey777"


def _seed_archive(state_root: Path, repo: str = "demo") -> None:
    """Minimal prs.sqlite3 mirroring the verified temporal-relevant columns.

    Learner PRs:
      - PR 391 (id 1): opened 2026-05-28T00:00:00Z, NOT merged. Mentor (jurlring)
        comments at +12h and reviews at +24h → first review 12h after open,
        one gap of 12h between comment→review, 1 review round. Also has a
        learner self-review/comment that must be ignored.
      - PR 392 (id 2): opened 2026-05-20T00:00:00Z, merged +48h. One mentor
        review at +1h, then a SECOND mentor review at +96h → a 95h gap > 72h
        threshold = one stall. 2 review rounds.
      - PR 393 (id 3): opened 2026-05-10T00:00:00Z, NO mentor activity →
        first_review_at / time_to_first_review_h null, 0 rounds.
    Cohort PR 100 (id 9) by crew-a is present but ignored (not the learner).
    """
    db_dir = state_root / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    db = db_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            created_at TEXT, merged_at TEXT, closed_at TEXT, state TEXT,
            html_url TEXT
        );
        CREATE TABLE pull_request_reviews_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER, user_login TEXT,
            state TEXT, submitted_at TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER, user_login TEXT,
            created_at TEXT
        );

        INSERT INTO pull_requests_current VALUES
          (1, 391, 'DongKey777', '2026-05-28T00:00:00Z', '', '', 'open',
           'https://x/391');
        INSERT INTO pull_requests_current VALUES
          (2, 392, 'DongKey777', '2026-05-20T00:00:00Z',
           '2026-05-22T00:00:00Z', '2026-05-22T00:00:00Z', 'closed',
           'https://x/392');
        INSERT INTO pull_requests_current VALUES
          (3, 393, 'DongKey777', '2026-05-10T00:00:00Z', '', '', 'open',
           'https://x/393');
        INSERT INTO pull_requests_current VALUES
          (9, 100, 'crew-a', '2026-05-01T00:00:00Z', '', '', 'open',
           'https://x/100');

        -- PR 391: mentor review +24h, mentor comment +12h, learner self-review.
        INSERT INTO pull_request_reviews_current VALUES
          (1, 1, 'jurlring', 'CHANGES_REQUESTED', '2026-05-29T00:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (2, 1, 'DongKey777', 'COMMENTED', '2026-05-29T06:00:00Z');
        INSERT INTO pull_request_review_comments_current VALUES
          (1, 1, 'jurlring', '2026-05-28T12:00:00Z');
        INSERT INTO pull_request_review_comments_current VALUES
          (2, 1, 'DongKey777', '2026-05-28T18:00:00Z');

        -- PR 392: mentor review +1h then +96h (95h gap = stall).
        INSERT INTO pull_request_reviews_current VALUES
          (3, 2, 'jurlring', 'COMMENTED', '2026-05-20T01:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (4, 2, 'jurlring', 'APPROVED', '2026-05-24T00:00:00Z');
    """)
    conn.commit()
    conn.close()


def _seed_archive_reviewer_login_schema(state_root: Path,
                                        repo: str = "demo2") -> None:
    """Same data as _seed_archive but the reviews table exposes the reviewer as
    `reviewer_login` (member/auth schema) instead of `user_login` (waiting).
    Guards the schema-drift detector: querying the wrong column name silently
    NULLs every review, so review_rounds must still resolve from reviewer_login."""
    db_dir = state_root / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "prs.sqlite3"))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            created_at TEXT, merged_at TEXT, closed_at TEXT, state TEXT,
            html_url TEXT
        );
        CREATE TABLE pull_request_reviews_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER, reviewer_login TEXT,
            state TEXT, submitted_at TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER, user_login TEXT,
            created_at TEXT
        );
        INSERT INTO pull_requests_current VALUES
          (1, 391, 'DongKey777', '2026-05-28T00:00:00Z', '', '', 'open',
           'https://x/391');
        -- two mentor reviews (reviewer_login) + one learner self-review.
        INSERT INTO pull_request_reviews_current VALUES
          (1, 1, 'hyeonic', 'CHANGES_REQUESTED', '2026-05-29T00:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (2, 1, 'hyeonic', 'APPROVED', '2026-05-29T02:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (3, 1, 'DongKey777', 'COMMENTED', '2026-05-29T03:00:00Z');
    """)
    conn.commit()
    conn.close()


def test_reviewer_login_schema_counts_mentor_reviews(tmp_path: Path) -> None:
    # Regression guard: member/auth repos store the reviewer as reviewer_login.
    # Before the schema-drift fix, temporal queried user_login → NULL → 0 rounds
    # for every heavily-reviewed PR (e.g. member PR#390 with 3 real reviews).
    _seed_archive_reviewer_login_schema(tmp_path, "demo2")
    rep = analyze_repo("demo2", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    pr = {p["number"]: p for p in rep["prs"]}[391]
    assert pr["review_rounds"] == 2  # two mentor reviews, learner self-review ignored
    assert pr["first_review_at"] == "2026-05-29T00:00:00+00:00"


def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze_repo("nonexistent", LEARNER, tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["prs"] == []
    assert rep["aggregate"] == {
        "n_prs": 0, "avg_time_to_first_review_h": None,
        "avg_time_to_merge_h": None, "avg_review_rounds": 0, "total_stalls": 0}
    assert rep["counts"] == {"prs": 0, "prs_with_stalls": 0}


def test_time_to_first_review_from_timestamps(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    pr = by_number[391]
    # opened 05-28T00:00, earliest mentor activity = comment at +12h.
    assert pr["opened_at"] == "2026-05-28T00:00:00+00:00"
    assert pr["first_review_at"] == "2026-05-28T12:00:00+00:00"
    assert pr["time_to_first_review_h"] == 12.0


def test_review_rounds_counts_only_mentor(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    # PR 391 has 1 mentor review + 1 learner self-review → only mentor counted.
    assert by_number[391]["review_rounds"] == 1
    # PR 392 has 2 mentor reviews.
    assert by_number[392]["review_rounds"] == 2


def test_long_gap_produces_stall(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    pr = by_number[392]
    # mentor reviews at +1h then +96h → gap of 95h > 72h threshold.
    assert pr["round_gaps_h"] == [95.0]
    assert len(pr["stalls"]) == 1
    stall = pr["stalls"][0]
    assert stall["gap_h"] == 95.0
    assert stall["gap_h"] > STALL_THRESHOLD_H
    assert stall["after"] == "2026-05-20T01:00:00+00:00"
    # PR 391 gap is 12h (comment→review) → no stall.
    assert by_number[391]["stalls"] == []


def test_time_to_merge_merged_vs_unmerged(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    # PR 392 merged 48h after open.
    assert by_number[392]["merged"] is True
    assert by_number[392]["time_to_merge_h"] == 48.0
    # PR 391 unmerged → null.
    assert by_number[391]["merged"] is False
    assert by_number[391]["time_to_merge_h"] is None


def test_no_mentor_activity_nulls(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    pr = by_number[393]
    assert pr["first_review_at"] is None
    assert pr["time_to_first_review_h"] is None
    assert pr["review_rounds"] == 0
    assert pr["round_gaps_h"] == []
    assert pr["stalls"] == []


def test_aggregate_averages_over_non_null(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    agg = rep["aggregate"]
    # 3 learner PRs total (cohort PR excluded).
    assert agg["n_prs"] == 3
    # time_to_first_review non-null only for 391 (12h) and 392 (1h) → avg 6.5.
    assert agg["avg_time_to_first_review_h"] == 6.5
    # time_to_merge non-null only for 392 (48h) → avg 48.0.
    assert agg["avg_time_to_merge_h"] == 48.0
    # review_rounds: 391=1, 392=2, 393=0 → avg 1.0.
    assert agg["avg_review_rounds"] == 1.0
    # one stall total (PR 392).
    assert agg["total_stalls"] == 1
    assert rep["counts"] == {"prs": 3, "prs_with_stalls": 1}


def test_save_round_trip_and_key_parity(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    out = save_temporal(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "temporal.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    documented = ("repo", "status", "learner_login", "prs", "aggregate",
                  "counts", "generated_at")
    assert set(data.keys()) == set(documented)
    assert len(documented) == 7
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["learner_login"] == LEARNER
