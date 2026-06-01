"""Tests for mission.reviewer_profile — F3 family C (mode reviewer_profile)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.reviewer_profile import analyze, save_reviewer_profile  # noqa: E402

LEARNER = "DongKey777"
MENTOR = "hyeonic"


def _seed_archive(state_root: Path, repo: str = "demo") -> None:
    """Minimal prs.sqlite3 with a learner PR + a cohort PR, the mentor's root
    review-comments (with reply comments that must be excluded), and review
    rows. Columns mirror the member schema (reviews table uses reviewer_login,
    comments table uses user_login)."""
    db_dir = state_root / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    db = db_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY,
            number INTEGER, title TEXT, body TEXT, state TEXT,
            author_login TEXT, created_at TEXT, merged_at TEXT,
            review_comments_count INTEGER
        );
        CREATE TABLE pull_request_review_comments_current (
            id INTEGER PRIMARY KEY,
            pull_request_id INTEGER,
            github_comment_id INTEGER UNIQUE,
            user_login TEXT, path TEXT, line INTEGER,
            body TEXT, in_reply_to_github_comment_id INTEGER,
            created_at TEXT
        );
        CREATE TABLE pull_request_reviews_current (
            id INTEGER PRIMARY KEY,
            pull_request_id INTEGER,
            github_review_id INTEGER,
            reviewer_login TEXT, state TEXT, body TEXT,
            submitted_at TEXT, reviewer_role TEXT
        );

        -- learner's own PR + a cohort member's PR
        INSERT INTO pull_requests_current VALUES
          (1, 391, '2단계', '', 'open', 'DongKey777', '2026-05-01', NULL, 4);
        INSERT INTO pull_requests_current VALUES
          (2, 412, '2단계', '', 'open', 'someoneelse', '2026-05-01', NULL, 1);

        -- mentor hyeonic: root review-comments on the learner's PR (id=1).
        -- Natural Korean review bodies, each on a real file/line so hotspot_files
        -- and sample_threads populate.
        INSERT INTO pull_request_review_comments_current VALUES
          (10, 1, 1001, 'hyeonic', 'Reservation.java', 12,
           '이 메서드는 책임이 너무 많은 것 같아요. 분리해보면 어떨까요?',
           NULL, '2026-05-02T09:00:00Z');
        INSERT INTO pull_request_review_comments_current VALUES
          (11, 1, 1002, 'hyeonic', 'ReservationTest.java', 30,
           '테스트가 동작은 검증하는데 경계값도 같이 봐주면 좋겠어요',
           NULL, '2026-05-02T09:05:00Z');
        INSERT INTO pull_request_review_comments_current VALUES
          (12, 1, 1003, 'hyeonic', 'Member.java', 8,
           '네이밍이 의도를 잘 못 드러내는 것 같네요',
           NULL, '2026-05-02T09:10:00Z');
        INSERT INTO pull_request_review_comments_current VALUES
          (13, 1, 1004, 'hyeonic', 'Reservation.java', 20,
           '여기 예외 처리는 도메인 안으로 넣는 편이 응집도가 좋아요',
           NULL, '2026-05-03T09:00:00Z');

        -- mentor hyeonic: a root comment on the cohort PR (counts toward
        -- comments_total but NOT comments_on_learner_pr).
        INSERT INTO pull_request_review_comments_current VALUES
          (14, 2, 1005, 'hyeonic', 'Service.java', 40,
           '트랜잭션 경계를 다시 한번 확인해보면 좋겠어요',
           NULL, '2026-05-03T10:00:00Z');

        -- reply comments (in_reply_to NOT NULL) — must be EXCLUDED from counts.
        INSERT INTO pull_request_review_comments_current VALUES
          (20, 1, 2001, 'DongKey777', 'Reservation.java', 12,
           '말씀하신 대로 분리했어요', 1001, '2026-05-02T11:00:00Z');
        INSERT INTO pull_request_review_comments_current VALUES
          (21, 1, 2002, 'hyeonic', 'Reservation.java', 12,
           '좋습니다, 훨씬 읽기 편해졌네요', 1001, '2026-05-02T12:00:00Z');

        -- reviews by hyeonic: one APPROVED, two CHANGES_REQUESTED.
        INSERT INTO pull_request_reviews_current VALUES
          (100, 1, 9001, 'hyeonic', 'CHANGES_REQUESTED',
           '몇 군데 정리하면 좋겠어요', '2026-05-02T09:15:00Z', 'mentor');
        INSERT INTO pull_request_reviews_current VALUES
          (101, 1, 9002, 'hyeonic', 'CHANGES_REQUESTED',
           '하나만 더요', '2026-05-03T09:15:00Z', 'mentor');
        INSERT INTO pull_request_reviews_current VALUES
          (102, 1, 9003, 'hyeonic', 'APPROVED',
           '반영 잘 됐네요, 머지할게요', '2026-05-04T09:15:00Z', 'mentor');
    """)
    conn.commit()
    conn.close()


