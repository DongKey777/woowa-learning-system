"""Unit tests for core.pr_retro — Phase T1."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.pr_retro import (  # noqa: E402
    SIGNAL_MIN_FREQ, _topics_from_body, _open_days,
    build_retrospective, save_retrospective,
)


def _seed_archive(tmp_path: Path, repo: str = "demo") -> Path:
    """Build a minimal prs.sqlite3 with 3 PRs by DongKey777 + mentor comments."""
    db_dir = tmp_path / "repos" / repo / "archive"
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
        INSERT INTO pull_requests_current VALUES
          (1, 100, 'step1', '', 'merged', 'DongKey777', '2026-05-01', '2026-05-03', 5),
          (2, 200, 'step2', '', 'merged', 'DongKey777', '2026-05-10', '2026-05-12', 6),
          (3, 300, 'step3', '', 'open',   'DongKey777', '2026-05-20', NULL, 4);
        -- PR1: mentor talks about 트랜잭션 + JdbcTemplate
        INSERT INTO pull_request_review_comments_current VALUES
          (10, 1, 1001, 'hyeonic', 'Reservation.java', 12,
           '트랜잭션 경계가 JdbcTemplate 호출 밖에 있어요', NULL, '2026-05-02');
        -- PR1: learner reply
        INSERT INTO pull_request_review_comments_current VALUES
          (11, 1, 1002, 'DongKey777', 'Reservation.java', 12,
           '수정했어요', 1001, '2026-05-02');
        -- PR2: same 트랜잭션 topic again (recurring)
        INSERT INTO pull_request_review_comments_current VALUES
          (20, 2, 2001, 'hyeonic', 'Service.java', 30,
           '트랜잭션을 Service에 두면 좋겠어요', NULL, '2026-05-11');
        -- PR2: unresolved mentor root
        INSERT INTO pull_request_review_comments_current VALUES
          (21, 2, 2002, 'hyeonic', 'Service.java', 50,
           'JdbcTemplate 직접 호출 줄여보세요', NULL, '2026-05-11');
        -- PR3: still open, 1 unresolved + 트랜잭션 third hit
        INSERT INTO pull_request_review_comments_current VALUES
          (30, 3, 3001, 'hyeonic', 'Controller.java', 5,
           '트랜잭션 어노테이션 빠진 거 같아요', NULL, '2026-05-21');
    """)
    conn.commit()
    conn.close()
    return tmp_path


def test_topics_from_body_filters_stopwords() -> None:
    body = "트랜잭션 경계가 JdbcTemplate 호출 밖에 있어요. 코드 리뷰 부분"
    topics = _topics_from_body(body)
    assert "트랜잭션" in topics
    assert "JdbcTemplate" in topics
    assert "코드" not in topics  # stopword
    assert "리뷰" not in topics


def test_topics_from_body_empty() -> None:
    assert _topics_from_body("") == []
    assert _topics_from_body(None) == []  # type: ignore


def test_open_days_basic() -> None:
    # 2026-05-01 → now 2026-05-25 = 24 days
    now = time.mktime((2026, 5, 25, 0, 0, 0, 0, 0, 0))
    days = _open_days("2026-05-01T00:00:00Z", now)
    assert days is not None
    assert 23 <= days <= 25


def test_build_retrospective_recurring_signal_detected(tmp_path: Path) -> None:
    state_root = _seed_archive(tmp_path)
    retro = build_retrospective("demo", "DongKey777", state_root=state_root)

    assert retro.source == "sqlite"
    assert retro.prs_total == 3
    assert retro.prs_merged == 2
    # 트랜잭션 appears in PR 100, 200, 300 → recurring (≥2)
    topics = {s.topic for s in retro.recurring_mentor_signals}
    assert "트랜잭션" in topics
    # JdbcTemplate appears in PR 100, 200 → recurring
    assert "JdbcTemplate" in topics


def test_build_retrospective_unresolved_threads_heuristic(tmp_path: Path) -> None:
    state_root = _seed_archive(tmp_path)
    retro = build_retrospective("demo", "DongKey777", state_root=state_root)
    # 4 mentor root comments; learner replied only to id=1001 → 3 unresolved
    pr_numbers = sorted([u.pr_number for u in retro.unresolved_threads])
    assert pr_numbers == [200, 200, 300]


def test_build_retrospective_timeline_per_pr(tmp_path: Path) -> None:
    state_root = _seed_archive(tmp_path)
    retro = build_retrospective("demo", "DongKey777", state_root=state_root)
    assert len(retro.timeline) == 3
    # newest first (number desc)
    assert [t.pr_number for t in retro.timeline] == [300, 200, 100]
    # review_rounds = unique days of mentor comments per PR
    by_pr = {t.pr_number: t.review_rounds for t in retro.timeline}
    assert by_pr[100] == 1  # 1 mentor day
    assert by_pr[200] == 1  # 1 mentor day (both same day)
    assert by_pr[300] == 1


def test_build_retrospective_missing_archive() -> None:
    retro = build_retrospective("nonexistent", "DongKey777",
                                 state_root=Path("/tmp/nonexistent-root-xyz"))
    assert retro.source == "missing_archive"
    assert retro.prs_total == 0
    assert retro.recurring_mentor_signals == []


def test_build_retrospective_other_learner_returns_empty(tmp_path: Path) -> None:
    state_root = _seed_archive(tmp_path)
    retro = build_retrospective("demo", "SomeoneElse", state_root=state_root)
    assert retro.prs_total == 0
    assert retro.recurring_mentor_signals == []


def test_save_retrospective_writes_complete_json(tmp_path: Path) -> None:
    state_root = _seed_archive(tmp_path)
    retro = build_retrospective("demo", "DongKey777", state_root=state_root)
    out = save_retrospective(retro, state_root=state_root)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    # Top-level keys present + types correct
    for k in ("repo", "learner_login", "computed_at", "prs_total", "prs_merged",
              "avg_review_rounds", "timeline", "recurring_mentor_signals",
              "unresolved_threads", "source"):
        assert k in data, f"missing key: {k}"
    assert isinstance(data["timeline"], list)
    assert isinstance(data["recurring_mentor_signals"], list)


def test_pr_limit_caps_results(tmp_path: Path) -> None:
    state_root = _seed_archive(tmp_path)
    retro = build_retrospective("demo", "DongKey777", state_root=state_root, pr_limit=2)
    assert retro.prs_total == 2
    assert len(retro.timeline) == 2
    # newest 2 → 300, 200
    assert [t.pr_number for t in retro.timeline] == [300, 200]
