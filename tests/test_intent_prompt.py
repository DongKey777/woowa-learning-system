"""Tests for core.intent + core.prompt — dispatch accuracy + prompt assembly."""
from __future__ import annotations

import pytest

from core.intent import IntentDecision, detect_mode
from core.prompt import build


# ── intent.detect_mode ──────────────────────────────────────────────────────


def test_detect_self_assess_when_pending_and_score() -> None:
    d = detect_mode("8점", pending_self_assessment={"trigger_session_id": "s1"})
    assert d.mode == "self_assess"
    assert d.confidence > 0.9


def test_detect_drill_when_pending_and_answer_shape() -> None:
    d = detect_mode("DI는 의존성 주입을 위한 것이다.", pending_drill={"id": "d1"})
    assert d.mode == "drill"


def test_detect_retro_keyword() -> None:
    for q in ("내 PR 흐름 보여줘", "반복 멘토 지적", "PR 37 정밀"):
        assert detect_mode(q).mode == "retro", f"failed: {q}"


def test_detect_coaching_when_repo_and_keyword() -> None:
    d = detect_mode("내 코드 어떻게 리팩토링?", repo="roomescape-admin")
    assert d.mode == "coaching"


def test_detect_coaching_requires_repo() -> None:
    """Same keywords without repo → not coaching mode."""
    d = detect_mode("어떻게 짜야 할까")
    assert d.mode != "coaching"


def test_detect_tool_only_fast_path() -> None:
    for q in ("gradle build 어떻게 해", "git rebase 충돌 났어", "intellij에서 단축키"):
        assert detect_mode(q).mode == "tool_only", f"failed: {q}"


def test_detect_default_cs_qa() -> None:
    for q in ("DI가 뭐야", "Bean lifecycle 설명해", "transaction propagation"):
        assert detect_mode(q).mode == "cs_qa", f"failed: {q}"


def test_detect_retro_beats_coaching_when_both_match() -> None:
    """retro keyword wins even with repo specified."""
    d = detect_mode("내 PR 흐름 보여줘 리뷰", repo="roomescape-admin")
    assert d.mode == "retro"


def test_detect_returns_intent_decision_with_reason() -> None:
    d = detect_mode("DI가 뭐야")
    assert isinstance(d, IntentDecision)
    assert d.reason and 0 <= d.confidence <= 1


# ── prompt.build ────────────────────────────────────────────────────────────


def test_build_cs_qa_includes_question_and_hits() -> None:
    ctx = {
        "mode": "cs_qa", "prompt": "DI가 뭐야",
        "hits": [
            {"concept_id": "spring/di-intro", "score": 0.92, "category": "spring",
             "title": "DI Intro", "source": "dense"},
        ],
        "personalization": {"mastered_applied": ["spring/bean"], "uncertain_applied": []},
    }
    md = build("cs_qa", ctx)
    assert "DI가 뭐야" in md
    assert "spring/di-intro" in md
    assert "이미 학습됨" in md
    assert "RAG: tier" in md


def test_build_coaching_includes_state_and_peer() -> None:
    ctx = {
        "mode": "coaching", "repo": "roomescape", "prompt": "리팩토링 어떻게?",
        "learner_state": {
            "working_copy": {"branch": "step1", "dirty": False},
            "target_pr_number": 37,
            "threads": [{"id": "t1", "resolved": False}],
        },
        "peer_pr_comparison": {
            "learner_pr": {"number": 37, "title": "Step1", "additions": 80, "deletions": 10},
            "learner_files": ["X.java"],
            "peer_prs": [{"number": 40, "title": "Step1 Cubin", "nickname": "큐빈", "additions": 90, "deletions": 5}],
            "peer_files": {40: ["X.java", "Z.java"]},
            "common_paths": ["X.java"],
            "learner_only_paths": [],
            "peer_only_paths": ["Z.java"],
            "representative_threads": {"40": [{"root": {"author_login": "mentor", "path": "Z.java", "line": 5, "body": "split"}}]},
        },
        "rag_augment": [],
    }
    md = build("coaching", ctx)
    assert "Step1" in md
    assert "PR #37" in md or "#37" in md
    assert "큐빈" in md
    assert "X.java" in md
    assert "feedback_no_auto_code_edit" in md


def test_build_drill_includes_pending() -> None:
    ctx = {
        "mode": "drill",
        "drill_due": [{"id": "d1", "due_at": "2026-06-01"}],
        "pending_triggers": {"review_drill": {"id": "d2", "question": "What is DI"}},
        "recent_mastered": ["spring/bean"],
    }
    md = build("drill", ctx)
    assert "spring/bean" in md
    assert "d2" in md
    assert "accuracy" in md


def test_build_retro_lists_prs() -> None:
    ctx = {
        "mode": "retro", "repo": "x", "learner_login": "donkey", "pr_count": 2,
        "pr_timeline": [
            {"number": 37, "title": "Step1", "additions": 80, "deletions": 10},
            {"number": 38, "title": "Step2", "additions": 50, "deletions": 5},
        ],
    }
    md = build("retro", ctx)
    assert "donkey" in md
    assert "#37" in md and "#38" in md
    assert "Step1" in md


def test_build_self_assess_includes_concept_ids() -> None:
    ctx = {
        "mode": "self_assess",
        "pending_self_assessment": {"trigger_session_id": "s1", "payload": {"concept_ids": ["spring/bean"]}},
        "recent_history": [{"type": "code_attempt", "event_id": "e1"}],
    }
    md = build("self_assess", ctx)
    assert "spring/bean" in md
    assert "s1" in md
    assert "calibration only" in md


def test_build_tool_only_skips_rag_block() -> None:
    md = build("tool_only", {"prompt": "gradle build"})
    assert "gradle" in md
    assert "RAG: tier-0" in md
    assert "참고:" in md  # contract: forbid 참고 출력


def test_build_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        build("xxx", {})


def test_build_cs_qa_handles_empty_hits() -> None:
    md = build("cs_qa", {"prompt": "q", "hits": [], "personalization": {}})
    assert "retrieval 결과 없음" in md