def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze("nonexistent", reviewer_login=None,
                  learner_login=LEARNER, state_root=tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["recurring_topics"] == []
    assert rep["hotspot_files"] == []
    assert rep["sample_threads"] == []
    assert rep["comments_total"] == 0
    assert rep["counts"] == {"recurring_topics": 0, "hotspot_files": 0,
                             "sample_threads": 0}


def test_auto_resolve_top_mentor(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", reviewer_login=None,
                  learner_login=LEARNER, state_root=tmp_path)
    assert rep["status"] == "ok"
    # top mentor on the learner's OWN PRs is hyeonic (4 root comments on PR id=1)
    assert rep["reviewer_login"] == MENTOR
    assert rep["nickname"] == MENTOR


def test_ok_counts_and_review_states(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", reviewer_login=MENTOR,
                  learner_login=LEARNER, state_root=tmp_path)
    assert rep["status"] == "ok"
    # 5 root comments total (4 on learner PR + 1 on cohort PR); replies excluded
    assert rep["comments_total"] == 5
    assert rep["comments_on_learner_pr"] == 4
    assert rep["review_states"] == {"APPROVED": 1, "CHANGES_REQUESTED": 2,
                                    "COMMENTED": 0}
    assert rep["prs_reviewed"] == 1
    assert rep["first_seen"] is not None
    assert rep["last_seen"] is not None


def test_recurring_topics_excludes_improved_stop(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", reviewer_login=MENTOR,
                  learner_login=LEARNER, state_root=tmp_path)
    assert rep["recurring_topics"]  # non-empty
    topics = {t["topic"] for t in rep["recurring_topics"]}
    # improved STOP tokens must never surface as topics
    for noise in ("같아요", "어떻게", "코드"):
        assert noise not in topics


def test_hotspot_files_and_sample_threads(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", reviewer_login=MENTOR,
                  learner_login=LEARNER, state_root=tmp_path)
    assert rep["hotspot_files"]  # non-empty
    files = {h["file"] for h in rep["hotspot_files"]}
    assert "Reservation.java" in files
    assert rep["sample_threads"]
    assert len(rep["sample_threads"]) <= 3
    for s in rep["sample_threads"]:
        assert set(s.keys()) == {"path", "line", "body_excerpt"}
        assert len(s["body_excerpt"]) <= 150


def test_unknown_reviewer(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", reviewer_login="nobody-here",
                  learner_login=LEARNER, state_root=tmp_path)
    assert rep["status"] == "unknown_reviewer"
    assert rep["reviewer_login"] == "nobody-here"
    assert rep["comments_total"] == 0


def test_save_reviewer_profile_round_trips(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", reviewer_login=MENTOR,
                  learner_login=LEARNER, state_root=tmp_path)
    out = save_reviewer_profile(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "reviewer_profile.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data.keys()) == set(rep.keys())
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["reviewer_login"] == MENTOR
    assert data["comments_total"] == rep["comments_total"]
