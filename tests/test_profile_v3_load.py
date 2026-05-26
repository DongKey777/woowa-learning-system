"""Phase Y11 PR-A — load_profile + save_profile v3 schema 지원 회귀.

v3 profile.json schema:
  {
    "schema_version": "v3",
    "learner_id": "default",
    "computed_at": <float>,
    "concepts": { "mastered": [...], "uncertain": [...], "proficient": [...], "underexplored": [...] },
    "activity": { "events_total": N, ... },
    "pending_triggers": { ... },           # ephemeral keys (review_drill) skipped on save
    "drill_due": [...],
  }
"""
from __future__ import annotations

import json

from core.state import (
    EPHEMERAL_PENDING_KEYS,
    LearnerProfile,
    load_profile,
    save_profile,
)


def _write_v3_profile(state_root, **overrides):
    learner_dir = state_root / "learner"
    learner_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "v3",
        "learner_id": "default",
        "computed_at": 1700000000.0,
        "concepts": {
            "mastered": ["spring/di", "spring/mvc"],
            "proficient": ["db/tx"],
            "uncertain": ["alg/dp", "alg/dijkstra", "ds/treemap"],
            "underexplored": [],
        },
        "activity": {"events_total": 1234},
        "drill_due": [],
        "pending_triggers": {},
    }
    data.update(overrides)
    (learner_dir / "profile.json").write_text(json.dumps(data), encoding="utf-8")
    return learner_dir / "profile.json"


def test_load_profile_v3_returns_concepts(tmp_path):
    _write_v3_profile(tmp_path)

    p = load_profile("default", state_root=tmp_path)

    assert len(p.mastered_concepts) == 2
    assert len(p.uncertain_concepts) == 3
    assert p.total_events == 1234
    assert p.last_updated == 1700000000.0  # computed_at fallback


def test_load_profile_flat_schema_back_compat(tmp_path):
    learner_dir = tmp_path / "learner"
    learner_dir.mkdir(parents=True)
    # legacy flat schema (no schema_version / no concepts wrapper)
    (learner_dir / "profile.json").write_text(json.dumps({
        "learner_id": "default",
        "mastered_concepts": ["a", "b", "c"],
        "uncertain_concepts": ["x"],
        "total_events": 99,
        "last_updated": 1690000000.0,
    }), encoding="utf-8")

    p = load_profile("default", state_root=tmp_path)

    assert p.mastered_concepts == ["a", "b", "c"]
    assert p.total_events == 99


def test_save_profile_v3_round_trip(tmp_path):
    _write_v3_profile(tmp_path)
    p = load_profile("default", state_root=tmp_path)
    p.mastered_concepts.append("spring/transactional")

    save_profile(p, state_root=tmp_path)

    raw = json.loads((tmp_path / "learner" / "profile.json").read_text())
    assert raw["schema_version"] == "v3"
    assert "concepts" in raw
    assert "spring/transactional" in raw["concepts"]["mastered"]
    assert raw["activity"]["events_total"] == 1234


def test_save_profile_skips_ephemeral_pending(tmp_path):
    _write_v3_profile(tmp_path, pending_triggers={"self_assessment": {"id": "s1"}})
    p = load_profile("default", state_root=tmp_path)
    # In-memory pending includes both persistent + ephemeral
    p.pending_triggers["review_drill"] = {"concept_id": "alg/dp"}
    p.pending_triggers["self_assessment"] = {"id": "s1"}

    save_profile(p, state_root=tmp_path)

    raw = json.loads((tmp_path / "learner" / "profile.json").read_text())
    assert "review_drill" not in raw["pending_triggers"]
    assert "self_assessment" in raw["pending_triggers"]
    assert "review_drill" in EPHEMERAL_PENDING_KEYS  # contract


def test_load_pending_triggers_merges_dedicated_files(tmp_path):
    _write_v3_profile(tmp_path, pending_triggers={"self_assessment": {"id": "s1"}})
    learner_dir = tmp_path / "learner"
    (learner_dir / "pending_triggers.json").write_text(json.dumps({
        "self_assessment": {"id": "from_pt_file"},
    }), encoding="utf-8")
    (learner_dir / "drill_pending.json").write_text(json.dumps({
        "concept_id": "alg/dp", "question": "What is DP?",
    }), encoding="utf-8")

    p = load_profile("default", state_root=tmp_path)

    # Dedicated files take precedence for ownership keys.
    assert p.pending_triggers["self_assessment"]["id"] == "from_pt_file"
    # drill_pending.json populates review_drill (was not in profile dict).
    assert p.pending_triggers["review_drill"]["concept_id"] == "alg/dp"


def test_empty_profile_path_returns_empty_learner_profile(tmp_path):
    p = load_profile("default", state_root=tmp_path)
    assert isinstance(p, LearnerProfile)
    assert p.mastered_concepts == []
    assert p.uncertain_concepts == []
