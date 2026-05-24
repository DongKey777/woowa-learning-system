"""Lazy artifact loader (D-A). Loads only what router asked for, gracefully
empty when an artifact does not exist yet (e.g. mission_patterns before
Phase B build).

Token-budget critical: never load all 5 artifacts at once. The router's
`lazy_artifacts` list is the contract.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from core.mastery import DEFAULT_STATE_ROOT, summary as mastery_summary
from core.router import (
    ARTIFACT_ANCHORS,
    ARTIFACT_CONCEPT_GRAPH,
    ARTIFACT_CROSS_CREW,
    ARTIFACT_MASTERY,
    ARTIFACT_MISSION_PATTERNS,
    RouteDecision,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONCEPT_GRAPH = REPO_ROOT / "corpus" / "concept_graph.json"


def load(
    route: RouteDecision,
    repo: str | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
    concept_graph_path: Path = DEFAULT_CONCEPT_GRAPH,
    query: str | None = None,
) -> dict[str, Any]:
    """Return dict {artifact_name: artifact_content_or_summary}.

    Each loader is graceful — missing artifact → empty/None placeholder, never
    raise. When route.need_rag and query is supplied, also invokes
    rag.search → "rag_hits" key (F1 retrieval gap fix).
    """
    out: dict[str, Any] = {}
    for name in route.lazy_artifacts:
        if name == ARTIFACT_MASTERY:
            out[name] = _load_mastery(state_root)
        elif name == ARTIFACT_CONCEPT_GRAPH:
            out[name] = _load_concept_graph(concept_graph_path)
        elif name == ARTIFACT_MISSION_PATTERNS:
            out[name] = _load_mission_patterns(repo, state_root)
        elif name == ARTIFACT_ANCHORS:
            out[name] = _load_anchors(state_root)
        elif name == ARTIFACT_CROSS_CREW:
            out[name] = _load_cross_crew(repo, state_root)
    if route.need_rag and query:
        out["rag_hits"] = _load_rag_hits(query)
    return out


def _load_rag_hits(query: str, top_k: int = 5) -> list[dict]:
    """Invoke rag.search via daemon first (warm <2s); fall back in-process
    (cold 7-8s BGE-M3 load) when daemon unavailable."""
    try:
        from core import daemon as rag_daemon
        hits = rag_daemon.search(query, top_k=top_k, relations_expand=3)
        if hits is not None:
            return hits   # daemon path — warm
    except Exception:
        pass
    try:
        from rag.search import search
        hits = search(query, top_k=top_k, relations_expand=3)
        return [{"concept_id": h.concept_id, "score": round(h.score, 4),
                 "category": h.category, "title": h.title, "source": h.source}
                for h in hits]
    except Exception as exc:
        return [{"error": f"rag.search unavailable: {exc!s}"}]


def _load_mastery(state_root: Path) -> dict:
    """Lightweight summary + top-10 mastered/familiar concepts. Full SQLite stays."""
    s = mastery_summary(state_root=state_root)
    db = state_root / "learner" / "mastery_graph.sqlite"
    if not db.exists():
        return {"summary": s, "by_level": {}}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    by_level: dict[str, list[str]] = {}
    try:
        rows = conn.execute(
            "SELECT concept_id, bloom_level FROM mastery "
            "ORDER BY last_seen_at DESC LIMIT 50"
        ).fetchall()
        for r in rows:
            by_level.setdefault(r["bloom_level"], []).append(r["concept_id"])
    finally:
        conn.close()
    return {"summary": s, "by_level": by_level}


def _load_concept_graph(path: Path) -> dict:
    if not path.exists():
        return {"version": "missing", "nodes": {}, "edges": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_mission_patterns(repo: str | None, state_root: Path) -> dict:
    if not repo:
        return {"repo": None, "patterns": []}
    p = state_root / "repos" / repo / "mission_patterns.json"
    if not p.exists():
        return {"repo": repo, "patterns": [], "missing": True}
    return json.loads(p.read_text(encoding="utf-8"))


def _load_anchors(state_root: Path) -> dict:
    p = state_root / "learner" / "review_anchors.json"
    if not p.exists():
        return {"anchors": [], "missing": True}
    return json.loads(p.read_text(encoding="utf-8"))


def _load_cross_crew(repo: str | None, state_root: Path) -> dict:
    """Parquet file — returns metadata + row count. Full query stays in anchors/match.py."""
    if not repo:
        return {"repo": None, "rows": 0}
    p = state_root / "repos" / repo / "cross_crew_review_graph.parquet"
    if not p.exists():
        return {"repo": repo, "rows": 0, "missing": True, "path": str(p)}
    # Avoid loading the full parquet here — return only path + size for the prompt.
    # Phase C anchors/match.py opens the parquet on demand.
    return {"repo": repo, "path": str(p), "size_bytes": p.stat().st_size, "ready": True}
