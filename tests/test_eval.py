"""Tests for rag.eval — prompt builder, parser, majority, history sampler."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.eval import (
    EvalSample,
    Judgment,
    JudgmentBatch,
    build_judge_prompt,
    load_history_samples,
    majority,
    parse_judgment,
)


def _sample(sid: str = "s1") -> EvalSample:
    return EvalSample(
        sample_id=sid,
        query="DI가 뭐야",
        expected_concept_ids=["spring/di-intro"],
        retrieved_hits=[
            {"concept_id": "spring/bean", "score": 0.91, "title": "Spring Bean"},
            {"concept_id": "spring/di-intro", "score": 0.83, "title": "DI Intro"},
        ],
        answer_text="DI는 의존성 주입입니다.",
    )


def test_build_judge_prompt_includes_query_and_hits() -> None:
    prompt = build_judge_prompt(_sample())
    assert "DI가 뭐야" in prompt
    assert "spring/di-intro" in prompt
    assert "spring/bean" in prompt
    assert "의미적으로 충족" in prompt
    assert "equivalent" in prompt


def test_parse_judgment_extracts_fields() -> None:
    text = "equivalent: true, confidence: 0.92, reason: retrieval covers DI concept directly"
    j = parse_judgment(text, "s1", round_n=1)
    assert j.equivalent is True
    assert j.confidence == pytest.approx(0.92)
    assert "DI concept" in j.reason


def test_parse_judgment_case_insensitive() -> None:
    j = parse_judgment("EQUIVALENT: FALSE, CONFIDENCE: 0.3, REASON: off topic", "s2", 1)
    assert j.equivalent is False
    assert j.confidence == pytest.approx(0.3)


def test_parse_judgment_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        parse_judgment("the answer is good", "s3", 1)


def test_parse_judgment_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="out of"):
        parse_judgment("equivalent: true, confidence: 1.5, reason: hi", "s4", 1)


def test_majority_true_when_two_of_three() -> None:
    judgments = [
        Judgment("s1", 1, True, 0.8, "r1"),
        Judgment("s1", 2, False, 0.6, "r2"),
        Judgment("s1", 3, True, 0.9, "r3"),
    ]
    verdict = majority(judgments)
    assert verdict.equivalent is True
    assert verdict.sample_id == "s1"
    assert verdict.confidence == pytest.approx((0.8 + 0.6 + 0.9) / 3, abs=1e-3)
    assert "2/3" in verdict.reason


def test_majority_false_when_one_of_three() -> None:
    judgments = [
        Judgment("s1", 1, True, 0.7, ""),
        Judgment("s1", 2, False, 0.6, ""),
        Judgment("s1", 3, False, 0.8, ""),
    ]
    assert majority(judgments).equivalent is False


def test_majority_rejects_mixed_sample_ids() -> None:
    with pytest.raises(ValueError, match="different sample_id"):
        majority([Judgment("a", 1, True, 0.5, ""), Judgment("b", 2, True, 0.5, "")])


def test_judgment_batch_roundtrip(tmp_path: Path) -> None:
    batch = JudgmentBatch(
        samples=[_sample()],
        judgments=[Judgment("s1", 1, True, 0.9, "r")],
    )
    p = tmp_path / "batch.json"
    batch.write(p)
    loaded = JudgmentBatch.read(p)
    assert loaded.samples[0].query == "DI가 뭐야"
    assert loaded.judgments[0].equivalent is True


def test_load_history_samples_filters_mode(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    events = [
        {"event_id": "1", "event_type": "rag_ask", "mode": "learning",
         "payload": {"prompt": "Q1", "hits": [{"concept_id": "a/b", "score": 0.5}]}},
        {"event_id": "2", "event_type": "rag_ask", "mode": "development",
         "payload": {"prompt": "Q2"}},
        {"event_id": "3", "event_type": "code_attempt", "mode": "learning",
         "payload": {"prompt": "Q3"}},
        {"event_id": "4", "event_type": "rag_ask", "mode": "learning",
         "payload": {"prompt": "Q4", "hits": []}},
    ]
    history.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    samples = load_history_samples(history, n=10, mode_filter="learning")
    assert len(samples) == 2
    assert {s.query for s in samples} == {"Q1", "Q4"}


def test_load_history_samples_subsamples_deterministically(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    events = [
        {"event_id": f"e{i}", "event_type": "rag_ask", "mode": "learning",
         "payload": {"prompt": f"Q{i}"}}
        for i in range(20)
    ]
    history.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    s1 = load_history_samples(history, n=5, seed=42)
    s2 = load_history_samples(history, n=5, seed=42)
    assert [s.sample_id for s in s1] == [s.sample_id for s in s2]
    assert len(s1) == 5
