"""Tests for core.evidence_sync — production pr_merge/mentor_accept emission (F0-e)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from anchors.extract import ReviewAnchor, save_anchors
from core.evidence_sync import sync_repo
from core.mastery import get

LEARNER = "donkey"
REPO = "spring-roomescape-member"


def _seed(tmp_path: Path, *, merged=True, approved=True, reviews_schema="new") -> str:
    arch = tmp_path / "repos" / REPO / "archive"
    arch.mkdir(parents=True)
    (tmp_path / "repos" / REPO / "mission_patterns.json").write_text(json.dumps({
        "repo": REPO, "patterns": [
            {"matched_concept_id": "spring/bean-di-basics"},
            {"matched_concept_id": "spring/bean-di-basics"},  # dup → 1 distinct
            {"matched_concept_id": "spring/mvc-controller-basics"},
        ],
    }), encoding="utf-8")
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    merged_at = "'2026-05-11T00:00:00Z'" if merged else "NULL"
    conn.executescript(f"""
      CREATE TABLE pull_requests_current (
        id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
        merged_at TEXT, state TEXT);
      INSERT INTO pull_requests_current VALUES (1, 390, '{LEARNER}', {merged_at}, 'closed');
      CREATE TABLE pull_request_review_comments_current (
        id INTEGER PRIMARY KEY, github_comment_id INTEGER, pull_request_id INTEGER,
        path TEXT, line INTEGER, diff_hunk TEXT, user_login TEXT, body TEXT,
        in_reply_to_github_comment_id INTEGER, created_at TEXT);
    """)
    if reviews_schema == "new":  # reviewer_login + reviewer_role
        conn.execute("CREATE TABLE pull_request_reviews_current (id INTEGER PRIMARY KEY, "
                     "pull_request_id INTEGER, reviewer_login TEXT, state TEXT, "
                     "submitted_at TEXT, reviewer_role TEXT)")
        if approved:
            conn.execute("INSERT INTO pull_request_reviews_current VALUES "
                         "(1,1,'hyeonic','APPROVED','2026-05-11T13:51:47Z','mentor')")
    else:  # legacy schema: user_login, no reviewer_role
        conn.execute("CREATE TABLE pull_request_reviews_current (id INTEGER PRIMARY KEY, "
                     "pull_request_id INTEGER, user_login TEXT, state TEXT, submitted_at TEXT)")
        if approved:
            conn.execute("INSERT INTO pull_request_reviews_current VALUES "
                         "(1,1,'hyeonic','APPROVED','2026-05-11T13:51:47Z')")
    conn.commit()
    conn.close()
    return REPO


def test_merged_and_approved_reaches_proficient(tmp_path: Path) -> None:
    sync_repo(_seed(tmp_path), learner_login=LEARNER, state_root=tmp_path)
    assert get("spring/bean-di-basics", state_root=tmp_path).bloom_level == "proficient"
    assert get("spring/mvc-controller-basics", state_root=tmp_path).bloom_level == "proficient"


def test_report_counts(tmp_path: Path) -> None:
    report = sync_repo(_seed(tmp_path), learner_login=LEARNER, state_root=tmp_path)
    assert report["status"] == "ok"
    assert report["pr_merge_emitted"] == 2       # 2 distinct concepts
    assert report["mentor_accept_emitted"] == 2
    assert report["after"]["proficient"] == 2
    assert report["promotions"] == 2             # both crossed into proficient


def test_idempotent_rerun(tmp_path: Path) -> None:
    repo = _seed(tmp_path)
    sync_repo(repo, learner_login=LEARNER, state_root=tmp_path)
    e1 = get("spring/bean-di-basics", state_root=tmp_path).evidence_count
    rerun = sync_repo(repo, learner_login=LEARNER, state_root=tmp_path)
    e2 = get("spring/bean-di-basics", state_root=tmp_path).evidence_count
    assert e1 == e2  # dedup_key prevents double-counting on re-sync
    assert rerun["promotions"] == 0  # nothing rose on the second pass


def test_merged_without_approval_not_proficient(tmp_path: Path) -> None:
    sync_repo(_seed(tmp_path, approved=False), learner_login=LEARNER, state_root=tmp_path)
    # pr_merge alone (no mentor_accept) must not reach proficient
    assert get("spring/bean-di-basics", state_root=tmp_path).bloom_level != "proficient"


def test_legacy_reviews_schema_user_login(tmp_path: Path) -> None:
    report = sync_repo(_seed(tmp_path, reviews_schema="old"),
                       learner_login=LEARNER, state_root=tmp_path)
    assert report["mentor_accept_emitted"] == 2  # user_login column resolved
    assert get("spring/bean-di-basics", state_root=tmp_path).bloom_level == "proficient"


def test_missing_archive_graceful(tmp_path: Path) -> None:
    report = sync_repo("nonexistent", learner_login=LEARNER, state_root=tmp_path)
    assert report["status"] == "missing_archive"
    assert report["promotions"] == 0


def test_resolved_thread_emits_mentor_accept(tmp_path: Path) -> None:
    repo = _seed(tmp_path, merged=False, approved=False)
    save_anchors([ReviewAnchor(
        thread_id=f"{repo}:390:1001", repo=repo, pr_number=390, path="X.java", line=1,
        code_hunk="c", mentor_login="hyeonic", comment_text="DI?",
        topic_concept_ids=["spring/ioc-di-basics"],
    )], state_root=tmp_path)
    conn = sqlite3.connect(str(tmp_path / "repos" / repo / "archive" / "prs.sqlite3"))
    conn.execute("INSERT INTO pull_request_review_comments_current "
                 "(github_comment_id, pull_request_id, user_login, body, in_reply_to_github_comment_id, created_at) "
                 "VALUES (1001,1,'hyeonic','DI?',NULL,'2026-05-01')")
    conn.execute("INSERT INTO pull_request_review_comments_current "
                 "(github_comment_id, pull_request_id, user_login, body, in_reply_to_github_comment_id, created_at) "
                 f"VALUES (2002,1,'{LEARNER}','네 고쳤어요',1001,'2026-05-02')")
    conn.commit()
    conn.close()
    report = sync_repo(repo, learner_login=LEARNER, state_root=tmp_path)
    assert report["mentor_accept_emitted"] == 1  # resolved-thread anchor link
    assert get("spring/ioc-di-basics", state_root=tmp_path) is not None
    # W6: thread_resolved ts is the learner reply's created_at (deterministic, NOT
    # wall-clock) — works even for this UNMERGED PR (merged_at NULL).
    from core.evidence_sync import _parse_iso
    mg = sqlite3.connect(str(tmp_path / "learner" / "mastery_graph.sqlite"))
    ts_rows = mg.execute(
        "SELECT ts FROM evidence WHERE concept_id=?", ("spring/ioc-di-basics",)
    ).fetchall()
    mg.close()
    assert ts_rows and ts_rows[0][0] == _parse_iso("2026-05-02")  # reply created_at


def test_unresolved_thread_no_mentor_accept(tmp_path: Path) -> None:
    repo = _seed(tmp_path, merged=False, approved=False)
    save_anchors([ReviewAnchor(
        thread_id=f"{repo}:390:1001", repo=repo, pr_number=390, path="X.java", line=1,
        code_hunk="c", mentor_login="hyeonic", comment_text="DI?",
        topic_concept_ids=["spring/ioc-di-basics"],
    )], state_root=tmp_path)
    conn = sqlite3.connect(str(tmp_path / "repos" / repo / "archive" / "prs.sqlite3"))
    # mentor root comment with NO learner reply → unresolved
    conn.execute("INSERT INTO pull_request_review_comments_current "
                 "(github_comment_id, pull_request_id, user_login, body, in_reply_to_github_comment_id, created_at) "
                 "VALUES (1001,1,'hyeonic','DI?',NULL,'2026-05-01')")
    conn.commit()
    conn.close()
    report = sync_repo(repo, learner_login=LEARNER, state_root=tmp_path)
    assert report["mentor_accept_emitted"] == 0  # not replied → not accepted
