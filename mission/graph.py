"""F10 backward + mastery gap detect — concept_graph walk + mission_patterns
× mastery_graph cross-reference.

Algorithms:
- backward(query) → for each concept hit, list mission file locations using it
- detect_gap(patterns) → walk prereq edges; for each pattern's concept,
  enumerate prereqs whose mastery bloom < familiar
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core.mastery import DEFAULT_STATE_ROOT, get as mastery_get
from mission.extract import MissionPattern


@dataclass(frozen=True)
class MasteryGap:
    pattern_concept: str
    missing_prereq: str
    bloom_level: str
    code_anchor: tuple[str, int]   # (path, line)


def load_concept_graph(path: Path) -> dict:
    """Load corpus/concept_graph.json. Returns empty graph if missing."""
    if not path.exists():
        return {"version": "missing", "nodes": {}, "edges": {"prerequisite": []}}
    return json.loads(path.read_text(encoding="utf-8"))


def walk_prerequisites(
    concept_id: str,
    graph: dict,
    depth: int = 2,
) -> list[str]:
    """Return list of prerequisite concept_ids reachable within `depth` hops."""
    edges = graph.get("edges", {}).get("prerequisite", [])
    parent_map: dict[str, list[str]] = {}
    for src, dst in edges:
        parent_map.setdefault(src, []).append(dst)
    visited: set[str] = set()
    frontier: list[tuple[str, int]] = [(concept_id, 0)]
    out: list[str] = []
    while frontier:
        cur, d = frontier.pop(0)
        if cur in visited or d >= depth:
            continue
        visited.add(cur)
        for nxt in parent_map.get(cur, []):
            if nxt not in visited:
                out.append(nxt)
                frontier.append((nxt, d + 1))
    return out


def detect_gap(
    patterns: list[MissionPattern],
    graph: dict,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> list[MasteryGap]:
    """For each pattern's concept, find prereqs whose mastery bloom_level < familiar."""
    gaps: list[MasteryGap] = []
    seen: set[tuple[str, str]] = set()  # (pattern_concept, missing_prereq) dedupe
    for p in patterns:
        prereqs = walk_prerequisites(p.matched_concept_id, graph, depth=2)
        for prereq in prereqs:
            key = (p.matched_concept_id, prereq)
            if key in seen:
                continue
            seen.add(key)
            entry = mastery_get(prereq, state_root=state_root)
            level = entry.bloom_level if entry else "attempted"
            if level in ("attempted",):
                gaps.append(MasteryGap(
                    pattern_concept=p.matched_concept_id,
                    missing_prereq=prereq,
                    bloom_level=level,
                    code_anchor=(p.file, p.line),
                ))
    return gaps


def reverse_index(
    patterns: list[MissionPattern],
) -> dict[str, list[tuple[str, int]]]:
    """concept_id → list of (file, line) where used. For F10 backward narrate."""
    idx: dict[str, list[tuple[str, int]]] = {}
    for p in patterns:
        idx.setdefault(p.matched_concept_id, []).append((p.file, p.line))
    return idx


def backward(
    query_concept_ids: list[str],
    patterns: list[MissionPattern],
) -> dict[str, list[tuple[str, int]]]:
    """For each queried concept, return mission file locations using it."""
    idx = reverse_index(patterns)
    return {cid: idx.get(cid, []) for cid in query_concept_ids}


def load_patterns(repo: str, state_root: Path = DEFAULT_STATE_ROOT) -> list[MissionPattern]:
    p = state_root / "repos" / repo / "mission_patterns.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [MissionPattern(**d) for d in data.get("patterns", [])]
