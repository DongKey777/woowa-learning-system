"""Unit tests for core.learner_state — Phase T5."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.learner_state import (  # noqa: E402
    _classify_thread, _file_exists_in_head, _git, _git_refs, _working_copy,
    assess_learner_state,
)


def _init_git_repo(tmp_path: Path) -> Path:
    """Init a fresh git repo with 1 commit so refs work."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"],
                   check=True, capture_output=True)
    f = tmp_path / "X.java"
    f.write_text("class X {}", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return tmp_path


def _seed_state(tmp_path: Path, repo: str = "demo") -> Path:
    """Build SQLite archive with 1 open PR + 3 mentor threads (1 has learner reply)."""
    sr = tmp_path / "state"
    db_dir = sr / "repos" / repo / "archive"
    db_dir.mkdir(parents=True)
    db = db_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, title TEXT, state TEXT,
            author_login TEXT, head_ref_name TEXT, base_ref_name TEXT,
            head_sha TEXT, created_at TEXT, merged_at TEXT, closed_at TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
            id INTEGER PRIMARY KEY, pull_request_id INTEGER,
            github_comment_id INTEGER UNIQUE, user_login TEXT, body TEXT,
            path TEXT, line INTEGER,
            in_reply_to_github_comment_id INTEGER, created_at TEXT
        );
        INSERT INTO pull_requests_current VALUES
          (1, 50, 'step1', 'open', 'DongKey777', 'step1', 'main',
           'sha1', '2026-05-20', NULL, NULL);
        -- 3 mentor threads on PR 50
        INSERT INTO pull_request_review_comments_current VALUES
          (10, 1, 1001, 'hyeonic', 'mentor 1: 트랜잭션', 'X.java', 1, NULL, '2026-05-21');
        INSERT INTO pull_request_review_comments_current VALUES
          (11, 1, 1002, 'DongKey777', '수정함', NULL, NULL, 1001, '2026-05-21');
        INSERT INTO pull_request_review_comments_current VALUES
          (20, 1, 2001, 'hyeonic', 'mentor 2: file gone', 'GoneFile.java', 5, NULL, '2026-05-22');
        INSERT INTO pull_request_review_comments_current VALUES
          (30, 1, 3001, 'hyeonic', 'mentor 3: still present', 'X.java', 10, NULL, '2026-05-23');
    """)
    conn.commit()
    conn.close()
    return sr


def test_classify_thread_already_fixed() -> None:
    assert _classify_thread("body", "X.java", 10, has_learner_reply=True,
                              file_present_in_head=True) == "already-fixed"


def test_classify_thread_likely_fixed_no_file() -> None:
    assert _classify_thread("body", "Gone.java", 10, has_learner_reply=False,
                              file_present_in_head=False) == "likely-fixed"


def test_classify_thread_still_applies() -> None:
    assert _classify_thread("body", "X.java", 10, has_learner_reply=False,
                              file_present_in_head=True) == "still-applies"


def test_classify_thread_ambiguous_no_path() -> None:
    assert _classify_thread("body", None, None, has_learner_reply=False,
                              file_present_in_head=False) == "ambiguous"


def test_git_refs_on_real_repo(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    refs = _git_refs(repo)
    assert refs["head_branch"] in ("main", "master")
    assert refs["head_sha"] and len(refs["head_sha"]) == 40


def test_working_copy_clean_after_commit(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    wc = _working_copy(repo)
    assert wc["clean"] is True
    assert wc["dirty_paths"] == []


def test_working_copy_dirty_when_modified(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    (repo / "X.java").write_text("class X { int a; }", encoding="utf-8")
    wc = _working_copy(repo)
    assert wc["clean"] is False
    assert any("X.java" in p for p in wc["dirty_paths"])


def test_file_exists_in_head(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    assert _file_exists_in_head(repo, "X.java") is True
    assert _file_exists_in_head(repo, "Nope.java") is False


def test_assess_learner_state_full(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    mission = _init_git_repo(tmp_path / "mission")
    payload = assess_learner_state(
        repo="demo", mission_path=mission, state_root=sr,
        learner_login="DongKey777",
    )
    assert payload["schema_version"] == "v1"
    assert payload["coverage"] == "full"
    assert payload["working_copy"]["clean"] is True
    assert payload["target_pr"]["number"] == 50
    assert payload["target_pr"]["threads_total"] == 3
    # Verify thread classifications
    classifications = {t["thread_id"]: t["classification"]
                       for t in payload["target_pr"]["threads"]}
    # mentor 1: has learner reply → already-fixed
    assert classifications["1001"] == "already-fixed"
    # mentor 2: file 'GoneFile.java' not in HEAD → likely-fixed
    assert classifications["2001"] == "likely-fixed"
    # mentor 3: X.java present → still-applies
    assert classifications["3001"] == "still-applies"


def test_assess_learner_state_missing_repo_returns_partial(tmp_path: Path) -> None:
    # repo dir exists but archive missing → still works (empty prs)
    sr = tmp_path / "state"
    (sr / "repos" / "noarchive").mkdir(parents=True)
    mission = _init_git_repo(tmp_path / "mission")
    payload = assess_learner_state(repo="noarchive", mission_path=mission,
                                     state_root=sr, learner_login="DongKey777")
    assert payload["prs"] == []
    assert payload["target_pr"] is None
    assert payload["coverage"] == "full"  # quick if no archive
