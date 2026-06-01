"""Tests for mission.learning_path — F2 family D (learning_path)."""
from __future__ import annotations

import json
from pathlib import Path

from core.mastery import promote, record_evidence
from mission import learning_path
from mission.learning_path import analyze, save_learning_path

LEARNER = "donkey"

# Known (proficient) anchor concept the learner has proven.
KNOWN = "spring/bean-di-basics"
# A second known concept, used as a satisfied prerequisite below.
KNOWN2 = "spring/ioc-di-basics"

# Candidate reachable from KNOWN via next_docs, no prerequisites → ready.
NEXT_READY = "spring/bean-scope-singleton"
# Candidate reachable from KNOWN, all prereqs known → ready.
GATED_OK = "spring/component-scan-basics"
# Candidate reachable from KNOWN, one prereq NOT known → not ready.
GATED_BLOCKED = "spring/aop-proxy-basics"
# The unknown prerequisite that blocks GATED_BLOCKED.
UNKNOWN_PREREQ = "spring/proxy-pattern-basics"
# A concept the learner merely attempted (tracked but not "known").
ATTEMPTED = "language/generics-basics"
# A concept confusable with the known anchor.
CONFUSED = "spring/inject-vs-autowired-basics"


def _graph() -> dict:
    """Small but realistic concept graph. prerequisite [src, dst] = dst is a
    prerequisite OF src. next_docs [src, dst] = after src, dst is next."""
    return {
        "version": "test",
        "nodes": {
            KNOWN: {"category": "spring", "level": "foundation"},
            KNOWN2: {"category": "spring", "level": "foundation"},
            NEXT_READY: {"category": "spring", "level": "intermediate"},
            GATED_OK: {"category": "spring", "level": "intermediate"},
            GATED_BLOCKED: {"category": "spring", "level": "advanced"},
            UNKNOWN_PREREQ: {"category": "spring", "level": "intermediate"},
            CONFUSED: {"category": "spring", "level": "foundation"},
        },
        "edges": {
            "prerequisite": [
                # GATED_OK needs KNOWN2 (which the learner knows) → ready.
                [GATED_OK, KNOWN2],
                # GATED_BLOCKED needs KNOWN (known) + UNKNOWN_PREREQ (not known).
                [GATED_BLOCKED, KNOWN],
                [GATED_BLOCKED, UNKNOWN_PREREQ],
            ],
            "next_docs": [
                # After KNOWN, these are natural next steps.
                [KNOWN, NEXT_READY],
                [KNOWN, GATED_OK],
                [KNOWN, GATED_BLOCKED],
            ],
            "confusable_with": [
                [KNOWN, CONFUSED],
            ],
        },
        "stats": {},
    }


def _write_graph(tmp_path: Path, monkeypatch, graph: dict | None) -> None:
    p = tmp_path / "concept_graph.json"
    if graph is not None:
        p.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(learning_path, "_graph_path", lambda: p)


def _make_proficient(cid: str, state_root: Path) -> None:
    """pr_merge + mentor_accept promotes a concept to proficient."""
    record_evidence(cid, "pr_merge", payload={"repo": "r"}, state_root=state_root)
    record_evidence(cid, "mentor_accept", payload={"repo": "r"},
                    state_root=state_root)
    promote(cid, state_root=state_root)


def _seed_mastery(state_root: Path) -> None:
    _make_proficient(KNOWN, state_root)
    _make_proficient(KNOWN2, state_root)
    # An attempted-only concept: tracked but not "known".
    record_evidence(ATTEMPTED, "mission_use", payload={"repo": "r"},
                    state_root=state_root)
    promote(ATTEMPTED, state_root=state_root)


def test_missing_graph_status(tmp_path: Path, monkeypatch) -> None:
    _write_graph(tmp_path, monkeypatch, None)  # graph file absent
    _seed_mastery(tmp_path)
    rep = analyze(LEARNER, tmp_path)
    assert rep["status"] == "missing_graph"
    assert rep["next_concepts"] == []
    assert rep["graph_sparse"] is True


