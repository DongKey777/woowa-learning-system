"""Tests for core.feedback — event → evidence → auto-promote."""
from __future__ import annotations

import json
from pathlib import Path

from core.feedback import (
    ingest_mentor_accept,
    ingest_pr_merge,
    reconcile_mastery_with_history_labels,
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


def test_record_turn_copies_top_level_repo_into_evidence(tmp_path: Path) -> None:
    import sqlite3
    # code_event.py carries repo at the event top level, not in payload.
    record_turn(
        {"event_type": "code_attempt", "repo": "spring-roomescape-member",
         "payload": {"used_concepts": ["spring/bean"]}},
        state_root=tmp_path,
    )
    conn = sqlite3.connect(str(tmp_path / "learner" / "mastery_graph.sqlite"))
    row = conn.execute(
        "SELECT json_extract(payload_json,'$.repo') FROM evidence WHERE concept_id='spring/bean'"
    ).fetchone()
    conn.close()
    assert row[0] == "spring-roomescape-member"  # repo attributable for L use-case


def test_record_turn_does_not_overwrite_payload_repo(tmp_path: Path) -> None:
    import sqlite3
    record_turn(
        {"event_type": "rag_ask", "repo": "outer",
         "payload": {"top_concept_ids": ["spring/bean"], "repo": "inner"}},
        state_root=tmp_path,
    )
    conn = sqlite3.connect(str(tmp_path / "learner" / "mastery_graph.sqlite"))
    row = conn.execute(
        "SELECT json_extract(payload_json,'$.repo') FROM evidence WHERE concept_id='spring/bean'"
    ).fetchone()
    conn.close()
    assert row[0] == "inner"  # payload repo wins; top-level only fills when absent


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


def test_extract_score_honors_self_assess(tmp_path: Path) -> None:
    """W4b: self_assess score scales evidence weight (was hard-coded 1.0)."""
    from core.feedback import _extract_score
    # live emitter stores 0..1 already
    assert _extract_score({"score": 0.3}, "self_assess") == 0.3
    assert _extract_score({"score": 0.9}, "self_assess") == 0.9
    # legacy 10-point self_assessment events in history replay still normalize
    assert _extract_score({"score": 2}, "self_assess") == 0.2
    # no score → calibration-only full weight (1 self_assess never promotes anyway)
    assert _extract_score({}, "self_assess") == 1.0
    # drill_score unchanged; unrelated sources untouched
    assert _extract_score({"score": 0.4}, "drill_score") == 0.4
    assert _extract_score({"score": 0.5}, "mission_use") == 1.0


def test_self_assess_low_score_records_lower_weight(tmp_path: Path) -> None:
    """W4b end-to-end: a low self-assessment stores less evidence weight than a
    high one (before, both stored full weight regardless of the reported score)."""
    import sqlite3

    def weight_for(score: float, cid: str) -> float:
        record_turn(
            {"event_type": "self_assessment",
             "payload": {"concept_id": cid, "score": score}},
            state_root=tmp_path,
        )
        conn = sqlite3.connect(str(tmp_path / "learner" / "mastery_graph.sqlite"))
        w = conn.execute(
            "SELECT weight FROM evidence WHERE concept_id=?", (cid,)
        ).fetchone()[0]
        conn.close()
        return w

    low = weight_for(0.2, "spring/low")
    high = weight_for(1.0, "spring/high")
    assert low < high  # "2/10" must not earn the same mastery credit as "10/10"
    assert low == high * 0.2  # weight scales linearly with the reported score


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


def _relabel_history_mode(state_root: Path, event_id: str, new_mode: str) -> None:
    path = state_root / "learner" / "history.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event_id") == event_id:
            ev["mode"] = new_mode
            ev["mode_source"] = "backfill_reclassified"
        out.append(json.dumps(ev, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_reconcile_drops_reclassified_evidence_preserves_pr_sync(tmp_path: Path) -> None:
    # A genuine learning turn (stays), a turn that will be reclassified to dev
    # (its mission_use must be purged), and a PR-sync-only proficient concept
    # (pr_merge+mentor_accept live only in the store — must NOT be wiped).
    append_history_event(
        {"event_id": "ask-learn", "event_type": "rag_ask", "mode": "learning",
         "ts": 1000.0, "payload": {"prompt": "DI 설명", "top_concept_ids": ["c/keep"]}},
        state_root=tmp_path,
    )
    append_history_event(
        {"event_id": "ask-dev", "event_type": "rag_ask", "mode": "learning",
         "ts": 1001.0, "payload": {"prompt": "코퍼스 확장 측정", "top_concept_ids": ["c/phantom"]}},
        state_root=tmp_path,
    )
    replay_history(state_root=tmp_path)
    ingest_pr_merge(["c/pr"], state_root=tmp_path, ts=1002.0)
    ingest_mentor_accept(["c/pr"], state_root=tmp_path, ts=1003.0)
    assert get("c/phantom", state_root=tmp_path) is not None
    assert get("c/pr", state_root=tmp_path).bloom_level == "proficient"

    # the migration relabels the dev turn AFTER mastery already counted it
    _relabel_history_mode(tmp_path, "ask-dev", "development")
    result = reconcile_mastery_with_history_labels(state_root=tmp_path)

    # phantom concept (only the now-dev event) is dropped
    assert get("c/phantom", state_root=tmp_path) is None
    assert result["dropped_concepts"] == 1
    # genuine learning evidence survives
    assert get("c/keep", state_root=tmp_path) is not None
    # PR-sync evidence preserved — naive unlink+replay would have wiped this
    assert get("c/pr", state_root=tmp_path).bloom_level == "proficient"

    # idempotent: a second pass changes nothing
    again = reconcile_mastery_with_history_labels(state_root=tmp_path)
    assert again["dropped_concepts"] == 0
    assert again["demoted"] == []
    assert get("c/pr", state_root=tmp_path).bloom_level == "proficient"
