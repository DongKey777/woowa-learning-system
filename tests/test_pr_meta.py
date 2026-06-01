"""Tests for mission.pr_meta — family J (mode pr_meta)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.pr_meta import analyze_repo, save_pr_meta  # noqa: E402

LEARNER = "DongKey777"


def _seed_archive(state_root: Path, repo: str = "demo") -> None:
    """Minimal prs.sqlite3 with the verified pull_requests_current columns.

    Cohort (author != learner, not bot): three PRs.
      additions  : 1000, 2000, 6000  → avg 3000
      changed_files: 20, 40, 90       → avg 50
      commits    : 10, 20, 30         → avg 20
    Learner PRs: one huge (no body, way over 1.5*3000, 137/27 ≈ 5.07 files per
    commit so cohesion is fine but it stays large + no_body) and one clean small
    PR with a real Korean body and modest size (no flags). One bot PR is seeded
    that must be excluded from the cohort.
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
        -- cohort PRs (author != learner, not bot)
        INSERT INTO pull_requests_current VALUES
          (1, 100, '1단계 예약', '본문', 'closed', 'crew-a', 20, 10,
           1000, 50, '2026-05-01T00:00:00Z', 'https://x/100', '2026-04-30');
        INSERT INTO pull_requests_current VALUES
          (2, 101, '2단계 예약', '본문', 'closed', 'crew-b', 40, 20,
           2000, 100, '2026-05-02T00:00:00Z', 'https://x/101', '2026-05-01');
        INSERT INTO pull_requests_current VALUES
          (3, 102, '3단계 예약', '본문', 'open', 'crew-c', 90, 30,
           6000, 200, '', 'https://x/102', '2026-05-02');
        -- bot PR (must be excluded from cohort)
        INSERT INTO pull_requests_current VALUES
          (4, 103, 'chore: bump deps', 'bot body', 'closed',
           'coderabbitai[bot]', 5, 1, 50, 5, '2026-05-03T00:00:00Z',
           'https://x/103', '2026-05-03');
        -- learner huge PR: empty body, 9000 changes (> 1.5*3000=4500),
        -- merged_at empty (NOT merged), 137 files / 27 commits ≈ 5.07
        INSERT INTO pull_requests_current VALUES
          (5, 391, '대기 기능 전체', '', 'open', 'DongKey777', 137, 27,
           9000, 1, '', 'https://x/391', '2026-05-04');
        -- learner clean small PR: real Korean body, modest size, MERGED
        INSERT INTO pull_requests_current VALUES
          (6, 392, '예외 처리 정책 통일',
           '예외 처리 정책을 ControllerAdvice로 통일했습니다. 기존에 흩어져 있던 try-catch를 한곳으로 모았어요. 리뷰 부탁드립니다.',
           'closed', 'DongKey777', 6, 3, 120, 40, '2026-05-05T00:00:00Z',
           'https://x/392', '2026-05-05');
    """)
    conn.commit()
    conn.close()


def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze_repo("nonexistent", LEARNER, tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["prs"] == []
    assert rep["cohort"] == {"avg_additions": 0, "avg_changed_files": 0,
                             "avg_commits": 0, "n": 0}
    assert rep["counts"] == {"prs": 0, "flagged": 0}


def test_cohort_excludes_learner_and_bot(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    # 3 cohort PRs only (learner + bot excluded). Hand-computed averages:
    #   additions (1000+2000+6000)/3 = 3000
    #   changed_files (20+40+90)/3 = 50
    #   commits (10+20+30)/3 = 20
    assert rep["cohort"] == {"avg_additions": 3000, "avg_changed_files": 50,
                             "avg_commits": 20, "n": 3}


def test_learner_huge_pr_flags_and_clean_pr_no_flags(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}

    huge = by_number[391]
    # empty body + 9000 > 1.5*3000=4500 → no_body + large_pr.
    # 137/27 ≈ 5.07 ≤ 8 so cohesion is NOT flagged here.
    assert "no_body" in huge["flags"]
    assert "large_pr" in huge["flags"]
    assert "low_commit_cohesion" not in huge["flags"]

    clean = by_number[392]
    assert clean["flags"] == []


def test_low_commit_cohesion_flag(tmp_path: Path) -> None:
    """A PR whose files_per_commit > 8 carries low_commit_cohesion."""
    _seed_archive(tmp_path, "demo")
    db = tmp_path / "repos" / "demo" / "archive" / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    # 90 files over 2 commits = 45 files/commit, body present + size moderate.
    conn.execute(
        "INSERT INTO pull_requests_current VALUES "
        "(7, 393, '리팩터링 묶음', '여러 파일을 한 번에 정리하다 보니 커밋이 좀 뭉쳐 버렸습니다. 다음에는 커밋을 더 잘게 나눠 보겠습니다. 확인 부탁드립니다.', "
        "'open', 'DongKey777', 90, 2, 200, 100, '', 'https://x/393', '2026-05-06')"
    )
    conn.commit()
    conn.close()
    rep = analyze_repo("demo", LEARNER, tmp_path)
    pr = next(p for p in rep["prs"] if p["number"] == 393)
    assert pr["files_per_commit"] == 45.0
    assert "low_commit_cohesion" in pr["flags"]
    assert "no_body" not in pr["flags"]
    assert "thin_body" not in pr["flags"]


def test_merged_derived_from_merged_at(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    assert by_number[391]["merged"] is False   # merged_at empty
    assert by_number[392]["merged"] is True    # merged_at present


def test_files_per_commit_and_counts(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    by_number = {pr["number"]: pr for pr in rep["prs"]}
    assert by_number[391]["files_per_commit"] == round(137 / 27, 2)  # 5.07
    assert by_number[392]["files_per_commit"] == round(6 / 3, 2)     # 2.0
    assert rep["counts"]["prs"] == 2
    # only the huge PR is flagged → 1
    assert rep["counts"]["flagged"] == 1


def test_save_round_trip_and_key_parity(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze_repo("demo", LEARNER, tmp_path)
    out = save_pr_meta(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "pr_meta.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    documented = ("repo", "status", "learner_login", "cohort", "prs",
                  "counts", "generated_at")
    assert set(data.keys()) == set(documented)
    assert len(documented) == 7
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["learner_login"] == LEARNER
