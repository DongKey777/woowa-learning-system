"""Tests for core.feedback — event → evidence → auto-promote."""
from __future__ import annotations

import json
from pathlib import Path

from core.feedback import (
    ingest_mentor_accept,
    ingest_pr_merge,
    record_turn,
    replay_history,
)
from core.mastery import get, list_by_level
from core.state import append_history_event


def test_code_attempt_records_mission_use(tmp_path: Path) -> None:
    promoted = record_turn(
        {"event_type": "code_attempt",
         "payload": {"used_concepts": ["spring/bean", "spring/component"]}},
        state_root=tmp_path,
    )
    assert {p[0] for p in promoted} == {"spring/bean", "spring/component"}
    assert all(p[1] == "attempted" for p in promoted)


def test_drill_answer_scales_score_to_weight(tmp_path: Path) -> None:
    record_turn(
        {"event_type": "drill_answer",
         "payload": {"concept_id": "spring/bean", "score": 8}},  # legacy 10-point
        state_root=tmp_path,
    )
    # 1 drill alone never reaches familiar (needs 3 events + 2 sources)
    assert get("spring/bean", state_root=tmp_path).bloom_level == "attempted"


def test_self_assess_calibration_only(tmp_path: Path) -> None:
    """1 self_assess alone never promotes."""
    record_turn(
        {"event_type": "self_assessment", "payload": {"concept_id": "spring/bean"}},
        state_root=tmp_path,
    )
    assert get("spring/bean", state_root=tmp_path).bloom_level == "attempted"


def test_pr_merge_plus_mentor_accept_reaches_proficient(tmp_path: Path) -> None:
    now = 1_000_000.0
    ingest_pr_merge(["spring/bean"], state_root=tmp_path, ts=now - 100)
    ingest_mentor_accept(["spring/bean"], state_root=tmp_path, ts=now - 50)
    assert get("spring/bean", state_root=tmp_path).bloom_level == "proficient"


def test_unknown_event_type_ignored(tmp_path: Path) -> None:
    out = record_turn({"event_type": "test_result", "payload": {"concept_id": "x"}}, state_root=tmp_path)
    assert out == []


def test_citations_extracted_from_rag_ask(tmp_path: Path) -> None:
    """rag_ask with payload.citations gives mission_use evidence."""
    record_turn(
        {"event_type": "rag_ask",
         "payload": {"citations": ["spring/bean", "concept:spring/component"]}},
        state_root=tmp_path,
    )
    # concept: prefix stripped
    assert sorted(list_by_level("attempted", state_root=tmp_path)) == ["spring/bean", "spring/component"]


def test_top_concept_ids_extracted_from_daemon_rag_ask(tmp_path: Path) -> None:
    """daemon rag_ask payload.top_concept_ids feeds Bloom evidence."""
    record_turn(
        {"event_type": "rag_ask",
         "payload": {"top_concept_ids": ["database/lock", "spring/tx"]}},
        state_root=tmp_path,
    )
    assert sorted(list_by_level("attempted", state_root=tmp_path)) == ["database/lock", "spring/tx"]


def test_replay_history_from_jsonl(tmp_path: Path) -> None:
    append_history_event(
        {"event_id": "e1", "event_type": "code_attempt", "mode": "learning",
         "payload": {"used_concepts": ["spring/bean"]}},
        state_root=tmp_path,
    )
    append_history_event(
        {"event_id": "e2", "event_type": "self_assessment", "mode": "learning",
         "payload": {"concept_id": "spring/bean"}},
        state_root=tmp_path,
    )
    append_history_event(
        {"event_id": "e3", "event_type": "drill_answer", "mode": "learning",
         "payload": {"concept_id": "spring/bean", "score": 8}},
        state_root=tmp_path,
    )
    n = replay_history(state_root=tmp_path)
    assert n == 3
    # 3 events × 2 distinct sources (mission_use + self_assess + drill_score = 3 sources)
    assert get("spring/bean", state_root=tmp_path).bloom_level == "familiar"


def test_replay_excludes_development_mode(tmp_path: Path) -> None:
    append_history_event(
        {"event_id": "e1", "event_type": "code_attempt", "mode": "development",
         "payload": {"used_concepts": ["dev/x"]}},
        state_root=tmp_path,
    )
    replay_history(state_root=tmp_path, mode_filter="learning")
    assert get("dev/x", state_root=tmp_path) is None


def test_drill_score_normalized_from_legacy_10_point(tmp_path: Path) -> None:
    """score=8 → weight 0.7 × 0.8 = 0.56 (exactly mastered threshold)."""
    now = 1_000_000.0
    ingest_pr_merge(["spring/bean"], state_root=tmp_path, ts=now - 100)
    ingest_mentor_accept(["spring/bean"], state_root=tmp_path, ts=now - 50)
    record_turn(
        {"event_type": "drill_answer", "ts": now - 10,
         "payload": {"concept_id": "spring/bean", "score": 8}},
        state_root=tmp_path,
    )
    assert get("spring/bean", state_root=tmp_path).bloom_level == "mastered"
