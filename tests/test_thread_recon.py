"""Tests for mission.thread_recon — family G (mode thread_recon)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission.thread_recon import analyze_repo, save_thread_recon  # noqa: E402

LEARNER = "DongKey777"


def _seed_archive(state_root: Path, repo: str = "demo") -> None:
    """Minimal prs.sqlite3 with the verified review-comment + PR columns.

    PRs:
      id 375 / #391 — learner's OWN PR (author DongKey777).
      id 400 / #500 — a cohort PR (author crew-a), used to prove its threads
                      are excluded.

    Threads seeded on the learner PR (id 375):
      - A real chain: root (mentor jurlring) + 2 replies (DongKey777, jurlring)
        → reply_count 2, three unique-sorted participants collapse to 2 logins.
      - An isolated root comment with NO reply → must be skipped (not a thread).
      - A root whose body is long (>160 chars) → excerpt truncation.

    On the cohort PR (id 400) a full root+reply chain is seeded; it must NOT
    appear because threads are built only on the learner's own PRs.
    """
    db_dir = state_root / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    db = db_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            html_url TEXT
        );
        INSERT INTO pull_requests_current VALUES
          (375, 391, 'DongKey777', 'https://x/391'),
          (400, 500, 'crew-a',     'https://x/500');

        CREATE TABLE pull_request_review_comments_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER,
            github_review_id INTEGER, github_comment_id INTEGER,
            user_login TEXT, path TEXT, position INTEGER,
            original_position INTEGER, line INTEGER, original_line INTEGER,
            start_line INTEGER, side TEXT, start_side TEXT, commit_id TEXT,
            original_commit_id TEXT, in_reply_to_github_comment_id INTEGER,
            diff_hunk TEXT, body TEXT, created_at TEXT, updated_at TEXT,
            collected_at TEXT
        );
    """)

    def _c(cid, parent, user, body, created, pr=375, path="src/Reservation.java",
           line=10):
        conn.execute(
            "INSERT INTO pull_request_review_comments_current "
            "(id, pull_request_id, github_comment_id, user_login, path, line, "
            " in_reply_to_github_comment_id, body, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, pr, cid, user, path, line, parent, body, created))

    # --- Real chain on the learner PR: root + 2 ordered replies ---
    _c(1000, None, "jurlring",
       "이 메서드는 null을 반환하면 이후 흐름이 어떻게 되나요?",
       "2026-05-29T10:00:00Z")
    _c(1001, 1000, "DongKey777",
       "Optional로 감싸서 호출부에서 처리하도록 바꿨습니다.",
       "2026-05-29T10:05:00Z")
    _c(1002, 1000, "jurlring",
       "좋아요, 그 방향이 더 안전하겠네요.",
       "2026-05-29T10:10:00Z")

    # --- Isolated root with no reply on the learner PR → skipped ---
    _c(2000, None, "jurlring",
       "컨벤션에 맞지 않는 것 같아요.",
       "2026-05-29T11:00:00Z", path="src/Waiting.java", line=42)

    # --- Long-body root + one reply → exercises excerpt truncation ---
    long_body = "대기 순번 계산을 어디까지 DB에 맡길지 고민이 필요합니다. " * 12
    _c(3000, None, "jurlring", long_body, "2026-05-29T12:00:00Z",
       path="src/Queue.java", line=7)
    _c(3001, 3000, "DongKey777", "DB 쪽으로 옮겨 보겠습니다.",
       "2026-05-29T12:05:00Z", path="src/Queue.java", line=7)

    # --- Full chain on a COHORT PR (id 400) → must be excluded ---
    _c(9000, None, "crew-b", "여기 네이밍이 모호합니다.",
       "2026-05-20T09:00:00Z", pr=400, path="src/Other.java", line=3)
    _c(9001, 9000, "crew-a", "수정했습니다.",
       "2026-05-20T09:05:00Z", pr=400, path="src/Other.java", line=3)

    conn.commit()
    conn.close()


def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze_repo("nonexistent", LEARNER, tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["threads"] == []
    assert rep["counts"] == {"prs_with_threads": 0, "threads": 0,
                             "total_messages": 0}


def test_real_reply_chain(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    assert rep["status"] == "ok"
    chain = next(t for t in rep["threads"]
                 if t["path"] == "src/Reservation.java")
    assert chain["pr_number"] == 391
    assert chain["root_author"] == "jurlring"
    assert chain["reply_count"] == 2
    # participants unique + sorted
    assert chain["participants"] == ["DongKey777", "jurlring"]
    # messages ordered by created_at: root, learner reply, mentor reply
    authors = [m["author"] for m in chain["messages"]]
    assert authors == ["jurlring", "DongKey777", "jurlring"]
    created = [m["created_at"] for m in chain["messages"]]
    assert created == sorted(created)
    # is_learner correctness
    assert [m["is_learner"] for m in chain["messages"]] == [False, True, False]
    assert chain["last_at"] == "2026-05-29T10:10:00Z"


def test_isolated_comment_excluded(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    # The lone comment on src/Waiting.java has no reply → not a thread.
    paths = {t["path"] for t in rep["threads"]}
    assert "src/Waiting.java" not in paths
    # every surfaced thread has at least one reply
    assert all(t["reply_count"] >= 1 for t in rep["threads"])


def test_cohort_pr_thread_excluded(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    # Cohort PR #500 has a full chain but is not the learner's PR.
    numbers = {t["pr_number"] for t in rep["threads"]}
    assert 500 not in numbers
    assert numbers == {391}
    # only the two learner threads survive
    assert rep["counts"]["threads"] == 2
    assert rep["counts"]["prs_with_threads"] == 1


def test_excerpt_truncation_at_160(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    long_thread = next(t for t in rep["threads"] if t["path"] == "src/Queue.java")
    # root_excerpt is single-line, <= 160 chars, and ends with the ellipsis.
    assert len(long_thread["root_excerpt"]) <= 161  # 160 + the ellipsis char
    assert long_thread["root_excerpt"].endswith("…")
    assert "\n" not in long_thread["root_excerpt"]


def test_save_round_trip_and_key_parity(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    out = save_thread_recon(rep, tmp_path)
    assert out == tmp_path / "repos" / "demo" / "thread_recon.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    documented = ("repo", "status", "learner_login", "threads", "counts",
                  "generated_at")
    assert set(data.keys()) == set(documented)
    assert len(documented) == 6
    assert data["repo"] == "demo"
    assert data["status"] == "ok"
    assert data["learner_login"] == LEARNER


def test_threads_ordered_most_replies_first(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    rep = analyze_repo("demo", LEARNER, tmp_path)
    counts = [t["reply_count"] for t in rep["threads"]]
    # longest/most-replies first: the 2-reply chain precedes the 1-reply one.
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 2