def test_missing_mastery_status(tmp_path: Path, monkeypatch) -> None:
    _write_graph(tmp_path, monkeypatch, _graph())
    # No mastery rows seeded → total_tracked == 0.
    rep = analyze(LEARNER, tmp_path)
    assert rep["status"] == "missing_mastery"
    assert rep["next_concepts"] == []


def test_ok_next_via_next_docs(tmp_path: Path, monkeypatch) -> None:
    _write_graph(tmp_path, monkeypatch, _graph())
    _seed_mastery(tmp_path)
    rep = analyze(LEARNER, tmp_path)
    assert rep["status"] == "ok"
    ids = {c["concept_id"] for c in rep["next_concepts"]}
    # NEXT_READY surfaced from KNOWN via next_docs.
    assert NEXT_READY in ids
    item = next(c for c in rep["next_concepts"] if c["concept_id"] == NEXT_READY)
    assert item["from_known"] == KNOWN
    assert item["category"] == "spring"
    # Already-known concepts never appear as candidates.
    assert KNOWN not in ids
    assert KNOWN2 not in ids


def test_ready_flag_and_unmet_prereqs(tmp_path: Path, monkeypatch) -> None:
    _write_graph(tmp_path, monkeypatch, _graph())
    _seed_mastery(tmp_path)
    rep = analyze(LEARNER, tmp_path)
    by_id = {c["concept_id"]: c for c in rep["next_concepts"]}

    # GATED_OK's only prereq (KNOWN2) is known → ready.
    assert by_id[GATED_OK]["ready"] is True
    assert by_id[GATED_OK]["prereqs_total"] == 1
    assert by_id[GATED_OK]["prereqs_met"] == 1

    # GATED_BLOCKED has an unknown prereq → not ready.
    assert by_id[GATED_BLOCKED]["ready"] is False
    assert by_id[GATED_BLOCKED]["prereqs_total"] == 2
    assert by_id[GATED_BLOCKED]["prereqs_met"] == 1

    # The unmet prereq is reported with its (untracked) level.
    unmet = [u for u in rep["unmet_prereqs"]
             if u["concept_id"] == GATED_BLOCKED]
    missing = {u["missing_prereq"]: u["prereq_level"] for u in unmet}
    assert UNKNOWN_PREREQ in missing
    assert missing[UNKNOWN_PREREQ] == "미학습"
    # The satisfied prereq (KNOWN) is NOT listed as unmet.
    assert KNOWN not in missing


def test_confusable_pairs_surface(tmp_path: Path, monkeypatch) -> None:
    _write_graph(tmp_path, monkeypatch, _graph())
    _seed_mastery(tmp_path)
    rep = analyze(LEARNER, tmp_path)
    pairs = {frozenset((p["a"], p["b"])) for p in rep["confusable_pairs"]}
    # KNOWN ↔ CONFUSED edge touches the known set → surfaced.
    assert frozenset((KNOWN, CONFUSED)) in pairs


def test_save_round_trip_and_key_parity(tmp_path: Path, monkeypatch) -> None:
    _write_graph(tmp_path, monkeypatch, _graph())
    _seed_mastery(tmp_path)
    rep = analyze(LEARNER, tmp_path)
    out = save_learning_path(rep, tmp_path)
    assert out == tmp_path / "learner" / "learning_path.json"
    loaded = json.loads(out.read_text(encoding="utf-8"))

    expected_keys = {
        "learner_login", "status", "mastery_distribution", "next_concepts",
        "unmet_prereqs", "confusable_pairs", "graph_sparse", "counts",
        "generated_at",
    }
    assert set(loaded.keys()) == expected_keys
    assert loaded["learner_login"] == LEARNER
    assert loaded["mastery_distribution"]["proficient"] == 2
    assert loaded["mastery_distribution"]["total"] == 3
