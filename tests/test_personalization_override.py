"""Phase Y11 PR-E (P1.2) — learner_context personalization override.

compose()의 _user_section에 learner_context를 surface한다:
  - must_skip_explanations_of: mastered + proficient concept 정의 재설명 금지
  - must_include_phrases: AI가 답변에 포함할 phrase
  - must_offer_next_action: 답변 끝에 다음 행동 surface
"""
from __future__ import annotations

from core.coach import compose
from core.router import ARTIFACT_CONCEPT_GRAPH, PERSONA_MENTOR, RouteDecision


def _cs_route():
    return RouteDecision(
        mode="cs_qa", need_rag=True, need_mission_ctx=False, need_anchors=False,
        personas=[PERSONA_MENTOR], budget_tokens=4500,
        lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH],
        reason="test", confidence=0.6,
    )


def test_must_skip_explanations_of_surfaces_in_prompt():
    context = {
        "must_skip_explanations_of": ["spring/di", "spring/ioc"],
    }
    md, _, _, _ = compose(
        _cs_route(), {}, "DI 어떻게",
        learner_id="default", learner_context=context,
    )
    assert "learner_context" in md
    assert "must_skip_explanations_of" in md
    assert "spring/di" in md
    assert "spring/ioc" in md


def test_must_include_phrases_surfaces_in_prompt():
    context = {
        "must_include_phrases": ["3번째 질문이야"],
    }
    md, _, _, _ = compose(
        _cs_route(), {}, "DI 어떻게",
        learner_context=context,
    )
    assert "must_include_phrases" in md
    assert "3번째 질문이야" in md


def test_must_offer_next_action_surfaces_in_prompt():
    context = {
        "must_offer_next_action": {"concept_id": "spring/tx", "question": "..."},
    }
    md, _, _, _ = compose(
        _cs_route(), {}, "DI 어떻게",
        learner_context=context,
    )
    assert "must_offer_next_action" in md


def test_empty_learner_context_omits_section():
    md, _, _, _ = compose(_cs_route(), {}, "DI 어떻게", learner_context={})
    assert "learner_context" not in md
    md2, _, _, _ = compose(_cs_route(), {}, "DI 어떻게", learner_context=None)
    assert "learner_context" not in md2


def test_v3_profile_proficient_field_loaded(tmp_path):
    """LearnerProfile.proficient_concepts 가 v3 schema에서 정상 로드 +
    save round-trip 보존 (P1.2 prereq)."""
    import json
    from core.state import load_profile, save_profile

    learner_dir = tmp_path / "learner"
    learner_dir.mkdir(parents=True)
    (learner_dir / "profile.json").write_text(json.dumps({
        "schema_version": "v3",
        "learner_id": "default",
        "computed_at": 1700000000.0,
        "concepts": {
            "mastered": ["spring/di"],
            "proficient": ["spring/mvc", "db/tx"],
            "uncertain": [],
            "underexplored": [],
        },
        "activity": {"events_total": 10},
    }), encoding="utf-8")

    p = load_profile("default", state_root=tmp_path)
    assert p.proficient_concepts == ["spring/mvc", "db/tx"]
    p.proficient_concepts.append("db/locks")
    save_profile(p, state_root=tmp_path)

    raw = json.loads((learner_dir / "profile.json").read_text())
    assert raw["concepts"]["proficient"] == ["spring/mvc", "db/tx", "db/locks"]
