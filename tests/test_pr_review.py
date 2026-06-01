"""Tests for mission.pr_review — F2 family A+N (mode pr_review)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.pr_review import analyze, save_pr_review  # noqa: E402

LEARNER = "DongKey777"
MENTOR = "jurlring"


def _seed_archive(state_root: Path, repo: str = "demo") -> None:
    """Minimal prs.sqlite3 with one learner PR + mentor review comments +
    issue comments. Columns match core/pr_retro.py + mission/pr_review.py
    queries. No comment_role column (mirrors the 'waiting' schema)."""
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
        CREATE TABLE pull_request_issue_comments_current (
            id INTEGER PRIMARY KEY,
            pull_request_id INTEGER,
            github_comment_id INTEGER UNIQUE,
            user_login TEXT, body TEXT, created_at TEXT
        );
        INSERT INTO pull_requests_current VALUES
          (1, 391, 'step', '', 'open', 'DongKey777', '2026-05-01', NULL, 3);
        -- 3 mentor root comments, learner replies only to id=1001 → 2 unresolved
        INSERT INTO pull_request_review_comments_current VALUES
          (10, 1, 1001, 'jurlring', 'Reservation.java', 12,
           '빌더를 쓰신 이유가 있나요?', NULL, '2026-05-02');
        INSERT INTO pull_request_review_comments_current VALUES
          (11, 1, 1002, 'DongKey777', 'Reservation.java', 12,
           '수정했어요', 1001, '2026-05-02');
        INSERT INTO pull_request_review_comments_current VALUES
          (20, 1, 2001, 'jurlring', 'Member.java', 30,
           '도메인 테스트가 없는 이유가 있을까요?', NULL, '2026-05-03');
        INSERT INTO pull_request_review_comments_current VALUES
          (21, 1, 2002, 'jurlring', 'Service.java', 50,
           '캡슐화를 해볼까요~?', NULL, '2026-05-03');
    """)
    conn.commit()
    conn.close()


def _issue_comment(state_root: Path, repo: str, github_id: int, pr_id: int,
                   user_login: str, body: str,
                   created_at: str = "2026-05-04") -> None:
    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO pull_request_issue_comments_current "
        "(github_comment_id, pull_request_id, user_login, body, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (github_id, pr_id, user_login, body, created_at),
    )
    conn.commit()
    conn.close()


def _anchors(state_root: Path, anchors: list[dict]) -> None:
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    (d / "review_anchors.json").write_text(
        json.dumps({"anchor_count": len(anchors), "anchors": anchors},
                   ensure_ascii=False),
        encoding="utf-8")


def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze("nonexistent", LEARNER, tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["anchors"] == []
    assert rep["unresolved_threads"] == []
    assert rep["pr_overview"] == []
    assert rep["counts"] == {"anchors": 0, "linked_anchors": 0,
                             "unresolved": 0, "overview": 0}


def test_unresolved_threads_count_and_shape(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    # 3 mentor root comments; learner replied only to id=1001 → 2 unresolved
    assert rep["counts"]["unresolved"] == 2
    assert len(rep["unresolved_threads"]) == 2
    u = rep["unresolved_threads"][0]
    for k in ("pr_number", "path", "line", "mentor_login", "body_excerpt",
              "open_days"):
        assert k in u
    assert all(u["mentor_login"] == MENTOR for u in rep["unresolved_threads"])


def test_anchors_filtered_by_repo(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "repoX")
    _anchors(tmp_path, [
        {"thread_id": "repoX:1:a", "repo": "repoX", "pr_number": 1,
         "path": "A.java", "line": 5, "mentor_login": MENTOR,
         "comment_text": "왜 403이 아니라 404 인가요?",
         "topic_concept_ids": ["security/auth-failure-response-401-403-404"]},
        {"thread_id": "repoX:1:b", "repo": "repoX", "pr_number": 1,
         "path": "B.java", "line": 7, "mentor_login": MENTOR,
         "comment_text": "링크 안 된 코멘트", "topic_concept_ids": []},
        {"thread_id": "repoY:2:c", "repo": "repoY", "pr_number": 2,
         "path": "C.java", "line": 9, "mentor_login": MENTOR,
         "comment_text": "다른 repo", "topic_concept_ids": ["x/y"]},
    ])
    rep = analyze("repoX", LEARNER, tmp_path)
    assert rep["counts"]["anchors"] == 2          # only repoX anchors
    assert rep["counts"]["linked_anchors"] == 1   # only one has concept_ids
    paths = {a["path"] for a in rep["anchors"]}
    assert paths == {"A.java", "B.java"}
    linked = next(a for a in rep["anchors"] if a["concept_ids"])
    assert linked["concept_ids"] == ["security/auth-failure-response-401-403-404"]


def test_pr_overview_includes_mentor_excludes_bot_and_self(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    _issue_comment(tmp_path, "demo", 5001, 1, MENTOR, "전반적으로 좋아요, 머지할게요")
    _issue_comment(tmp_path, "demo", 5002, 1, "coderabbitai[bot]", "bot summary")
    _issue_comment(tmp_path, "demo", 5003, 1, LEARNER, "내 셀프 코멘트")
    rep = analyze("demo", LEARNER, tmp_path)
    logins = {o["user_login"] for o in rep["pr_overview"]}
    assert logins == {MENTOR}                      # bot + self excluded
    assert rep["counts"]["overview"] == 1
    o = rep["pr_overview"][0]
    for k in ("pr_number", "user_login", "body_excerpt", "created_at"):
        assert k in o
    assert o["pr_number"] == 391


def test_pr_overview_degrades_to_empty(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    _issue_comment(tmp_path, "demo", 6001, 1, "coderabbitai[bot]", "bot only")
    _issue_comment(tmp_path, "demo", 6002, 1, LEARNER, "self only")
    rep = analyze("demo", LEARNER, tmp_path)
    assert rep["pr_overview"] == []
    assert rep["counts"]["overview"] == 0


def test_save_pr_review_round_trips(tmp_path: Path) -> None:
    _seed_archive(tmp_path, "demo")
    rep = analyze("demo", LEARNER, tmp_path)
    out = save_pr_review(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "pr_review.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["counts"]["unresolved"] == 2
    for k in ("repo", "learner_login", "status", "generated_at", "anchors",
              "unresolved_threads", "pr_overview", "counts"):
        assert k in data
