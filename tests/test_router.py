"""Tests for core.router — Self-Routing decision layer."""
from __future__ import annotations

from core.router import (
    ARTIFACT_ANCHORS,
    ARTIFACT_CONCEPT_GRAPH,
    ARTIFACT_CROSS_CREW,
    ARTIFACT_MASTERY,
    ARTIFACT_MISSION_PATTERNS,
    PERSONA_MENTOR,
    PERSONA_REVIEWER,
    PERSONA_SOCRATIC,
    RouteDecision,
    route,
    strip_override_tokens,
)


def test_f11_explicit_trigger_overrides_other_modes() -> None:
    d = route("PR 37 정밀 비교 해줘", repo="roomescape-admin")
    assert d.mode == "f11_anchor"
    assert d.need_anchors is True
    assert ARTIFACT_CROSS_CREW in d.lazy_artifacts
    assert ARTIFACT_ANCHORS in d.lazy_artifacts
    assert d.personas == [PERSONA_REVIEWER, PERSONA_MENTOR]
    assert d.budget_tokens == 12000


def test_f11_keyword_anchor() -> None:
    d = route("이 anchor 다른 크루는?")
    assert d.mode == "f11_anchor"


def test_f11_pr_line_pattern() -> None:
    d = route("PR 42 line 38 thread 뭐가 다른지")
    assert d.mode == "f11_anchor"


def test_tool_only_routing() -> None:
    d = route("gradle 빌드 어떻게")
    assert d.mode == "tool_only"
    assert d.need_rag is False
    assert d.lazy_artifacts == []
    assert d.personas == []
    assert d.budget_tokens == 1500


def test_cs_qa_routing() -> None:
    d = route("DI가 뭐야")
    assert d.mode == "cs_qa"
    assert d.need_rag is True
    assert d.need_mission_ctx is False
    assert ARTIFACT_CONCEPT_GRAPH in d.lazy_artifacts
    assert ARTIFACT_MASTERY in d.lazy_artifacts
    assert d.personas == [PERSONA_MENTOR, PERSONA_SOCRATIC]
    assert d.budget_tokens == 4500


def test_transactional_location_question_stays_cs_qa() -> None:
    d = route("@Transactional 어노테이션은 어디에 붙이는 게 좋아?")
    assert d.mode == "cs_qa"
    assert d.need_rag is True


def test_java_short_concept_queries_stay_cs_qa() -> None:
    for prompt in ("JdbcTemplate 사용", "Optional 사용", "Stream API", "Lambda"):
        d = route(prompt)
        assert d.mode == "cs_qa", prompt
        assert d.need_rag is True


def test_learning_practice_request_without_pending_stays_cs_qa() -> None:
    d = route("확인 질문 하나 줘")
    assert d.mode == "cs_qa"
    assert d.need_rag is True


def test_coaching_routing_loads_mission_ctx() -> None:
    d = route("내 코드 어떻게 리팩토링", repo="roomescape-admin")
    assert d.mode == "coaching"
    assert d.need_rag is True
    assert d.need_mission_ctx is True
    assert ARTIFACT_MISSION_PATTERNS in d.lazy_artifacts
    assert d.personas == [PERSONA_MENTOR, PERSONA_REVIEWER, PERSONA_SOCRATIC]
    assert d.budget_tokens == 5500


def test_drill_routing_with_pending_drill() -> None:
    d = route("DI는 의존성 주입을 위한 패턴이다.", pending_drill={"id": "d1"})
    assert d.mode == "drill"
    assert d.need_rag is False
    assert d.personas == [PERSONA_SOCRATIC]
    assert d.budget_tokens == 3000
    assert d.lazy_artifacts == [ARTIFACT_MASTERY]


def test_self_assess_routing_with_pending_trigger() -> None:
    d = route("8점", pending_self_assessment={"trigger_session_id": "s1"})
    assert d.mode == "self_assess"
    assert d.need_rag is False
    assert d.personas == []
    assert d.budget_tokens == 2000


def test_retro_routing() -> None:
    d = route("내 PR 흐름 보여줘", repo="roomescape-admin")
    assert d.mode == "retro"
    assert d.need_mission_ctx is True
    assert d.personas == [PERSONA_MENTOR, PERSONA_REVIEWER]
    assert d.budget_tokens == 5000


def test_route_decision_returns_dataclass() -> None:
    d = route("DI가 뭐야")
    assert isinstance(d, RouteDecision)
    assert d.reason and d.confidence > 0


def test_budget_within_phase_table() -> None:
    """Token budget per mode matches plan §D-H table."""
    cases = {
        "gradle 빌드": 1500,           # tool_only
        "DI가 뭐야": 4500,             # cs_qa
        "8점": 2000,                   # self_assess (needs pending)
    }
    for prompt, expected in cases.items():
        pending = {"trigger_session_id": "s"} if prompt == "8점" else None
        d = route(prompt, pending_self_assessment=pending)
        assert d.budget_tokens == expected, f"budget mismatch for {prompt!r}: {d.budget_tokens}"


def test_f11_priority_over_retro() -> None:
    """F11 explicit trigger beats retro keyword."""
    d = route("내 PR 흐름 정밀 비교", repo="x")
    assert d.mode == "f11_anchor"


def test_override_skip_rag_wins_over_cs_signal() -> None:
    d = route("그냥 답해줘. DI가 뭐야")
    assert d.mode == "tier_0_fallback"
    assert d.need_rag is False
    assert "skip RAG" in d.reason


def test_override_full_rag_forces_cs_qa() -> None:
    d = route("RAG로 깊게 DI vs Service Locator 알려줘")
    assert d.mode == "cs_qa"
    assert d.need_rag is True
    assert d.budget_tokens == 5500
    assert strip_override_tokens("RAG로 깊게 DI 설명") == "DI 설명"


def test_override_min_rag_rescues_weak_prompt() -> None:
    d = route("근거 보여줘. 이거 왜?")
    assert d.mode == "cs_qa"
    assert d.need_rag is True


def test_override_coach_mode_without_keyword_needs_mission_context() -> None:
    d = route("코치 모드로 봐줘", repo="roomescape")
    assert d.mode == "coaching"
    assert d.need_mission_ctx is True
    assert ARTIFACT_MISSION_PATTERNS in d.lazy_artifacts
