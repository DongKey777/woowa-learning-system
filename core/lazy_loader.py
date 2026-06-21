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
    ARTIFACT_CROSS_MISSION,
    ARTIFACT_MASTERY,
    ARTIFACT_MEMORY_REVIEW,
    ARTIFACT_MISSION_PATTERNS,
    ARTIFACT_PR_DIFF_EVOLUTION,
    ARTIFACT_PR_REVIEW,
    ARTIFACT_REVIEWER_PROFILE,
    ARTIFACT_LEARNING_PATH,
    ARTIFACT_PR_META,
    ARTIFACT_THREAD_RECON,
    ARTIFACT_TEMPORAL,
    ARTIFACT_META_ANALYTICS,
    ARTIFACT_COHORT,
    ARTIFACT_PREDICT,
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
        elif name == ARTIFACT_PR_DIFF_EVOLUTION:
            out[name] = _load_pr_diff_evolution(repo, state_root)
        elif name == ARTIFACT_CROSS_MISSION:
            out[name] = _load_cross_mission(state_root)
        elif name == ARTIFACT_MEMORY_REVIEW:
            out[name] = _load_memory_review(state_root)
        elif name == ARTIFACT_PR_REVIEW:
            out[name] = _load_pr_review(repo, state_root)
        elif name == ARTIFACT_REVIEWER_PROFILE:
            out[name] = _load_reviewer_profile(repo, state_root)
        elif name == ARTIFACT_LEARNING_PATH:
            out[name] = _load_learning_path(state_root)
        elif name == ARTIFACT_PR_META:
            out[name] = _load_pr_meta(repo, state_root)
        elif name == ARTIFACT_THREAD_RECON:
            out[name] = _load_thread_recon(repo, state_root)
        elif name == ARTIFACT_TEMPORAL:
            out[name] = _load_temporal(repo, state_root)
        elif name == ARTIFACT_META_ANALYTICS:
            out[name] = _load_meta_analytics(state_root)
        elif name == ARTIFACT_COHORT:
            out[name] = _load_cohort(repo, state_root)
        elif name == ARTIFACT_PREDICT:
            out[name] = _load_predict(repo, state_root)
    if route.need_rag and query:
        hits, personalization, dense_margin = _load_rag_hits(
            query, corpus=corpus, learner_profile=learner_profile,
            state_root=state_root,
        )
        out["rag_hits"] = hits
        out["rag_dense_margin"] = dense_margin
        if personalization is not None:
            out["personalization"] = personalization
    return out


