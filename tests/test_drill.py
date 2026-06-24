"""Unit tests for core.drill — F6 completion."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import core.drill as drill_module  # noqa: E402
from core.drill import (  # noqa: E402
    DIM_WEIGHTS, SPACED_BANDS_DAYS,
    DrillOffer, DrillScore,
    build_offer_if_due, score_pending_answer, build_review_offer_if_due,
)


def _seed_state(tmp_path: Path) -> Path:
    (tmp_path / "learner").mkdir(parents=True)
    return tmp_path


def test_build_offer_uses_real_corpus_concept(tmp_path: Path) -> None:
    """spring/bean-di-basics exists in corpus — offer should succeed with
    real expected_queries[0] question + at least 1 expected_term."""
    sr = _seed_state(tmp_path)
    offer = build_offer_if_due(
        state_root=sr,
        uncertain_concept_ids=["spring/bean-di-basics"],
    )
    assert offer is not None
    assert offer.concept_id == "spring/bean-di-basics"
    assert len(offer.question) >= 10
    assert offer.question.endswith(("?", "?"))
    assert len(offer.expected_terms) >= 1
    assert offer.source in ("expected_queries[0]", "learner_query_patterns[0]")
    # Pending file persisted
    pending = sr / "learner" / "drill_pending.json"
    assert pending.exists()


def test_build_offer_uses_schema_object_learner_query_pattern(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_dir = tmp_path / "concepts"
    (corpus_dir / "spring").mkdir(parents=True)
    concept = {
        "id": "spring/pattern-only",
        "title": "Pattern Only",
        "category": "spring",
        "level": "beginner",
        "summary": "Pattern fallback test concept.",
        "body_markdown": "Body content here for testing learner pattern fallback.",
        "expected_queries": [],
        "learner_query_patterns": [
            {
                "pattern": "스키마 객체 learner query pattern도 drill 질문이 돼?",
                "match_count": 1,
                "added_at": "2026-05-27",
            }
        ],
        "metadata": {
            "schema_version": "v2",
            "created_at": "2026-05-27",
            "last_modified": "2026-05-27",
        },
    }
    (corpus_dir / "spring" / "pattern-only.json").write_text(
        json.dumps(concept, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(drill_module, "CORPUS_DIR", corpus_dir)

    offer = build_offer_if_due(
        state_root=_seed_state(tmp_path),
        uncertain_concept_ids=["spring/pattern-only"],
    )

    assert offer is not None
    assert offer.question == "스키마 객체 learner query pattern도 drill 질문이 돼?"
    assert offer.source == "learner_query_patterns[0]"


def test_build_offer_scans_past_short_expected_query_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EQ[0] is a short '<term>이/가 뭐야?' (the exact-shortcut surface, <10 chars)
    but a later EQ is substantive — build_offer must scan past [0] rather than
    drop the concept. Guards the latent drill-break class (e.g. aop-basics,
    lock-basics) where a short EQ[0] + empty learner_query_patterns yielded None."""
    corpus_dir = tmp_path / "concepts"
    (corpus_dir / "spring").mkdir(parents=True)
    concept = {
        "id": "spring/short-eq-zero",
        "title": "Short EQ Zero",
        "category": "spring",
        "level": "beginner",
        "summary": "Concept whose expected_queries[0] is too short to drill on.",
        "body_markdown": "Body content for scan-past-short-eq test.",
        "expected_queries": ["AOP가 뭐야?", "관점 지향 프로그래밍이 왜 필요해?"],
        "learner_query_patterns": [],
        "metadata": {
            "schema_version": "v2",
            "created_at": "2026-06-25",
            "last_modified": "2026-06-25",
        },
    }
    (corpus_dir / "spring" / "short-eq-zero.json").write_text(
        json.dumps(concept, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(drill_module, "CORPUS_DIR", corpus_dir)

    offer = build_offer_if_due(
        state_root=_seed_state(tmp_path),
        uncertain_concept_ids=["spring/short-eq-zero"],
    )
    assert offer is not None
    assert offer.question == "관점 지향 프로그래밍이 왜 필요해?"
    assert offer.source == "expected_queries[1]"


def test_build_offer_returns_none_when_pending_exists(tmp_path: Path) -> None:
    """Single open offer at a time — second call returns None."""
    sr = _seed_state(tmp_path)
    first = build_offer_if_due(state_root=sr,
                                uncertain_concept_ids=["spring/bean-di-basics"])
    assert first is not None
    second = build_offer_if_due(state_root=sr,
                                 uncertain_concept_ids=["spring/ioc-di-basics"])
    assert second is None


def test_build_offer_returns_none_for_invalid_concept(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    offer = build_offer_if_due(state_root=sr,
                                uncertain_concept_ids=["nonsense/no-such-concept"])
    assert offer is None


def test_build_offer_empty_candidates_returns_none(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    assert build_offer_if_due(state_root=sr, uncertain_concept_ids=[]) is None


def test_score_strong_answer_promotes_mastered(tmp_path: Path) -> None:
    """Long detailed answer with all expected_terms + code block + structure
    keywords → score >= 0.80 (mastered band)."""
    sr = _seed_state(tmp_path)
    offer = build_offer_if_due(state_root=sr,
                                uncertain_concept_ids=["spring/bean-di-basics"])
    assert offer is not None

    # Construct answer that hits every dim
    answer = (
        "Spring Bean이란 Spring IoC 컨테이너가 관리하는 객체이고, "
        "의존성 주입(DI)을 통해 생명주기를 제어해. "
        "왜 필요하냐면 객체 간 결합도를 낮춰서 테스트와 교체를 쉽게 만들기 때문이야. "
        "언제 쓰냐면 Service/Repository/Controller 같은 협력 객체가 필요할 때야. "
        "어떻게 쓰냐면 @Component, @Service, @Configuration 같은 어노테이션으로 등록하면 돼. "
        "예제: ```java\n@Service\npublic class FooService {}\n``` "
        + " ".join(offer.expected_terms)  # ensure 100% term match
    )
    score = score_pending_answer(answer, state_root=sr)
    assert score is not None
    assert score.total >= 0.80, f"expected ≥0.80, got {score.total}"
    assert score.level == "mastered"
    # Pending cleared
    assert not (sr / "learner" / "drill_pending.json").exists()


def test_score_weak_answer_grades_weak(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    build_offer_if_due(state_root=sr,
                       uncertain_concept_ids=["spring/bean-di-basics"])
    weak = "몰라"  # 2-char stub
    score = score_pending_answer(weak, state_root=sr)
    assert score is not None
    assert score.total < 0.45
    assert score.level == "weak"
    assert "키워드" in score.improvement_notes or "짧" in score.improvement_notes


def test_score_appends_drill_answer_event(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    offer = build_offer_if_due(state_root=sr,
                                uncertain_concept_ids=["spring/bean-di-basics"])
    answer = "Bean은 컨테이너가 관리하는 객체 " + " ".join(offer.expected_terms)
    score = score_pending_answer(answer, state_root=sr)
    assert score is not None
    hist = sr / "learner" / "history.jsonl"
    assert hist.exists()
    lines = [json.loads(L) for L in hist.read_text().splitlines() if L.strip()]
    assert len(lines) == 1
    assert lines[0]["event_type"] == "drill_answer"
    assert lines[0]["payload"]["concept_ids"] == ["spring/bean-di-basics"]
    assert 0 <= lines[0]["payload"]["score"] <= 1


def test_score_schedules_next_review(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    build_offer_if_due(state_root=sr,
                       uncertain_concept_ids=["spring/bean-di-basics"])
    score_pending_answer("간단한 답변", state_root=sr)
    due = json.loads((sr / "learner" / "drill_due.json").read_text())
    assert len(due) == 1
    assert due[0]["concept_id"] == "spring/bean-di-basics"
    assert "next_due_ts" in due[0]


def test_score_returns_none_when_no_pending(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    assert score_pending_answer("어떤 답변", state_root=sr) is None


def test_dim_weights_sum_to_one() -> None:
    assert abs(sum(DIM_WEIGHTS.values()) - 1.0) < 1e-6


def test_spaced_repetition_review_due(tmp_path: Path) -> None:
    """due_list with past timestamp → build_review_offer_if_due fires."""
    sr = _seed_state(tmp_path)
    # Synthetic due item in the past
    (sr / "learner" / "drill_due.json").write_text(json.dumps([
        {"concept_id": "spring/bean-di-basics", "next_due_ts": 0.0,
         "level": "weak", "last_score": 0.2}
    ]), encoding="utf-8")
    offer = build_review_offer_if_due(state_root=sr)
    assert offer is not None
    assert offer.concept_id == "spring/bean-di-basics"


def test_spaced_repetition_future_due_skipped(tmp_path: Path) -> None:
    sr = _seed_state(tmp_path)
    (sr / "learner" / "drill_due.json").write_text(json.dumps([
        {"concept_id": "spring/bean-di-basics",
         "next_due_ts": 9_999_999_999.0,  # year 2286
         "level": "good", "last_score": 0.7}
    ]), encoding="utf-8")
    assert build_review_offer_if_due(state_root=sr) is None


def test_proactive_offer_marker_round_trip(tmp_path: Path) -> None:
    # W8: frequency-cap marker (drill_offer_meta.json) read/write.
    from core.drill import read_last_proactive_offer_at, write_last_proactive_offer_at
    assert read_last_proactive_offer_at(tmp_path) is None
    write_last_proactive_offer_at(1234.5, tmp_path)
    assert read_last_proactive_offer_at(tmp_path) == 1234.5


def test_score_upserts_due_entry_not_appends(tmp_path: Path) -> None:
    # A prior review left a past-due entry for the concept. Answering must REPLACE
    # it with the new band, not append a 2nd row — append-only grew the list every
    # cycle and the stale past-due row kept getting re-offered (spaced-repetition
    # band delay ignored).
    sr = _seed_state(tmp_path)
    (sr / "learner" / "drill_due.json").write_text(json.dumps([
        {"concept_id": "spring/bean-di-basics", "next_due_ts": 0.0,
         "level": "weak", "last_score": 0.2}
    ]), encoding="utf-8")
    build_offer_if_due(state_root=sr, uncertain_concept_ids=["spring/bean-di-basics"])
    score_pending_answer("간단한 답변", state_root=sr, now=1_000_000.0)
    due = json.loads((sr / "learner" / "drill_due.json").read_text())
    assert len([d for d in due if d["concept_id"] == "spring/bean-di-basics"]) == 1
    assert due[0]["next_due_ts"] > 1_000_000.0  # rescheduled to the future
    # no longer past-due → review offer won't re-fire on the stale entry
    assert build_review_offer_if_due(state_root=sr, now=1_000_000.0) is None
