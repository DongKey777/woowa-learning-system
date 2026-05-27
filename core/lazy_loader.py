"""Lazy artifact loader (D-A). Loads only what router asked for, gracefully
empty when an artifact does not exist yet (e.g. mission_patterns before
Phase B build).

Token-budget critical: never load all 5 artifacts at once. The router's
`lazy_artifacts` list is the contract.
"""
from __future__ import annotations

import json
import os
import sqlite3
from functools import lru_cache
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
from core.state import LearnerProfile
from rag.search import SearchHit

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONCEPT_GRAPH = REPO_ROOT / "corpus" / "concept_graph.json"
_PERSONALIZATION_DISABLED = {"0", "false", "off", "no"}


def _empty_mastery() -> dict:
    counts = {l: 0 for l in ("attempted", "familiar", "proficient", "mastered")}
    return {"summary": {"counts": counts, "total_tracked": 0}, "by_level": {}}


def load(
    route: RouteDecision,
    repo: str | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
    concept_graph_path: Path = DEFAULT_CONCEPT_GRAPH,
    query: str | None = None,
    corpus=None,
    learner_profile: LearnerProfile | None = None,
) -> dict[str, Any]:
    """Return dict {artifact_name: artifact_content_or_summary}.

    When corpus is supplied (daemon ask path), uses in-process rag.search
    instead of daemon socket — avoids daemon self-recursion deadlock.
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
        hits, personalization = _load_rag_hits(
            query, corpus=corpus, learner_profile=learner_profile,
            state_root=state_root,
        )
        out["rag_hits"] = hits
        if personalization is not None:
            out["personalization"] = personalization
    return out


def _load_rag_hits(
    query: str,
    top_k: int = 5,
    corpus=None,
    learner_profile: LearnerProfile | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> tuple[list[dict], dict | None]:
    """Invoke rag.search.

    Priority: (1) in-process if corpus injected (daemon ask path — avoids
    self-recursion), (2) daemon socket (bin/ask fallback warm path),
    (3) in-process cold (daemon unavailable).
    """
    if corpus is not None:
        # In-process path (daemon ask handler injects corpus)
        try:
            from rag.search import search
            from core.lexical_fusion import make_lexical_fusion_fn
            hits = search(query, top_k=top_k, relations_expand=3,
                          rerank_fn=make_lexical_fusion_fn(corpus, expand=8),
                          corpus=corpus)
            hits, personalization = _personalize_hits(hits, learner_profile)
            return _hits_to_dicts(hits), personalization
        except Exception as exc:
            return [{"error": f"rag.search failed: {exc!s}"}], None
    try:
        from core import daemon as rag_daemon
        hit_dicts = rag_daemon.search(query, top_k=top_k,
                                      relations_expand=3, state_root=state_root)
        if hit_dicts is not None:
            hits = _dicts_to_hits(hit_dicts)
            if not hits:
                return hit_dicts, None
            hits, personalization = _personalize_hits(hits, learner_profile)
            return _hits_to_dicts(hits), personalization
    except Exception:
        pass
    try:
        from rag.search import search
        hits = search(query, top_k=top_k, relations_expand=3)
        hits, personalization = _personalize_hits(hits, learner_profile)
        return _hits_to_dicts(hits), personalization
    except Exception as exc:
        return [{"error": f"rag.search unavailable: {exc!s}"}], None


def personalization_active() -> bool:
    value = os.environ.get("WOOWA_PERSONALIZATION_ACTIVE", "on").strip().lower()
    return value not in _PERSONALIZATION_DISABLED


def _mastered_like(profile: LearnerProfile | None) -> list[str]:
    return [] if profile is None else (
        list(profile.mastered_concepts)
        + list(getattr(profile, "proficient_concepts", []))
    )


def _personalize_hits(
    hits: list[SearchHit],
    learner_profile: LearnerProfile | None,
) -> tuple[list[SearchHit], dict | None]:
    enabled = personalization_active()
    mastered = _mastered_like(learner_profile)
    uncertain = list(learner_profile.uncertain_concepts) if learner_profile else []
    if not enabled:
        if mastered or uncertain:
            return list(hits), {"enabled": False, "mastered_applied": [],
                                "uncertain_applied": []}
        return list(hits), None
    if not hits or (not mastered and not uncertain):
        return list(hits), None

    from rag.personalization import adjust, _matches_family

    adjusted = adjust(hits, mastered_concepts=mastered,
                      uncertain_concepts=uncertain)
    return adjusted, {
        "enabled": True,
        "mastered_applied": [c for c in mastered
                             if any(_matches_family(h.concept_id, c) for h in hits)],
        "uncertain_applied": [c for c in uncertain
                              if any(_matches_family(h.concept_id, c) for h in hits)],
    }


def _hits_to_dicts(hits: list[SearchHit]) -> list[dict]:
    return [
        {"concept_id": h.concept_id, "score": round(h.score, 4),
         "category": h.category, "title": h.title, "source": h.source}
        for h in hits
    ]


def _dicts_to_hits(hit_dicts: list[dict]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for h in hit_dicts:
        if not isinstance(h, dict) or "error" in h:
            continue
        cid = h.get("concept_id")
        if not cid:
            continue
        hits.append(SearchHit(
            concept_id=str(cid), score=float(h.get("score") or 0.0),
            category=str(h.get("category") or ""), title=str(h.get("title") or cid),
            source=str(h.get("source") or "daemon")))
    return hits


def _load_mastery(state_root: Path) -> dict:
    """Lightweight summary + top-10 mastered/familiar concepts.
    Single sqlite connection (summary + by_level in one open)."""
    db = state_root / "learner" / "mastery_graph.sqlite"
    if not db.exists():
        return _empty_mastery()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        # summary
        counts = {l: 0 for l in ("attempted", "familiar", "proficient", "mastered")}
        try:
            for row in conn.execute("SELECT bloom_level, COUNT(*) c FROM mastery GROUP BY bloom_level"):
                counts[row["bloom_level"]] = row["c"]
        except sqlite3.OperationalError:
            return _empty_mastery()
        # by_level
        by_level: dict[str, list[str]] = {}
        rows = conn.execute(
            "SELECT concept_id, bloom_level FROM mastery "
            "ORDER BY last_seen_at DESC LIMIT 50"
        ).fetchall()
        for r in rows:
            by_level.setdefault(r["bloom_level"], []).append(r["concept_id"])
    finally:
        conn.close()
    return {"summary": {"counts": counts, "total_tracked": sum(counts.values())},
            "by_level": by_level}


def _load_concept_graph(path: Path) -> dict:
    if not path.exists():
        return {"version": "missing", "nodes": {}, "edges": {}}
    mtime = path.stat().st_mtime
    return _load_concept_graph_cached(str(path), mtime)


@lru_cache(maxsize=2)
def _load_concept_graph_cached(path_str: str, mtime: float) -> dict:
    """mtime as cache key — auto-invalidates when concept_graph rebuilt."""
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


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


def _load_cross_crew(repo: str | None, state_root: Path, top_n: int = 3) -> dict:
    """Parquet file — returns top-N matches by embed_cosine for prompt evidence.

    Pre-built parquet (`bin/cross-crew-build`) holds all anchor×candidate scored
    rows; surfacing the top-N here keeps the F11 prompt under 1500 tokens while
    giving real cross-crew evidence (crew_login + reviewer + comment snippet).
    """
    if not repo:
        return {"repo": None, "rows": 0}
    p = state_root / "repos" / repo / "cross_crew_review_graph.parquet"
    if not p.exists():
        return {"repo": repo, "rows": 0, "missing": True, "path": str(p)}
    return _load_cross_crew_cached(str(p), p.stat().st_mtime, top_n)


@lru_cache(maxsize=4)
def _load_cross_crew_cached(path_str: str, mtime: float, top_n: int) -> dict:
    import pyarrow.parquet as pq

    table = pq.read_table(path_str)
    df = table.to_pandas()
    total = len(df)
    if total == 0:
        return {"path": path_str, "size_bytes": Path(path_str).stat().st_size,
                "ready": True, "total_rows": 0, "top_matches": []}
    top = df.nlargest(top_n, "embed_cosine")
    matches = []
    for _, r in top.iterrows():
        matches.append({
            "anchor_pr": int(r["anchor_pr"]),
            "anchor_thread_id": r["anchor_thread_id"],
            "anchor_path": r["anchor_path"],
            "anchor_mentor": r["anchor_mentor"],
            "candidate_pr": int(r["candidate_pr"]),
            "crew_login": r["crew_login"],
            "candidate_reviewer": r["candidate_reviewer"],
            "jaccard": round(float(r["jaccard"]), 3),
            "embed_cosine": round(float(r["embed_cosine"]), 3),
            "comment_snippet": (r["candidate_comment"] or "")[:240],
        })
    return {
        "path": path_str,
        "size_bytes": Path(path_str).stat().st_size,
        "ready": True,
        "total_rows": total,
        "top_matches": matches,
    }
