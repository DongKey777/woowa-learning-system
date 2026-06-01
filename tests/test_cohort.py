"""Tests for mission.cohort — family F (mode cohort)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.cohort import analyze_repo, save_cohort  # noqa: E402

LEARNER = "DongKey777"


def _seed_archive(state_root: Path, repo: str = "demo") -> None:
    """Minimal prs.sqlite3 mirroring the verified real schema.

    pull_request_reviews_current joins on pull_request_id == PR.id (NOT number)
    and the reviewer column is reviewer_login. A review whose reviewer == the
    PR's own author is a self-review and is NOT a "received review".

    Cohort (author != learner, not bot): three PRs, by id.
      total_changes : 100, 300, 500  → median 300
      changed_files :   2,   6,  10  → median 6
      commits       :   1,   3,   5  → median 3
      reviews_recv  :   0,   1,   2  → median 1
      review_rounds :   0,   1,   2  → median 1

    Learner PR (id 5, number 391): total_changes 400, 8 files, 4 commits, and
    three reviews (mentor CHANGES_REQUESTED, a self-COMMENTED that must NOT
    count, mentor APPROVED) → reviews_received 2, review_rounds 2, approved.

    One bot PR is seeded that must be excluded from the cohort entirely.
    """
    db_dir = state_root / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    db = db_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY,
            number INTEGER, title TEXT, body TEXT, state TEXT,
            author_login TEXT, changed_files INTEGER, commits_count INTEGER,
            additions INTEGER, deletions INTEGER, merged_at TEXT,
            html_url TEXT, created_at TEXT
        );
        CREATE TABLE pull_request_reviews_current (
            id INTEGER PRIMARY KEY,
            pull_request_id INTEGER, reviewer_login TEXT, state TEXT,
            submitted_at TEXT
        );
        -- cohort PRs (author != learner, not bot)
        INSERT INTO pull_requests_current VALUES
          (1, 100, '1단계', '본문', 'closed', 'crew-a', 2, 1,
           90, 10, '2026-05-01T00:00:00Z', 'https://x/100', '2026-04-30');
        INSERT INTO pull_requests_current VALUES
          (2, 101, '2단계', '본문', 'closed', 'crew-b', 6, 3,
           270, 30, '2026-05-02T00:00:00Z', 'https://x/101', '2026-05-01');
        INSERT INTO pull_requests_current VALUES
          (3, 102, '3단계', '본문', 'open', 'crew-c', 10, 5,
           450, 50, '', 'https://x/102', '2026-05-02');
        -- bot PR (must be excluded from cohort)
        INSERT INTO pull_requests_current VALUES
          (4, 103, 'chore: bump', 'bot', 'closed',
           'coderabbitai[bot]', 1, 1, 10, 1, '2026-05-03T00:00:00Z',
           'https://x/103', '2026-05-03');
        -- learner PR (id 5)
        INSERT INTO pull_requests_current VALUES
          (5, 391, '대기 기능', '본문', 'open', 'DongKey777', 8, 4,
           380, 20, '', 'https://x/391', '2026-05-04');

        -- cohort reviews:
        --   crew-a PR (id 1): no reviews → 0/0
        --   crew-b PR (id 2): one mentor review → 1/1
        INSERT INTO pull_request_reviews_current VALUES
          (10, 2, 'mentor-x', 'COMMENTED', '2026-05-02T10:00:00Z');
        --   crew-c PR (id 3): two mentor reviews on distinct timestamps → 2/2
        INSERT INTO pull_request_reviews_current VALUES
          (11, 3, 'mentor-x', 'CHANGES_REQUESTED', '2026-05-02T11:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (12, 3, 'mentor-x', 'APPROVED', '2026-05-02T12:00:00Z');

        -- learner PR (id 5) reviews: mentor CR + self COMMENTED (excluded) +
        -- mentor APPROVED → reviews_received 2, review_rounds 2, approved True.
        INSERT INTO pull_request_reviews_current VALUES
          (20, 5, 'mentor-x', 'CHANGES_REQUESTED', '2026-05-04T10:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (21, 5, 'DongKey777', 'COMMENTED', '2026-05-04T11:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (22, 5, 'mentor-x', 'APPROVED', '2026-05-04T12:00:00Z');
    """)
    conn.commit()
    conn.close()


