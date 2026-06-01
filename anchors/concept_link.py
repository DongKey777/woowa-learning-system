"""Link review-comment text → concept_ids (fills ReviewAnchor.topic_concept_ids).

Build-time post-processing pass over extracted anchors. Reuses the warm
rag-daemon BGE-M3 encoder when available (seconds); falls back to a cold
in-process search only when the daemon is down. No learner-facing latency —
runs inside bin/anchors-build, which is already part of sync.

Kept separate from anchors/extract.py so extraction stays ML-free (tests call
it without a corpus/daemon).
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Callable

# Calibrated 2026-06-01 against the 10 real spring-roomescape-waiting anchors:
# genuine matches cluster at ≥0.62 (403-vs-404, soft-delete, N+1), while
# semantic near-misses ("도메인 테스트"→web-domain-verification,
# "캡슐화"→auth-transaction) sit at 0.56–0.59. 0.60 sits just above the worst
# false positive (0.592) → precision over recall ("의심 시 reject"). Tune via
# WOOWA_CONCEPT_LINK_THRESHOLD.
DEFAULT_THRESHOLD = 0.60
DEFAULT_TOP_K = 5
DEFAULT_MAX_LINKS = 3

# search_fn(query, top_k) → list of {"concept_id": str, "score": float} (or None)
SearchFn = Callable[[str, int], "list[dict] | None"]


def default_threshold() -> float:
    raw = os.environ.get("WOOWA_CONCEPT_LINK_THRESHOLD")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def _auto_search_fn(state_root: Path | None) -> SearchFn:
    """Warm daemon first, cold in-process only if the daemon is unreachable."""
    from core import daemon as rag_daemon

    cold: dict = {"fn": None}

    def fn(query: str, top_k: int):
        res = rag_daemon.search(
            query, top_k=top_k, relations_expand=0, lexical_expand=0,
            **({"state_root": state_root} if state_root is not None else {}),
        )
        if res is not None:
            return res
        if cold["fn"] is None:
            from rag.search import search as _csearch

            def _cold(q: str, k: int):
                return [
                    {"concept_id": h.concept_id, "score": h.score}
                    for h in _csearch(q, top_k=k, relations_expand=0)
                ]

            cold["fn"] = _cold
        return cold["fn"](query, top_k)

    return fn


def query_text(anchor) -> str:
    """comment_text + path leaf stem. code_hunk excluded — it's mostly noise."""
    stem = Path(anchor.path).stem if getattr(anchor, "path", "") else ""
    return f"{anchor.comment_text} {stem}".strip()


def link_concepts(
    anchors: list,
    *,
    search_fn: SearchFn | None = None,
    top_k: int = DEFAULT_TOP_K,
    threshold: float | None = None,
    max_links: int = DEFAULT_MAX_LINKS,
    state_root: Path | None = None,
) -> list:
    """Return anchors with topic_concept_ids populated by semantic search.

    Each anchor is isolated in try/except so one ML failure (or a single bad
    query) keeps topic_concept_ids=[] for that anchor instead of killing the
    whole build.
    """
    if threshold is None:
        threshold = default_threshold()
    if search_fn is None:
        search_fn = _auto_search_fn(state_root)

    out = []
    for a in anchors:
        try:
            hits = search_fn(query_text(a), top_k) or []
            cids: list[str] = []
            for h in hits:
                if (h.get("score") or 0.0) < threshold:
                    continue
                cid = h.get("concept_id")
                if cid and cid not in cids:
                    cids.append(cid)
                if len(cids) >= max_links:
                    break
            out.append(dataclasses.replace(a, topic_concept_ids=cids))
        except Exception:
            out.append(a)
    return out
