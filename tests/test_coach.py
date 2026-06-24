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


def test_render_peer_pr_renders_comparison() -> None:
    from core.router import RouteDecision

    r = RouteDecision(
        mode="peer_compare", need_rag=False, need_mission_ctx=True,
        need_anchors=False, personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
        budget_tokens=7000, lazy_artifacts=["peer_pr"], reason="test",
    )
    arts = {
        "peer_pr": {
            "ready": True, "status": "ok", "repo": "roomescape",
            "learner_pr": {"number": 489, "nickname": "동키",
                           "additions": 634, "deletions": 22},
            "learner_files": ["src/main/A.java", "src/main/Z.java"],
            "peer_prs": [{"number": 430, "nickname": "티온",
                          "author_login": "bee9827",
                          "additions": 100, "deletions": 5}],
            "peer_files": {"430": ["src/main/A.java", "src/main/B.java"]},
            "peer_only_files": {"430": ["src/main/B.java"]},
            "common_paths": ["src/main/A.java"],
            "learner_only_paths": ["src/main/Z.java"],
            "peer_only_paths": ["src/main/B.java"],
            "representative_threads": {"430": [{"root": {
                "author_login": "jamie9504", "path": "src/main/B.java",
                "line": 12, "body": "디미터의 법칙을 알아보세요."}, "reply_count": 1}]},
            "selection": [{"number": 430, "nickname": "티온",
                           "author_login": "bee9827", "jaccard": 0.273,
                           "common_files": 12}],
            "counts": {"peers": 1},
        }
    }
    prompt, _, _, _ = compose(r, arts, "동기들은 이 미션 어떻게 짰어?", repo="roomescape")
    assert "### peer_pr" in prompt
    assert "#489" in prompt and "동키" in prompt
    assert "티온" in prompt and "bee9827" in prompt
    assert "Jaccard 0.273" in prompt
    # learner-only vs peer-only file partition surfaces
    assert "나만 건드린 파일" in prompt
    assert "이 동기만 건드린 파일" in prompt and "B.java" in prompt
    # the real mentor thread on the peer's PR is the learning gold
    assert "디미터의 법칙" in prompt and "jamie9504" in prompt


def test_render_peer_pr_graceful_when_unbuilt() -> None:
    from core.router import RouteDecision

    r = RouteDecision(
        mode="peer_compare", need_rag=False, need_mission_ctx=True,
        need_anchors=False, personas=[PERSONA_REVIEWER], budget_tokens=7000,
        lazy_artifacts=["peer_pr"], reason="test",
    )
    prompt, _, _, _ = compose(
        r, {"peer_pr": {"repo": "r", "ready": False, "missing": True}},
        "동기 비교", repo="r")
    assert "peer_pr (NOT YET BUILT" in prompt


def test_render_peer_pr_no_peer_status() -> None:
    from core.router import RouteDecision

    r = RouteDecision(
        mode="peer_compare", need_rag=False, need_mission_ctx=True,
        need_anchors=False, personas=[PERSONA_REVIEWER], budget_tokens=7000,
        lazy_artifacts=["peer_pr"], reason="test",
    )
    prompt, _, _, _ = compose(
        r, {"peer_pr": {"ready": True, "status": "no_peer", "repo": "r"}},
        "동기 비교", repo="r")
    assert "비교할 동기 PR이 아직 없어" in prompt


def test_render_peer_pr_status_branches() -> None:
    from core.router import RouteDecision

    r = RouteDecision(
        mode="peer_compare", need_rag=False, need_mission_ctx=True,
        need_anchors=False, personas=[PERSONA_REVIEWER], budget_tokens=7000,
        lazy_artifacts=["peer_pr"], reason="test",
    )
    for status, expected in [
        ("missing_archive", "동기 비교용 archive가 아직 없어"),
        ("no_learner_pr", "비교할 내 PR이 아직 없어"),
    ]:
        prompt, _, _, _ = compose(
            r, {"peer_pr": {"ready": True, "status": status, "repo": "r"}},
            "동기 비교", repo="r")
        assert expected in prompt, f"status={status} missing its render text"