def _load_rag_hits(
    query: str,
    top_k: int = 5,
    corpus=None,
    learner_profile: LearnerProfile | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> tuple[list[dict], dict | None, float | None]:
    """Invoke rag.search. Returns (hit_dicts, personalization, dense_margin).

    Priority: (1) in-process if corpus injected (daemon ask path — avoids
    self-recursion), (2) daemon socket (bin/ask fallback warm path),
    (3) in-process cold (daemon unavailable). dense_margin is the raw dense
    top1-top2 cosine gap (in-process path only; None elsewhere) for rerank gating.
    """
    if corpus is not None:
        # In-process path (daemon ask handler injects corpus)
        try:
            from rag.search import search_with_margin
            from core.lexical_fusion import make_lexical_fusion_fn
            hits, dense_margin = search_with_margin(
                query, top_k=top_k, relations_expand=3,
                rerank_fn=make_lexical_fusion_fn(corpus, expand=8),
                corpus=corpus)
            hits, personalization = _personalize_hits(hits, learner_profile)
            return _hits_to_dicts(hits, corpus=corpus), personalization, dense_margin
        except Exception as exc:
            return [{"error": f"rag.search failed: {exc!s}"}], None, None
    try:
        from core import daemon as rag_daemon
        hit_dicts = rag_daemon.search(query, top_k=top_k,
                                      relations_expand=3, state_root=state_root)
        if hit_dicts is not None:
            hits = _dicts_to_hits(hit_dicts)
            if not hits:
                return hit_dicts, None, None
            hits, personalization = _personalize_hits(hits, learner_profile)
            return _hits_to_dicts(hits, corpus=corpus), personalization, None
    except Exception:
        pass
    try:
        from rag.search import search
        hits = search(query, top_k=top_k, relations_expand=3)
        hits, personalization = _personalize_hits(hits, learner_profile)
        return _hits_to_dicts(hits, corpus=corpus), personalization, None
    except Exception as exc:
        return [{"error": f"rag.search unavailable: {exc!s}"}], None, None


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


def _summary_sentence_cut(text: str, cap: int = 250) -> str:
    """Truncate at a sentence boundary near `cap` (avoids mid-sentence cuts that
    cripple the session-side rerank signal — adversarial review defect A)."""
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    head = text[:cap]
    for sep in (". ", ".\n", "? ", "! ", "다. ", "다.\n", "요. ", "요.\n"):
        i = head.rfind(sep)
        if i > cap * 0.5:
            return head[: i + len(sep)].strip()
    return head.strip()


def _hits_to_dicts(hits: list[SearchHit], corpus=None, body_top_k: int = 2) -> list[dict]:
    """Render hits to prompt dicts. When `corpus` is supplied (daemon ask path),
    inject top-K summary + top-2 body so the session can rerank + answer in one
    reasoning pass (no hidden Read pass / parametric hallucination). top-2 body
    grounds the likely answer concept + its close alternative at modest token
    cost. The gate path (daemon search action) calls without corpus → unchanged
    5-field dicts."""
    concepts = getattr(corpus, "concepts", None) if corpus is not None else None
    out: list[dict] = []
    for idx, h in enumerate(hits):
        d = {"concept_id": h.concept_id, "score": round(h.score, 4),
             "category": h.category, "title": h.title, "source": h.source}
        if concepts is not None:
            concept = concepts.get(h.concept_id) or {}
            summary = _summary_sentence_cut(concept.get("summary") or "", 250)
            if summary:
                d["summary"] = summary
            if idx < body_top_k:
                body = (concept.get("body_markdown") or "").strip()
                if body:
                    d["body"] = body[:800]
        out.append(d)
    return out


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


def _load_pr_diff_evolution(repo: str | None, state_root: Path) -> dict:
    """Prebuilt code-evolution artifact (bin/pr-diff-evolution-build).

    Budget-bounded: the builder already caps rounds/links/hotspots/smells, so we
    just gate to the most recent PRs and surface as-is. ``missing=True`` when the
    builder hasn't run, mirroring _load_mission_patterns."""
    if not repo:
        return {"repo": None, "prs": [], "missing": True}
    p = state_root / "repos" / repo / "pr_diff_evolution.json"
    if not p.exists():
        return {"repo": repo, "prs": [], "missing": True}
    return _load_pr_diff_evolution_cached(str(p), p.stat().st_mtime)


def _load_pr_review(repo: str | None, state_root: Path) -> dict:
    """Prebuilt received-review artifact (bin/learn-pr-review-build).

    Repo-scoped (mirrors _load_pr_diff_evolution): reads
    state/repos/<repo>/pr_review.json. ``missing=True`` when the builder
    hasn't run, so the coach render degrades gracefully."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True,
                "anchors": [], "unresolved_threads": [], "pr_overview": []}
    p = state_root / "repos" / repo / "pr_review.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True,
                "anchors": [], "unresolved_threads": [], "pr_overview": []}
    return _load_pr_review_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_pr_review_cached(path_str: str, mtime: float, top_n: int = 10) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "anchors": data.get("anchors", [])[:top_n],
        "unresolved_threads": data.get("unresolved_threads", [])[:top_n],
        "pr_overview": data.get("pr_overview", [])[:top_n],
        "counts": data.get("counts", {}),
    }


def _load_reviewer_profile(repo: str | None, state_root: Path) -> dict:
    """Prebuilt reviewer-profile artifact (bin/reviewer-profile-build).

    Repo-scoped (mirrors _load_pr_review): reads
    state/repos/<repo>/reviewer_profile.json. ``missing=True`` when the
    builder hasn't run, so the coach render degrades gracefully."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True,
                "recurring_topics": [], "hotspot_files": [], "sample_threads": []}
    p = state_root / "repos" / repo / "reviewer_profile.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True,
                "recurring_topics": [], "hotspot_files": [], "sample_threads": []}
    return _load_reviewer_profile_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_reviewer_profile_cached(path_str: str, mtime: float, top_n: int = 10) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "reviewer_login": data.get("reviewer_login"),
        "nickname": data.get("nickname"),
        "comments_total": data.get("comments_total"),
        "comments_on_learner_pr": data.get("comments_on_learner_pr"),
        "prs_reviewed": data.get("prs_reviewed"),
        "review_states": data.get("review_states", {}),
        "first_seen": data.get("first_seen"),
        "last_seen": data.get("last_seen"),
        "recurring_topics": data.get("recurring_topics", [])[:top_n],
        "hotspot_files": data.get("hotspot_files", [])[:top_n],
        "sample_threads": data.get("sample_threads", [])[:3],
    }


