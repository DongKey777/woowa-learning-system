"""Tests for mission.extract + mission.graph (F10 forward + backward + gap)."""
from __future__ import annotations

import json
from pathlib import Path

from core.mastery import record_evidence
from mission.extract import (
    ANNOTATION_TO_CONCEPT,
    MissionPattern,
    extract_from_files,
    extract_from_text,
    save_patterns,
)
from mission.graph import (
    backward,
    detect_gap,
    load_concept_graph,
    load_patterns,
    reverse_index,
    walk_prerequisites,
)


# ── extract ─────────────────────────────────────────────────────────────


SAMPLE_CONTROLLER = """\
package x.y;

import org.springframework.web.bind.annotation.RestController;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.transaction.annotation.Transactional;

@RestController
@RequestMapping("/api")
public class ReservationController {
    @Autowired
    private ReservationService service;

    @Transactional
    @PostMapping("/reserve")
    public Response reserve(@RequestBody Request req) {
        return service.reserve(req);
    }
}
"""


def test_extract_tier1_annotations() -> None:
    patterns = extract_from_text("X.java", SAMPLE_CONTROLLER)
    found = {p.value for p in patterns if p.extractor == "regex_tier1"}
    assert "@RestController" in found
    assert "@RequestMapping" in found
    assert "@Autowired" in found
    assert "@Transactional" in found
    assert "@PostMapping" in found
    assert "@RequestBody" in found


def test_annotation_to_concept_mapping_complete() -> None:
    """All annotation map values must be concept-id-shaped (category/slug)."""
    for ann, cid in ANNOTATION_TO_CONCEPT.items():
        assert "/" in cid, f"{ann} → {cid} not concept-id shaped"


def test_extract_tier1_confidence() -> None:
    patterns = extract_from_text("X.java", "@Autowired\n@Transactional")
    assert all(p.confidence == 0.97 for p in patterns)


def test_extract_tier2_method_invocation() -> None:
    code = """\
import org.springframework.jdbc.core.JdbcTemplate;

JdbcTemplate template;
List<X> result = template.query("SELECT * FROM x", rowMapper);
Stream.of(1, 2, 3).filter(...).collect(Collectors.toList());
"""
    patterns = extract_from_text("X.java", code)
    tier2 = [p for p in patterns if p.extractor == "regex_tier2"]
    concepts = {p.matched_concept_id for p in tier2}
    assert "database/jdbc-jpa-mybatis-basics" in concepts
    assert "language/stream-filter-vs-map-decision-mini-card" in concepts


def test_extract_exception_patterns() -> None:
    code = "throw new DataIntegrityViolationException(\"...\");"
    patterns = extract_from_text("X.java", code)
    assert any(p.matched_concept_id == "database/jdbc-jpa-mybatis-basics" for p in patterns)


def test_extract_skips_non_java_files() -> None:
    patterns = extract_from_files([
        ("X.java", "@Autowired"),
        ("README.md", "@Autowired"),
    ])
    assert all(p.file == "X.java" for p in patterns)


def test_save_patterns_writes_json(tmp_path: Path) -> None:
    patterns = [
        MissionPattern("X.java", 5, "annotation", "@Autowired",
                       "spring/dependency-injection-basics", 0.97, "regex_tier1"),
    ]
    out = save_patterns(patterns, "myrepo", state_root=tmp_path, last_built_commit="abc")
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["repo"] == "myrepo"
    assert data["last_built_commit"] == "abc"
    assert data["pattern_count"] == 1
    assert data["patterns"][0]["value"] == "@Autowired"


# ── graph ───────────────────────────────────────────────────────────────


def test_walk_prerequisites_depth_2() -> None:
    graph = {
        "edges": {
            "prerequisite": [
                ["spring/transactional-propagation", "spring/aop-proxy"],
                ["spring/aop-proxy", "spring/bean-di-basics"],
                ["spring/bean-di-basics", "language/object-basics"],
            ]
        }
    }
    prereqs = walk_prerequisites("spring/transactional-propagation", graph, depth=2)
    # 2-hop: aop-proxy (depth 0→1) + bean-di-basics (depth 1→2). language not reached
    assert "spring/aop-proxy" in prereqs
    assert "spring/bean-di-basics" in prereqs
    assert "language/object-basics" not in prereqs