def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze_repo("nonexistent", LEARNER, tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["learner_prs"] == []
    assert rep["comparison"] == []
    assert rep["cohort"] == {"n": 0, "median_total_changes": 0,
                             "median_changed_files": 0, "median_commits": 0,
                             "median_reviews_received": 0,
                             "median_review_rounds": 0}
    assert rep["counts"] == {"cohort_prs": 0, "learner_prs": 0}


def test_cohort_medians_exclude_learner_and_bot(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    # 3 cohort PRs only (learner + bot excluded). Hand-computed medians:
    #   total_changes: median(100, 300, 500) = 300
    #   changed_files: median(2, 6, 10) = 6
    #   commits:       median(1, 3, 5) = 3
    #   reviews_recv:  median(0, 1, 2) = 1
    #   review_rounds: median(0, 1, 2) = 1
    assert rep["cohort"] == {"n": 3, "median_total_changes": 300,
                             "median_changed_files": 6, "median_commits": 3,
                             "median_reviews_received": 1,
                             "median_review_rounds": 1}
    assert rep["counts"] == {"cohort_prs": 3, "learner_prs": 1}


def test_reviews_received_excludes_self_review(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    pr = rep["learner_prs"][0]
    # 3 review rows on the learner PR, but the self-COMMENTED one is excluded.
    assert pr["reviews_received"] == 2
    assert pr["review_rounds"] == 2


def _seed_archive_user_login_schema(state_root: Path, repo: str = "demo2") -> None:
    """Same learner PR as _seed_archive but the reviews table exposes the
    reviewer as `user_login` (waiting schema) not `reviewer_login` (member).
    Guards the schema-drift detector: querying reviewer_login here back-fills
    NULL → the self-review filter silently fails → self-reviews over-count."""
    db_dir = state_root / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "prs.sqlite3"))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY,
            number INTEGER, title TEXT, body TEXT, state TEXT,
            author_login TEXT, changed_files INTEGER, commits_count INTEGER,
            additions INTEGER, deletions INTEGER, merged_at TEXT,
            html_url TEXT, created_at TEXT
        );
        CREATE TABLE pull_request_reviews_current (
            id INTEGER PRIMARY KEY,
            pull_request_id INTEGER, user_login TEXT, state TEXT,
            submitted_at TEXT
        );
        -- one cohort PR so status is ok (cohort non-empty).
        INSERT INTO pull_requests_current VALUES
          (2, 101, '2단계', '본문', 'closed', 'crew-b', 6, 3,
           270, 30, '2026-05-02T00:00:00Z', 'https://x/101', '2026-05-01');
        INSERT INTO pull_requests_current VALUES
          (5, 391, '대기 기능', '본문', 'open', 'DongKey777', 8, 4,
           380, 20, '', 'https://x/391', '2026-05-04');
        -- cohort review (counts as 1 received for crew-b).
        INSERT INTO pull_request_reviews_current VALUES
          (10, 2, 'mentor-x', 'COMMENTED', '2026-05-02T10:00:00Z');
        -- learner PR: mentor CR + self COMMENTED (must be excluded) + mentor APPROVED.
        INSERT INTO pull_request_reviews_current VALUES
          (20, 5, 'mentor-x', 'CHANGES_REQUESTED', '2026-05-04T10:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (21, 5, 'DongKey777', 'COMMENTED', '2026-05-04T11:00:00Z');
        INSERT INTO pull_request_reviews_current VALUES
          (22, 5, 'mentor-x', 'APPROVED', '2026-05-04T12:00:00Z');
    """)
    conn.commit()
    conn.close()


def test_self_review_excluded_under_user_login_schema(tmp_path: Path) -> None:
    # Regression guard for the waiting (user_login) reviews schema. Before the
    # fix, cohort queried reviewer_login → NULL → the self-COMMENTED review was
    # counted, inflating reviews_received (69% of waiting reviews are self-reviews).
    _seed_archive_user_login_schema(tmp_path, "demo2")
    rep = analyze_repo("demo2", LEARNER, tmp_path)
    pr = rep["learner_prs"][0]
    assert pr["reviews_received"] == 2  # self-review excluded
    assert pr["review_rounds"] == 2
    assert pr["approved"] is True


def test_approved_true_when_approved_review_exists(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    pr = rep["learner_prs"][0]
    assert pr["approved"] is True
    # The crew-a cohort PR (id 1) has no reviews → not approved; verify via a
    # learner PR with no APPROVED row.
    db = tmp_path / "repos" / "demo" / "archive" / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO pull_requests_current VALUES "
        "(6, 392, '추가', '본문', 'open', 'DongKey777', 3, 2, 100, 10, '', "
        "'https://x/392', '2026-05-05')")
    conn.execute(
        "INSERT INTO pull_request_reviews_current VALUES "
        "(30, 6, 'mentor-x', 'CHANGES_REQUESTED', '2026-05-05T10:00:00Z')")
    conn.commit()
    conn.close()
    rep2 = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {p["number"]: p for p in rep2["learner_prs"]}
    assert by_number[392]["approved"] is False


def test_percentile_rank_above_and_below_median(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_metric = {row["metric"]: row for row in rep["comparison"]}

    # Learner most-recent PR (number 391): total_changes 400.
    # Cohort total_changes = [100, 300, 500]; values <= 400 → {100, 300} = 2/3.
    tc = by_metric["total_changes"]
    assert tc["learner_value"] == 400
    assert tc["cohort_median"] == 300
    assert tc["percentile"] == round(100 * 2 / 3)  # 67, above the median

    # changed_files: learner 8, cohort [2, 6, 10]; <= 8 → {2, 6} = 2/3 = 67.
    cf = by_metric["changed_files"]
    assert cf["learner_value"] == 8
    assert cf["cohort_median"] == 6
    assert cf["percentile"] == round(100 * 2 / 3)

    # All five metric rows present, in the documented order.
    assert [r["metric"] for r in rep["comparison"]] == [
        "total_changes", "changed_files", "commits_count",
        "reviews_received", "review_rounds"]


def test_percentile_below_median(tmp_path: Path) -> None:
    """A learner PR strictly below the whole cohort yields a low percentile."""
    _seed_archive(tmp_path, "demo")
    db = tmp_path / "repos" / "demo" / "archive" / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    # Replace the learner PR with a tiny one: total_changes 50 (< 100,300,500).
    conn.execute("DELETE FROM pull_request_reviews_current WHERE pull_request_id=5")
    conn.execute("DELETE FROM pull_requests_current WHERE id=5")
    conn.execute(
        "INSERT INTO pull_requests_current VALUES "
        "(5, 391, '작은 PR', '본문', 'open', 'DongKey777', 1, 1, 40, 10, '', "
        "'https://x/391', '2026-05-04')")
    conn.commit()
    conn.close()
    rep = analyze_repo("demo", LEARNER, tmp_path)
    tc = next(r for r in rep["comparison"] if r["metric"] == "total_changes")
    # total_changes 50; cohort [100,300,500]; none <= 50 → 0/3 = 0.
    assert tc["learner_value"] == 50
    assert tc["percentile"] == 0


def test_no_cohort_status(tmp_path: Path) -> None:
    """Only the learner (and a bot) authored PRs → no_cohort."""
    db_dir = tmp_path / "repos" / "solo" / "archive"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "prs.sqlite3"))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, title TEXT, body TEXT,
            state TEXT, author_login TEXT, changed_files INTEGER,
            commits_count INTEGER, additions INTEGER, deletions INTEGER,
            merged_at TEXT, html_url TEXT, created_at TEXT
        );
        CREATE TABLE pull_request_reviews_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER,
            reviewer_login TEXT, state TEXT, submitted_at TEXT
        );
        INSERT INTO pull_requests_current VALUES
          (1, 391, '대기', '본문', 'open', 'DongKey777', 8, 4, 380, 20, '',
           'https://x/391', '2026-05-04');
        INSERT INTO pull_requests_current VALUES
          (2, 392, 'chore', 'bot', 'closed', 'dependabot[bot]', 1, 1, 5, 1,
           '', 'https://x/392', '2026-05-04');
    """)
    conn.commit()
    conn.close()
    rep = analyze_repo("solo", LEARNER, tmp_path)
    assert rep["status"] == "no_cohort"
    assert rep["cohort"]["n"] == 0
    assert rep["learner_prs"] == []
    assert rep["comparison"] == []


def test_no_learner_pr_status(tmp_path: Path) -> None:
    """A cohort exists but the learner authored nothing → no_learner_pr,
    cohort medians still populated."""
    _seed_archive(tmp_path, "demo")
    db = tmp_path / "repos" / "demo" / "archive" / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM pull_request_reviews_current WHERE pull_request_id=5")
    conn.execute("DELETE FROM pull_requests_current WHERE author_login='DongKey777'")
    conn.commit()
    conn.close()
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["status"] == "no_learner_pr"
    assert rep["cohort"]["n"] == 3
    assert rep["cohort"]["median_total_changes"] == 300
    assert rep["learner_prs"] == []
    assert rep["comparison"] == []
    assert rep["counts"] == {"cohort_prs": 3, "learner_prs": 0}


def test_save_round_trip_and_key_parity(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    out = save_cohort(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "cohort.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    documented = ("repo", "status", "learner_login", "cohort", "learner_prs",
                  "comparison", "counts", "generated_at")
    assert set(data.keys()) == set(documented)
    assert len(documented) == 8
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["learner_login"] == LEARNER