def _load_learning_path(state_root: Path) -> dict:
    """Learner-scoped learning-path artifact (bin/learn-learning-path-build).

    Cross-repo, so it ignores `repo` and reads state/learner/learning_path.json
    (mirrors _load_memory_review). ``missing=True`` when unbuilt."""
    p = state_root / "learner" / "learning_path.json"
    if not p.exists():
        return {"ready": False, "missing": True, "next_concepts": [],
                "unmet_prereqs": [], "confusable_pairs": []}
    return _load_learning_path_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=2)
def _load_learning_path_cached(path_str: str, mtime: float, top_n: int = 12) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "status": data.get("status"),
        "mastery_distribution": data.get("mastery_distribution", {}),
        "next_concepts": data.get("next_concepts", [])[:top_n],
        "unmet_prereqs": data.get("unmet_prereqs", [])[:top_n],
        "confusable_pairs": data.get("confusable_pairs", [])[:8],
        "graph_sparse": data.get("graph_sparse", False),
    }


def _load_pr_meta(repo: str | None, state_root: Path) -> dict:
    """Prebuilt pr-meta artifact (bin/learn-pr-meta-build).

    Repo-scoped (mirrors _load_pr_review): reads
    state/repos/<repo>/pr_meta.json. ``missing=True`` when the builder
    hasn't run, so the coach render degrades gracefully."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True,
                "cohort": {}, "prs": []}
    p = state_root / "repos" / repo / "pr_meta.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True,
                "cohort": {}, "prs": []}
    return _load_pr_meta_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_pr_meta_cached(path_str: str, mtime: float, top_n: int = 10) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "learner_login": data.get("learner_login"),
        "cohort": data.get("cohort", {}),
        "prs": data.get("prs", [])[:top_n],
        "counts": data.get("counts", {}),
    }


def _load_thread_recon(repo: str | None, state_root: Path) -> dict:
    """Prebuilt thread-recon artifact (bin/learn-thread-recon-build).

    Repo-scoped (mirrors _load_pr_meta): reads
    state/repos/<repo>/thread_recon.json. ``missing=True`` when unbuilt."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True, "threads": []}
    p = state_root / "repos" / repo / "thread_recon.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True, "threads": []}
    return _load_thread_recon_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_thread_recon_cached(path_str: str, mtime: float, top_n: int = 8) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "learner_login": data.get("learner_login"),
        "threads": data.get("threads", [])[:top_n],
        "counts": data.get("counts", {}),
    }


def _load_temporal(repo: str | None, state_root: Path) -> dict:
    """Prebuilt temporal artifact (bin/learn-temporal-build).

    Repo-scoped (mirrors _load_pr_meta): reads
    state/repos/<repo>/temporal.json. ``missing=True`` when unbuilt."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True,
                "prs": [], "aggregate": {}}
    p = state_root / "repos" / repo / "temporal.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True,
                "prs": [], "aggregate": {}}
    return _load_temporal_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_temporal_cached(path_str: str, mtime: float, top_n: int = 10) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "learner_login": data.get("learner_login"),
        "prs": data.get("prs", [])[:top_n],
        "aggregate": data.get("aggregate", {}),
        "counts": data.get("counts", {}),
    }


def _load_meta_analytics(state_root: Path) -> dict:
    """Learner-scoped meta-analytics artifact (bin/learn-meta-analytics-build).

    Cross-repo, so it ignores `repo` and reads
    state/learner/meta_analytics.json (mirrors _load_learning_path).
    ``missing=True`` when unbuilt."""
    p = state_root / "learner" / "meta_analytics.json"
    if not p.exists():
        return {"ready": False, "missing": True, "repeated_concepts": [],
                "mode_mix": [], "quality_trend": {}, "mastery_distribution": {}}
    return _load_meta_analytics_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=2)
def _load_meta_analytics_cached(path_str: str, mtime: float, top_n: int = 12) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "status": data.get("status"),
        "learner_login": data.get("learner_login"),
        "repeated_concepts": data.get("repeated_concepts", [])[:top_n],
        "mode_mix": data.get("mode_mix", []),
        "quality_trend": data.get("quality_trend", {}),
        "mastery_distribution": data.get("mastery_distribution", {}),
        "counts": data.get("counts", {}),
    }


def _load_cohort(repo: str | None, state_root: Path) -> dict:
    """Prebuilt cohort artifact (bin/learn-cohort-build).

    Repo-scoped (mirrors _load_pr_meta): reads
    state/repos/<repo>/cohort.json. ``missing=True`` when unbuilt."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True,
                "cohort": {}, "learner_prs": [], "comparison": []}
    p = state_root / "repos" / repo / "cohort.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True,
                "cohort": {}, "learner_prs": [], "comparison": []}
    return _load_cohort_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_cohort_cached(path_str: str, mtime: float, top_n: int = 10) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "learner_login": data.get("learner_login"),
        "cohort": data.get("cohort", {}),
        "learner_prs": data.get("learner_prs", [])[:top_n],
        "comparison": data.get("comparison", []),
        "counts": data.get("counts", {}),
    }