def test_walk_prerequisites_empty_when_missing() -> None:
    graph = {"edges": {"prerequisite": []}}
    assert walk_prerequisites("unknown/x", graph) == []


def test_detect_gap_flags_unmastered_prereqs(tmp_path: Path) -> None:
    graph = {
        "edges": {"prerequisite": [["spring/transactional-propagation", "spring/aop-proxy"]]}
    }
    patterns = [
        MissionPattern("Ctrl.java", 10, "annotation", "@Transactional",
                       "spring/transactional-propagation", 0.97, "regex_tier1")
    ]
    gaps = detect_gap(patterns, graph, state_root=tmp_path)
    assert len(gaps) == 1
    assert gaps[0].missing_prereq == "spring/aop-proxy"
    assert gaps[0].bloom_level == "attempted"
    assert gaps[0].code_anchor == ("Ctrl.java", 10)


def test_detect_gap_skips_mastered_prereqs(tmp_path: Path) -> None:
    # mark aop-proxy familiar
    now = 1_000_000.0
    record_evidence("spring/aop-proxy", "mission_use", state_root=tmp_path, ts=now - 1)
    record_evidence("spring/aop-proxy", "self_assess", state_root=tmp_path, ts=now - 2)
    record_evidence("spring/aop-proxy", "mission_use", state_root=tmp_path, ts=now - 3)
    from core.mastery import promote
    promote("spring/aop-proxy", state_root=tmp_path, now=now)

    graph = {"edges": {"prerequisite": [["spring/transactional-propagation", "spring/aop-proxy"]]}}
    patterns = [
        MissionPattern("Ctrl.java", 10, "annotation", "@Transactional",
                       "spring/transactional-propagation", 0.97, "regex_tier1")
    ]
    gaps = detect_gap(patterns, graph, state_root=tmp_path)
    assert gaps == []


def test_backward_concept_to_code_location() -> None:
    patterns = [
        MissionPattern("A.java", 5, "annotation", "@Autowired", "spring/dependency-injection-basics", 0.97, "regex_tier1"),
        MissionPattern("B.java", 7, "annotation", "@Autowired", "spring/dependency-injection-basics", 0.97, "regex_tier1"),
        MissionPattern("C.java", 12, "annotation", "@Transactional", "spring/transactional-propagation", 0.97, "regex_tier1"),
    ]
    backw = backward(["spring/dependency-injection-basics", "unknown/x"], patterns)
    assert backw["spring/dependency-injection-basics"] == [("A.java", 5), ("B.java", 7)]
    assert backw["unknown/x"] == []


def test_reverse_index_groups_by_concept() -> None:
    patterns = [
        MissionPattern("A.java", 1, "annotation", "@Autowired", "spring/di", 0.97, "regex_tier1"),
        MissionPattern("A.java", 2, "annotation", "@Autowired", "spring/di", 0.97, "regex_tier1"),
    ]
    idx = reverse_index(patterns)
    assert len(idx["spring/di"]) == 2


def test_load_patterns_roundtrip(tmp_path: Path) -> None:
    patterns = [
        MissionPattern("X.java", 5, "annotation", "@Autowired",
                       "spring/dependency-injection-basics", 0.97, "regex_tier1"),
    ]
    save_patterns(patterns, "myrepo", state_root=tmp_path)
    loaded = load_patterns("myrepo", state_root=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].value == "@Autowired"


def test_load_concept_graph_missing_returns_placeholder(tmp_path: Path) -> None:
    g = load_concept_graph(tmp_path / "nope.json")
    assert g["version"] == "missing"
    assert g["nodes"] == {}


def test_load_concept_graph_present(tmp_path: Path) -> None:
    p = tmp_path / "cg.json"
    p.write_text(json.dumps({"version": "v3", "nodes": {"x": {}}, "edges": {"prerequisite": []}}), encoding="utf-8")
    g = load_concept_graph(p)
    assert g["version"] == "v3"
