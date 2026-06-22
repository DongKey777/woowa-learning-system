"""Tests for core.coach — multi-agent prompt composition."""
from __future__ import annotations

from pathlib import Path

from core.coach import _reorder_must_skip_by_relevance, compose
from core.lazy_loader import load
from core.router import (
    PERSONA_MENTOR,
    PERSONA_REVIEWER,
    PERSONA_SOCRATIC,
    route,
)


def test_compose_cs_qa_has_mentor_and_socratic(tmp_path: Path) -> None:
    r = route("DI가 뭐야")
    arts = load(r, state_root=tmp_path, concept_graph_path=tmp_path / "missing.json")
    prompt, _, _, _ = compose(r, arts, "DI가 뭐야", learner_id="donkey")
    assert "[MENTOR]" in prompt
    assert "[SOCRATIC]" in prompt
    assert "[REVIEWER]" not in prompt  # cs_qa has no reviewer persona
    assert "DI가 뭐야" in prompt
    assert "donkey" in prompt
    assert "[Mode: cs_qa]" in prompt
    assert "참고:" in prompt  # citation rule


def test_compose_coaching_has_all_three_personas(tmp_path: Path) -> None:
    r = route("내 코드 어떻게 리팩토링", repo="myrepo")
    arts = load(r, repo="myrepo", state_root=tmp_path)
    prompt, _, _, _ = compose(r, arts, "내 코드 어떻게 리팩토링", repo="myrepo", learner_id="donkey")
    for label in ("[MENTOR]", "[REVIEWER]", "[SOCRATIC]"):
        assert label in prompt
    assert "mission_patterns" in prompt
    assert "자동 코드 수정 ❌" in prompt


def test_compose_tool_only_skips_personas(tmp_path: Path) -> None:
    r = route("gradle 빌드")
    arts = load(r, state_root=tmp_path)
    prompt, _, _, _ = compose(r, arts, "gradle 빌드")
    assert "[MENTOR]" not in prompt
    assert "[REVIEWER]" not in prompt
    assert "[SOCRATIC]" not in prompt
    assert "tool_only" in prompt
    assert "Loaded artifacts" not in prompt  # no artifacts loaded


def test_compose_f11_anchor_emphasizes_review_diff(tmp_path: Path) -> None:
    r = route("PR 37 정밀 비교")
    arts = load(r, repo="myrepo", state_root=tmp_path)
    prompt, _, _, _ = compose(r, arts, "PR 37 정밀 비교", repo="myrepo")
    assert "[REVIEWER]" in prompt
    assert "[MENTOR]" in prompt
    assert "cross_crew" in prompt or "review_anchors" in prompt
    assert "Phase C pending" in prompt  # artifacts not yet built


def test_compose_retro_reviewer_does_not_cite_unloaded_review_data(tmp_path: Path) -> None:
    # retro carries REVIEWER persona but loads neither review_anchors nor
    # cross_crew_review_graph → it must NOT instruct citing those (hallucination guard).
    r = route("내 PR 회고", repo="myrepo")
    arts = load(r, repo="myrepo", state_root=tmp_path)
    prompt, _, _, _ = compose(r, arts, "내 PR 회고", repo="myrepo")
    assert "[REVIEWER]" in prompt
    assert "cross_crew_review_graph + review_anchors" not in prompt
    assert "없는 리뷰를 지어내" in prompt  # NO_XCREW variant active


def test_compose_drill_persona_only_socratic(tmp_path: Path) -> None:
    r = route("DI는 의존성 주입.", pending_drill={"id": "d1"})
    arts = load(r, state_root=tmp_path)
    prompt, _, _, _ = compose(r, arts, "DI는 의존성 주입.")
    assert "[SOCRATIC]" in prompt
    assert "[MENTOR]" not in prompt


def test_compose_includes_token_budget_in_header(tmp_path: Path) -> None:
    r = route("DI가 뭐야")
    prompt, _, _, _ = compose(r, {}, "DI가 뭐야")
    assert "4500" in prompt  # cs_qa budget per plan §D-H


def test_compose_recent_history_truncated_to_20(tmp_path: Path) -> None:
    r = route("DI가 뭐야")
    history = [{"event_type": "rag_ask", "event_id": f"e{i}"} for i in range(30)]
    prompt, _, _, _ = compose(r, {}, "DI가 뭐야", recent_history=history)
    assert "e29" in prompt  # most recent included
    assert "e9" not in prompt  # truncated (30 - 20 = 10, so e10+ kept; e9 dropped)
    assert "e10" in prompt


def test_compose_includes_mastery_summary_when_loaded(tmp_path: Path) -> None:
    from core.mastery import record_evidence

    record_evidence("spring/bean", "mission_use", state_root=tmp_path)
    r = route("DI가 뭐야")
    arts = load(r, state_root=tmp_path, concept_graph_path=tmp_path / "missing.json")
    prompt, _, _, _ = compose(r, arts, "DI가 뭐야")
    assert "mastery_graph" in prompt
    assert "spring/bean" in prompt


def test_compose_omits_artifact_section_when_empty() -> None:
    from core.router import RouteDecision

    r = RouteDecision(
        mode="tool_only", need_rag=False, need_mission_ctx=False, need_anchors=False,
        personas=[], budget_tokens=1500, lazy_artifacts=[], reason="test",
    )
    prompt, _, _, _ = compose(r, {}, "git status")
    assert "Loaded artifacts" not in prompt


def test_compose_surfaces_cognitive_trigger() -> None:
    r = route("DI가 뭐야")
    prompt, _, _, _ = compose(
        r,
        {
            "cognitive_trigger": {
                "trigger_type": "self_assessment",
                "trigger_session_id": "s1",
                "reason": "recent_code_attempt_without_self_assessment",
                "markdown": "## 자기 점검\n- 1-10점으로 적어줘.",
            }
        },
        "DI가 뭐야",
    )

    assert "### cognitive_trigger" in prompt
    assert "self_assessment" in prompt
    assert "trigger_session_id: s1" in prompt


def test_reorder_must_skip_noop_when_ten_or_fewer() -> None:
    # ≤10 concepts: the prompt cap is inactive, so order must stay identical
    # (guarantees byte-identical prompt for the current learner with 8).
    ms = [f"c/concept-{i}" for i in range(8)]
    hits = [{"concept_id": "c/concept-7"}]  # a relevant hit
    assert _reorder_must_skip_by_relevance(ms, hits) == ms


def test_reorder_must_skip_relevant_survives_cap_when_over_ten() -> None:
    # A mastered concept relevant to THIS query but alphabetically last would be
    # dropped by the [:10] cap; relevance-reorder must move it into the top 10.
    ms = [f"a/concept-{i:02d}" for i in range(12)] + ["zzz/jpa-persistence"]
    assert "zzz/jpa-persistence" not in ms[:10]  # alphabetical slice drops it
    hits = [{"concept_id": "zzz/jpa-persistence"}]
    reordered = _reorder_must_skip_by_relevance(ms, hits)
    assert reordered[0] == "zzz/jpa-persistence"
    assert "zzz/jpa-persistence" in reordered[:10]
    # Stable: the non-relevant tail keeps its original order.
    assert reordered[1:] == ms[:12]


def test_reorder_must_skip_noop_without_rag_concept_ids() -> None:
    # No relevance signal (cs_qa with no retrieval) → preserve current behavior.
    ms = [f"a/concept-{i:02d}" for i in range(13)]
    assert _reorder_must_skip_by_relevance(ms, []) == ms
    assert _reorder_must_skip_by_relevance(ms, [{"no_cid": 1}]) == ms
