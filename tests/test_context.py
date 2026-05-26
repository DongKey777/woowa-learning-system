"""Tests for core.context — per-mode collectors with mocked retrieval."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from core.context import (
    collect_coaching_context,
    collect_cs_qa_context,
    collect_drill_context,
    collect_retro_context,
    collect_self_assess_context,
)
from core.state import (
    LearnerProfile,
    RepoState,
    append_history_event,
    save_profile,
    save_repo_state,
)
from rag.search import SearchHit


def _seed_archive(tmp_path: Path, repo: str = "roomescape") -> None:
    arch = tmp_path / "repos" / repo / "archive"
    arch.mkdir(parents=True)
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    conn.executescript(
        """
        CREATE TABLE pull_requests (number INTEGER PRIMARY KEY, title TEXT,
            author_login TEXT, additions INTEGER, deletions INTEGER);
        CREATE TABLE pull_request_files_current (pr_number INTEGER, path TEXT,
            additions INTEGER, deletions INTEGER, patch_text TEXT);
        CREATE TABLE review_comments (id INTEGER PRIMARY KEY, pr_number INTEGER,
            body TEXT, path TEXT, line INTEGER, in_reply_to_id INTEGER, author_login TEXT);
        INSERT INTO pull_requests VALUES (37, '[1단계] 동키', 'donkey', 80, 10);
        INSERT INTO pull_requests VALUES (40, '[1단계] 큐빈', 'cubin', 90, 5);
        INSERT INTO pull_request_files_current VALUES
            (37, 'X.java', 80, 10, 'X'),
            (40, 'X.java', 90, 5, 'cubin X');
        """
    )
    conn.commit()
    conn.close()


def _mock_hits():
    return [
        SearchHit("spring/bean", 0.9, "spring", "Bean", "dense"),
        SearchHit("spring/component", 0.8, "spring", "Component", "dense"),
    ]


def test_cs_qa_returns_personalized_hits(tmp_path: Path) -> None:
    save_profile(LearnerProfile(learner_id="dk", mastered_concepts=["spring/bean"]), state_root=tmp_path)
    with patch("core.context.search", return_value=_mock_hits()):
        ctx = collect_cs_qa_context("DI", learner_id="dk", state_root=tmp_path)
    assert ctx["mode"] == "cs_qa"
    assert ctx["prompt"] == "DI"
    # spring/bean got -0.15 demotion → spring/component ranks first
    assert ctx["hits"][0]["concept_id"] == "spring/component"
    assert "spring/bean" in ctx["personalization"]["mastered_applied"]


def test_cs_qa_treats_proficient_as_mastered(tmp_path: Path) -> None:
    save_profile(LearnerProfile(learner_id="dk", proficient_concepts=["spring/bean"]), state_root=tmp_path)
    with patch("core.context.search", return_value=_mock_hits()):
        ctx = collect_cs_qa_context("DI", learner_id="dk", state_root=tmp_path)
    assert ctx["hits"][0]["concept_id"] == "spring/component"
    assert ctx["personalization"]["mastered_applied"] == ["spring/bean"]


def test_coaching_assembles_state_peer_rag(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    save_profile(LearnerProfile(learner_id="donkey"), state_root=tmp_path)
    save_repo_state(
        RepoState(repo_name="roomescape", target_pr_number=37,
                  working_copy={"branch": "step1"}),
        state_root=tmp_path,
    )
    with patch("core.context.search", return_value=_mock_hits()):
        ctx = collect_coaching_context(
            "어떻게 리팩토링?", repo="roomescape", learner_id="donkey",
            peer_pr_numbers=[40], state_root=tmp_path,
        )
    assert ctx["mode"] == "coaching"
    assert ctx["learner_state"]["target_pr_number"] == 37
    assert ctx["peer_pr_comparison"]["common_paths"] == ["X.java"]
    assert ctx["peer_pr_comparison"]["learner_pr"]["nickname"] == "동키"
    assert len(ctx["rag_augment"]) == 2


def test_coaching_without_target_pr_skips_peer(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    save_profile(LearnerProfile(learner_id="dk"), state_root=tmp_path)
    save_repo_state(RepoState(repo_name="roomescape", target_pr_number=None), state_root=tmp_path)
    with patch("core.context.search", return_value=[]):
        ctx = collect_coaching_context(
            "q", repo="roomescape", learner_id="dk",
            peer_pr_numbers=[40], state_root=tmp_path,
        )
    assert ctx["peer_pr_comparison"] is None


def test_drill_context_loads_pending(tmp_path: Path) -> None:
    save_profile(
        LearnerProfile(
            learner_id="dk",
            drill_due=[{"id": "d1", "due_at": "2026-06-01"}],
            pending_triggers={"review_drill": {"id": "d2"}},
            mastered_concepts=[f"spring/c{i}" for i in range(15)],
        ),
        state_root=tmp_path,
    )
    ctx = collect_drill_context(learner_id="dk", state_root=tmp_path)
    assert ctx["mode"] == "drill"
    assert ctx["drill_due"][0]["id"] == "d1"
    assert ctx["pending_triggers"] == {"review_drill": {"id": "d2"}}
    assert len(ctx["recent_mastered"]) == 10
    assert ctx["recent_mastered"][-1] == "spring/c14"


def test_retro_context_returns_learner_prs(tmp_path: Path) -> None:
    _seed_archive(tmp_path)
    ctx = collect_retro_context("roomescape", learner_login="donkey", state_root=tmp_path)
    assert ctx["mode"] == "retro"
    assert ctx["pr_count"] == 1
    assert ctx["pr_timeline"][0]["number"] == 37


def test_self_assess_context_returns_pending_trigger(tmp_path: Path) -> None:
    save_profile(
        LearnerProfile(
            learner_id="dk",
            pending_triggers={"self_assessment": {"trigger_session_id": "s1", "payload": {"concept_ids": ["spring/bean"]}}},
        ),
        state_root=tmp_path,
    )
    append_history_event({"event_id": "h1", "type": "code_attempt"}, state_root=tmp_path)
    ctx = collect_self_assess_context(learner_id="dk", state_root=tmp_path)
    assert ctx["mode"] == "self_assess"
    assert ctx["pending_self_assessment"]["trigger_session_id"] == "s1"
    assert len(ctx["recent_history"]) == 1


def test_cs_qa_handles_no_profile(tmp_path: Path) -> None:
    with patch("core.context.search", return_value=_mock_hits()):
        ctx = collect_cs_qa_context("q", learner_id="new", state_root=tmp_path)
    assert ctx["personalization"]["mastered_applied"] == []
    assert ctx["personalization"]["uncertain_applied"] == []