def _load_predict(repo: str | None, state_root: Path) -> dict:
    """Prebuilt predict artifact (bin/learn-predict-build).

    Repo-scoped (mirrors _load_cohort): reads state/repos/<repo>/predict.json.
    Synthesizes the C/F/H surfaces, so it's only meaningful once those have
    been built. ``missing=True`` when the predict builder hasn't run."""
    if not repo:
        return {"repo": None, "ready": False, "missing": True,
                "likely_review_topics": [], "likely_hotspot_files": [],
                "smell_warnings": [], "review_load_projection": {}}
    p = state_root / "repos" / repo / "predict.json"
    if not p.exists():
        return {"repo": repo, "ready": False, "missing": True,
                "likely_review_topics": [], "likely_hotspot_files": [],
                "smell_warnings": [], "review_load_projection": {}}
    return _load_predict_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _load_predict_cached(path_str: str, mtime: float, top_n: int = 10) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repo": data.get("repo"),
        "status": data.get("status"),
        "learner_login": data.get("learner_login"),
        "inputs": data.get("inputs", {}),
        "likely_review_topics": data.get("likely_review_topics", [])[:top_n],
        "likely_hotspot_files": data.get("likely_hotspot_files", [])[:top_n],
        "smell_warnings": data.get("smell_warnings", [])[:top_n],
        "review_load_projection": data.get("review_load_projection", {}),
        "counts": data.get("counts", {}),
    }


def _load_cross_mission(state_root: Path, top_carryover: int = 10) -> dict:
    """Learner-scoped cross-mission artifact (bin/learn-cross-mission-build).

    Cross-repo, so it ignores `repo` and reads state/learner/cross_mission.json
    (mirrors _load_anchors). The builder already caps lists; we just gate
    carryover to a prompt-friendly top-N. ``missing=True`` when unbuilt."""
    p = state_root / "learner" / "cross_mission.json"
    if not p.exists():
        return {"ready": False, "missing": True, "carryover": [],
                "recurring_across_missions": [], "difficulty": []}
    return _load_cross_mission_cached(str(p), p.stat().st_mtime, top_carryover)


@lru_cache(maxsize=2)
def _load_cross_mission_cached(path_str: str, mtime: float, top_carryover: int) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "repos_analyzed": data.get("repos_analyzed", []),
        "carryover": data.get("carryover", [])[:top_carryover],
        "recurring_across_missions": data.get("recurring_across_missions", []),
        "difficulty": data.get("difficulty", []),
    }


def _load_memory_review(state_root: Path) -> dict:
    """Learner-scoped memory-review artifact (bin/learn-memory-review-build).

    Cross-repo, so it ignores `repo` and reads state/learner/memory_review.json
    (mirrors _load_anchors). ``missing=True`` when unbuilt."""
    p = state_root / "learner" / "memory_review.json"
    if not p.exists():
        return {"ready": False, "missing": True, "blind_spots": [],
                "forgetting": [], "review_cards": []}
    return _load_memory_review_cached(str(p), p.stat().st_mtime)


@lru_cache(maxsize=2)
def _load_memory_review_cached(path_str: str, mtime: float) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return {
        "ready": True,
        "blind_spots": data.get("blind_spots", []),
        "forgetting": data.get("forgetting", []),
        "review_cards": data.get("review_cards", []),
    }


@lru_cache(maxsize=4)
def _load_pr_diff_evolution_cached(path_str: str, mtime: float, top_prs: int = 2) -> dict:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    prs = data.get("prs", [])
    prs = sorted(prs, key=lambda x: x.get("number", 0), reverse=True)[:top_prs]
    return {
        "repo": data.get("repo"),
        "clone_available": data.get("clone_available", False),
        "ready": True,
        "pr_count": len(data.get("prs", [])),
        "prs": prs,
    }


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
