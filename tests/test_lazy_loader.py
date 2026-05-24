"""Tests for core.lazy_loader — graceful missing artifact handling."""
from __future__ import annotations

import json
from pathlib import Path

from core.lazy_loader import load
from core.mastery import record_evidence
from core.router import (
    ARTIFACT_ANCHORS,
    ARTIFACT_CONCEPT_GRAPH,
    ARTIFACT_CROSS_CREW,
    ARTIFACT_MASTERY,
    ARTIFACT_MISSION_PATTERNS,
    RouteDecision,
    route,
)


def _make_route(artifacts: list[str]) -> RouteDecision:
    return RouteDecision(
        mode="test", need_rag=False, need_mission_ctx=False, need_anchors=False,
        personas=[], budget_tokens=0, lazy_artifacts=artifacts, reason="test",
    )


def test_load_only_asked_artifacts(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_MASTERY])
    out = load(r, state_root=tmp_path)
    assert set(out.keys()) == {ARTIFACT_MASTERY}


def test_mastery_graceful_when_empty(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_MASTERY])
    out = load(r, state_root=tmp_path)
    assert out[ARTIFACT_MASTERY]["summary"]["total_tracked"] == 0
    assert out[ARTIFACT_MASTERY]["by_level"] == {}


def test_mastery_populated_after_evidence(tmp_path: Path) -> None:
    record_evidence("spring/bean", "mission_use", state_root=tmp_path)
    r = _make_route([ARTIFACT_MASTERY])
    out = load(r, state_root=tmp_path)
    assert out[ARTIFACT_MASTERY]["summary"]["total_tracked"] == 1
    assert "spring/bean" in out[ARTIFACT_MASTERY]["by_level"]["attempted"]


def test_concept_graph_missing_returns_placeholder(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_CONCEPT_GRAPH])
    out = load(r, state_root=tmp_path, concept_graph_path=tmp_path / "no.json")
    assert out[ARTIFACT_CONCEPT_GRAPH]["version"] == "missing"
    assert out[ARTIFACT_CONCEPT_GRAPH]["nodes"] == {}


def test_concept_graph_loads_when_present(tmp_path: Path) -> None:
    path = tmp_path / "cg.json"
    path.write_text(json.dumps({"version": "v3", "nodes": {"x": {}}, "edges": {}}), encoding="utf-8")
    r = _make_route([ARTIFACT_CONCEPT_GRAPH])
    out = load(r, state_root=tmp_path, concept_graph_path=path)
    assert out[ARTIFACT_CONCEPT_GRAPH]["version"] == "v3"
    assert "x" in out[ARTIFACT_CONCEPT_GRAPH]["nodes"]


def test_mission_patterns_missing_with_repo(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_MISSION_PATTERNS])
    out = load(r, repo="myrepo", state_root=tmp_path)
    assert out[ARTIFACT_MISSION_PATTERNS]["missing"] is True
    assert out[ARTIFACT_MISSION_PATTERNS]["patterns"] == []


def test_mission_patterns_no_repo(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_MISSION_PATTERNS])
    out = load(r, repo=None, state_root=tmp_path)
    assert out[ARTIFACT_MISSION_PATTERNS]["repo"] is None
    assert out[ARTIFACT_MISSION_PATTERNS]["patterns"] == []


def test_anchors_missing_returns_empty(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_ANCHORS])
    out = load(r, state_root=tmp_path)
    assert out[ARTIFACT_ANCHORS]["anchors"] == []
    assert out[ARTIFACT_ANCHORS]["missing"] is True


def test_cross_crew_missing(tmp_path: Path) -> None:
    r = _make_route([ARTIFACT_CROSS_CREW])
    out = load(r, repo="myrepo", state_root=tmp_path)
    assert out[ARTIFACT_CROSS_CREW]["missing"] is True
    assert out[ARTIFACT_CROSS_CREW]["rows"] == 0


def test_cross_crew_ready_when_file_present(tmp_path: Path) -> None:
    """Build a real parquet (3 rows) — _load_cross_crew now reads top-N matches."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "repos" / "myrepo" / "cross_crew_review_graph.parquet"
    p.parent.mkdir(parents=True)
    table = pa.table({
        "anchor_thread_id": ["t1", "t2", "t3"],
        "anchor_pr": [10, 11, 12],
        "anchor_path": ["A.java", "B.java", "C.java"],
        "anchor_mentor": ["m1", "m2", "m3"],
        "candidate_pr": [20, 21, 22],
        "crew_login": ["alice", "bob", "carol"],
        "candidate_reviewer": ["rev1", "rev2", "rev3"],
        "jaccard": [0.5, 0.6, 0.7],
        "embed_cosine": [0.8, 0.9, 0.85],
        "candidate_comment": ["c1", "c2", "c3"],
    })
    pq.write_table(table, str(p))
    r = _make_route([ARTIFACT_CROSS_CREW])
    out = load(r, repo="myrepo", state_root=tmp_path)
    cc = out[ARTIFACT_CROSS_CREW]
    assert cc["ready"] is True
    assert cc["total_rows"] == 3
    assert cc["top_matches"][0]["embed_cosine"] == 0.9
    assert cc["top_matches"][0]["crew_login"] == "bob"


def test_end_to_end_with_router_decision(tmp_path: Path) -> None:
    """Real route() → load() chain — cs_qa loads concept_graph + mastery only."""
    r = route("DI가 뭐야")
    out = load(r, state_root=tmp_path, concept_graph_path=tmp_path / "missing.json")
    assert set(out.keys()) == {ARTIFACT_CONCEPT_GRAPH, ARTIFACT_MASTERY}
    # f11_anchor loads anchors + cross_crew + mission_patterns
    r2 = route("PR 37 정밀 비교")
    out2 = load(r2, repo="x", state_root=tmp_path)
    assert set(out2.keys()) == {ARTIFACT_ANCHORS, ARTIFACT_CROSS_CREW, ARTIFACT_MISSION_PATTERNS}
