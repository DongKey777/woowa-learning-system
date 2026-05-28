"""Tests for core.state + core.archive — pure file I/O + SQLite."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from core.archive import (
    get_diff_files,
    get_pr,
    get_review_threads,
    list_prs,
)
from core.state import (
    LearnerProfile,
    RepoState,
    append_history_event,
    load_profile,
    load_repo_state,
    read_history,
    save_profile,
    save_repo_state,
)


# ── state.py ────────────────────────────────────────────────────────────────


def test_load_profile_returns_empty_for_missing(tmp_path: Path) -> None:
    profile = load_profile("donkey", state_root=tmp_path)
    assert profile.learner_id == "donkey"
    assert profile.mastered_concepts == []
    assert profile.total_events == 0


def test_learner_profile_rejects_non_canonical_ids() -> None:
    for learner_id in ("default", "", "higgs95@naver.com"):
        with pytest.raises(ValueError):
            LearnerProfile(learner_id=learner_id)


def test_save_then_load_profile_roundtrip(tmp_path: Path) -> None:
    p = LearnerProfile(
        learner_id="donkey",
        mastered_concepts=["spring/bean"],
        uncertain_concepts=["database/index"],
        drill_due=[{"concept_id": "spring/bean", "due_at": "2026-06-01"}],
        pending_triggers={"self_assessment": {"trigger_session_id": "s1"}},
        total_events=42,
    )
    save_profile(p, state_root=tmp_path)
    loaded = load_profile("donkey", state_root=tmp_path)
    assert loaded.mastered_concepts == ["spring/bean"]
    assert loaded.uncertain_concepts == ["database/index"]
    assert loaded.drill_due == [{"concept_id": "spring/bean", "due_at": "2026-06-01"}]
    assert loaded.pending_triggers == {"self_assessment": {"trigger_session_id": "s1"}}
    assert loaded.total_events == 42
    assert loaded.last_updated > 0


def test_save_profile_creates_directories(tmp_path: Path) -> None:
    p = LearnerProfile(learner_id="x")
    save_profile(p, state_root=tmp_path)
    assert (tmp_path / "learner" / "profile.json").exists()


def test_repo_state_roundtrip(tmp_path: Path) -> None:
    s = RepoState(
        repo_name="roomescape",
        working_copy={"branch": "step1", "dirty": False},
        target_pr_number=37,
        threads=[{"id": "t1", "resolved": False}],
        active_session_id="sess-7",
    )
    save_repo_state(s, state_root=tmp_path)
    loaded = load_repo_state("roomescape", state_root=tmp_path)
    assert loaded.repo_name == "roomescape"
    assert loaded.target_pr_number == 37
    assert loaded.threads == [{"id": "t1", "resolved": False}]
    assert loaded.active_session_id == "sess-7"
    assert loaded.last_updated > 0


def test_history_append_and_read(tmp_path: Path) -> None:
    append_history_event({"event_id": "e1", "type": "rag_ask"}, state_root=tmp_path)
    time.sleep(0.001)
    append_history_event({"event_id": "e2", "type": "code_attempt"}, state_root=tmp_path)
    events = read_history(state_root=tmp_path)
    assert len(events) == 2
    assert events[0]["event_id"] == "e1"
    assert events[1]["event_id"] == "e2"
    assert all("ts" in e for e in events)


def test_history_caller_supplied_ts_preserved(tmp_path: Path) -> None:
    append_history_event({"event_id": "e1", "ts": 1.5}, state_root=tmp_path)
    events = read_history(state_root=tmp_path)
    assert events[0]["ts"] == 1.5


def test_read_history_returns_empty_when_missing(tmp_path: Path) -> None:
    assert read_history(state_root=tmp_path) == []


# ── archive.py ──────────────────────────────────────────────────────────────


def _seed_archive(tmp_path: Path, repo: str = "roomescape") -> Path:
    arch_dir = tmp_path / "repos" / repo / "archive"
    arch_dir.mkdir(parents=True)
    db_path = arch_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE pull_requests (
            number INTEGER PRIMARY KEY,
            title TEXT, author_login TEXT,
            additions INTEGER, deletions INTEGER
        );
        CREATE TABLE pull_request_files_current (
            pr_number INTEGER, path TEXT,
            additions INTEGER, deletions INTEGER, patch_text TEXT
        );
        CREATE TABLE review_comments (
            id INTEGER PRIMARY KEY,
            pr_number INTEGER, body TEXT, path TEXT, line INTEGER,
            in_reply_to_id INTEGER, author_login TEXT
        );
        INSERT INTO pull_requests VALUES (37, 'Step1 DB', 'donkey', 120, 30);
        INSERT INTO pull_requests VALUES (38, 'Step2 layers', 'donkey', 80, 10);
        INSERT INTO pull_requests VALUES (39, 'Peer PR', 'cubinkim', 200, 50);
        INSERT INTO pull_request_files_current VALUES
            (37, 'src/main/java/X.java', 80, 10, 'diff text X'),
            (37, 'src/main/java/Y.java', 40, 20, 'diff text Y');
        INSERT INTO review_comments VALUES
            (1, 37, 'consider X', 'X.java', 10, NULL, 'mentor'),
            (2, 37, 'agreed', 'X.java', 10, 1, 'donkey'),
            (3, 37, 'standalone', 'Y.java', 5, NULL, 'mentor');
        """
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_list_prs_recent_descending(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    prs = list_prs("roomescape", limit=5, state_root=root)
    assert [p["number"] for p in prs] == [39, 38, 37]


def test_list_prs_filter_by_author(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    prs = list_prs("roomescape", author_login="donkey", state_root=root)
    assert [p["number"] for p in prs] == [38, 37]
    assert all(p["author_login"] == "donkey" for p in prs)


def test_get_pr_returns_dict_or_none(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    assert get_pr("roomescape", 37, state_root=root)["title"] == "Step1 DB"
    assert get_pr("roomescape", 999, state_root=root) is None


def test_get_diff_files(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    files = get_diff_files("roomescape", 37, state_root=root)
    assert len(files) == 2
    assert files[0]["path"] == "src/main/java/X.java"
    assert files[0]["patch_text"] == "diff text X"


def test_get_review_threads_groups_replies(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    threads = get_review_threads("roomescape", 37, state_root=root)
    assert len(threads) == 2
    threaded = [t for t in threads if t["replies"]]
    assert len(threaded) == 1
    assert threaded[0]["root"]["id"] == 1
    assert threaded[0]["replies"][0]["body"] == "agreed"


def test_archive_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_prs("ghost", state_root=tmp_path)
