"""Unit tests for core.profile — Phase T6."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.profile import (  # noqa: E402
    SCHEMA_VERSION, _classify_experience, _is_learning_event,
    UNIFIED_SCHEMA_VERSION, build_unified_projection, compute_cs_view,
    rebuild_unified_projection, recompute_profile,
)


def _seed(tmp_path: Path, events: list[dict],
          mastery: dict[str, str] | None = None) -> Path:
    """events: list of {event_type, mode, ts, payload}."""
    sr = tmp_path
    (sr / "learner").mkdir(parents=True)
    log = sr / "learner" / "history.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    if mastery:
        db = sr / "learner" / "mastery_graph.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE mastery (concept_id TEXT, bloom_level TEXT, evidence_count INTEGER DEFAULT 0)")
        for cid, level in mastery.items():
            conn.execute("INSERT INTO mastery (concept_id, bloom_level) VALUES (?, ?)",
                         (cid, level))
        conn.commit()
        conn.close()
    return sr


def test_classify_experience_junior() -> None:
    assert _classify_experience(0, 0) == "junior"
    assert _classify_experience(99, 0) == "junior"


def test_classify_experience_intermediate_by_events() -> None:
    assert _classify_experience(100, 0) == "intermediate"


def test_classify_experience_intermediate_by_mastered() -> None:
    assert _classify_experience(10, 1) == "intermediate"


def test_classify_experience_advanced_by_events() -> None:
    assert _classify_experience(1000, 0) == "advanced"


def test_classify_experience_advanced_by_mastered() -> None:
    assert _classify_experience(10, 5) == "advanced"


def test_is_learning_event_filters_dev_mode() -> None:
    assert _is_learning_event({"mode": "learning"}) is True
    assert _is_learning_event({"mode": None}) is True
    assert _is_learning_event({"mode": "development"}) is False
    assert _is_learning_event({"mode": "test"}) is False


def test_recompute_profile_basic_shape(tmp_path: Path) -> None:
    events = [
        {"event_type": "rag_ask", "mode": "learning", "ts": time.time(),
         "payload": {"top_concept_ids": ["spring/bean"]}},
        {"event_type": "code_attempt", "mode": "learning", "ts": time.time(),
         "payload": {"concept_ids": ["spring/bean"]}},
    ]
    sr = _seed(tmp_path, events)
    p = recompute_profile(state_root=sr)
    assert p["schema_version"] == SCHEMA_VERSION
    assert p["experience_level"] == "junior"
    assert p["activity"]["events_total"] == 2
    assert "spring/bean" in p["concepts"]["underexplored"] + p["concepts"]["uncertain"]


def test_recompute_profile_advanced_with_5_mastered(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [], mastery={
        f"spring/{i}": "mastered" for i in range(5)
    })
    p = recompute_profile(state_root=sr)
    assert p["experience_level"] == "advanced"
    assert len(p["concepts"]["mastered"]) == 5


def test_recompute_profile_filters_dev_mode_events(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [
        {"event_type": "rag_ask", "mode": "development", "ts": time.time(),
         "payload": {"top_concept_ids": ["dev/concept"]}},
        {"event_type": "rag_ask", "mode": "learning", "ts": time.time(),
         "payload": {"top_concept_ids": ["learning/concept"]}},
    ])
    p = recompute_profile(state_root=sr)
    assert p["activity"]["events_total"] == 1
    # dev concept should NOT appear
    all_concepts = set(p["concepts"]["uncertain"] + p["concepts"]["underexplored"])
    assert "dev/concept" not in all_concepts


def test_recompute_profile_recent_code_24h(tmp_path: Path) -> None:
    now = time.time()
    sr = _seed(tmp_path, [
        {"event_type": "code_attempt", "mode": "learning", "ts": now - 100,
         "payload": {"concept_ids": ["spring/x"]}},
        {"event_type": "code_attempt", "mode": "learning", "ts": now - 200_000,  # >24h
         "payload": {"concept_ids": ["spring/y"]}},
    ])
    p = recompute_profile(state_root=sr, now=now)
    assert p["recent_code_changes_24h"] == 1


def test_recompute_profile_calibration_self_vs_drill(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [
        {"event_type": "drill_answer", "mode": "learning", "ts": time.time(),
         "payload": {"score": 0.6, "concept_id": "spring/x"}},
        {"event_type": "drill_answer", "mode": "learning", "ts": time.time(),
         "payload": {"score": 0.8, "concept_id": "spring/y"}},
        {"event_type": "self_assessment", "mode": "learning", "ts": time.time(),
         "payload": {"score": 9.0, "concept_id": "spring/x"}},  # 9/10 → 0.9
    ])
    p = recompute_profile(state_root=sr)
    cal = p["calibration_status"]
    assert cal["drill_avg_score"] == 0.7  # (0.6+0.8)/2
    assert cal["self_assess_avg_norm"] == 0.9
    assert cal["self_vs_drill_delta"] == 0.2  # learner over-confident


def test_recompute_profile_writes_json_file(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [
        {"event_type": "rag_ask", "mode": "learning", "ts": time.time(),
         "payload": {"top_concept_ids": ["spring/bean"]}},
    ])
    recompute_profile(state_root=sr)
    out = sr / "learner" / "profile.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION


def test_recompute_profile_empty_history(tmp_path: Path) -> None:
    sr = tmp_path
    (sr / "learner").mkdir(parents=True)
    p = recompute_profile(state_root=sr)
    assert p["experience_level"] == "junior"
    assert p["activity"]["events_total"] == 0


def test_recompute_profile_preserves_v3_extension_fields(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [
        {"event_type": "rag_ask", "mode": "learning", "ts": time.time(),
         "payload": {"top_concept_ids": ["spring/bean"]}},
    ])
    existing = {
        "schema_version": "v3",
        "learner_id": "default",
        "concepts": {"mastered": [], "proficient": [], "uncertain": [], "underexplored": []},
        "activity": {"events_total": 0},
        "pending_triggers": {"self_assessment": {"id": "s1"}},
        "drill_due": [{"concept_id": "spring/bean"}],
        "manual_overrides": {"mastered": {"value": "spring/di"}},
    }
    (sr / "learner" / "profile.json").write_text(
        json.dumps(existing), encoding="utf-8"
    )

    p = recompute_profile(state_root=sr)

    assert p["schema_version"] == SCHEMA_VERSION
    assert p["pending_triggers"] == existing["pending_triggers"]
    assert p["drill_due"] == existing["drill_due"]
    assert p["manual_overrides"] == existing["manual_overrides"]


def test_recompute_profile_reads_legacy_iso_drill_events(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [
        {
            "event_type": "drill_answer",
            "mode": "learning",
            "ts": "2026-05-07T11:26:18+00:00",
            "concept_ids": ["spring/bean"],
            "total_score": 7,
        },
    ])

    p = recompute_profile(state_root=sr)

    assert p["activity"]["days_active"] == 1
    assert p["calibration_status"]["drill_avg_score"] == 0.7
    assert "spring/bean" in p["concepts"]["underexplored"]


def test_compute_cs_view_matches_legacy_scale() -> None:
    history = [
        {
            "event_id": "d1",
            "event_type": "drill_answer",
            "mode": "learning",
            "ts": "2026-05-07T11:26:18+00:00",
            "concept_ids": ["spring/bean"],
            "total_score": 3,
            "dimensions": {
                "accuracy": 1,
                "depth": 1,
                "practicality": 0,
                "completeness": 1,
            },
            "weak_tags": ["정확도"],
            "source_doc": {"category": "spring"},
        }
    ]

    view = compute_cs_view(history)

    assert view is not None
    assert view["avg_score"] == 3.0
    assert view["level"] == "L2"
    assert view["weak_dimensions"] == ["accuracy", "depth", "practicality"]
    assert view["weak_tags"] == ["정확도"]
    assert view["low_categories"] == ["spring"]


def test_build_unified_projection_is_deterministic() -> None:
    profile = {
        "schema_version": "v3",
        "learner_id": "default",
        "experience_level": "intermediate",
        "concepts": {
            "mastered": ["spring/di"],
            "proficient": ["database/tx"],
            "uncertain": ["spring/bean"],
            "underexplored": ["testing/double"],
        },
        "activity": {"events_total": 100},
        "next_recommendations": [{"concept_id": "spring/bean", "reason": "x"}],
    }
    history = [
        {
            "event_type": "drill_answer",
            "mode": "learning",
            "ts": time.time(),
            "payload": {
                "concept_ids": ["spring/bean"],
                "score": 0.4,
                "dimensions": {"accuracy": 4, "depth": 4, "practicality": 4, "completeness": 4},
                "level": "weak",
            },
        }
    ]

    a = build_unified_projection(profile, history, computed_at=1.0)
    b = build_unified_projection(profile, history, computed_at=1.0)

    assert a == b
    assert a["schema_version"] == UNIFIED_SCHEMA_VERSION
    assert a["coach_view"]["must_skip_explanations_of"] == ["spring/di", "database/tx"]
    assert a["reconciled"]["priority_focus"] == ["spring/bean"]


def test_rebuild_unified_projection_writes_file(tmp_path: Path) -> None:
    sr = _seed(tmp_path, [
        {"event_type": "drill_answer", "mode": "learning", "ts": time.time(),
         "payload": {"concept_ids": ["spring/bean"], "score": 0.8}},
    ])
    profile = recompute_profile(state_root=sr)

    projection = rebuild_unified_projection(
        state_root=sr,
        profile=profile,
        now=123.0,
    )

    out = sr / "learner" / "unified_profile.json"
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved == projection
    assert saved["schema_version"] == UNIFIED_SCHEMA_VERSION
    assert saved["cs_view"]["avg_score"] == 8.0
