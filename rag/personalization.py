"""History-driven score adjustment after retrieval (D8 mechanical separation).

Hypothesis (Phase 4): personalization is a *pure function* of
(hits, mastered_concepts, uncertain_concepts). Same retrieval input → same
adjusted output. Cache key never needs to include personalization, because
the underlying retrieval is identical.

Score deltas reuse cycle 9.2 production values (Adaptive Response, AGENTS.md):
- mastered concept matched → -0.15 (demote — learner already knows)
- uncertain concept matched → +0.10 (boost — learner's growth edge)

Concept family matching (cycle 9.2 corpus mapping rules):
- `concept:spring/bean` → strip `concept:` prefix
- `spring/bean-di-basics` matches `spring/bean` by full hyphen-prefix overlap.
  Sibling families such as `spring/bean-di-*` and `spring/bean-lifecycle-*`
  do not match each other.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from rag.search import SearchHit

MASTERED_DELTA = -0.15
UNCERTAIN_DELTA = 0.10
CONCEPT_PREFIX = "concept:"


def _normalize(cid: str) -> str:
    cid = cid.strip()
    if cid.startswith(CONCEPT_PREFIX):
        cid = cid[len(CONCEPT_PREFIX) :]
    return cid


def _split(cid: str) -> tuple[str, str]:
    """Return (category, slug). Slug may be empty for malformed ids."""
    if "/" not in cid:
        return cid, ""
    cat, _, slug = cid.partition("/")
    return cat, slug


def _matches_family(hit_id: str, profile_id: str) -> bool:
    """True if hit and profile share concept family.

    Same category + exact slug or full hyphen-prefix overlap.
    Example: `spring/bean` matches `spring/bean-di-basics`,
    but `spring/bean-di-basics` does not match `spring/bean-lifecycle`.
    """
    hit_id = _normalize(hit_id)
    profile_id = _normalize(profile_id)
    if hit_id == profile_id:
        return True
    hcat, hslug = _split(hit_id)
    pcat, pslug = _split(profile_id)
    if hcat != pcat:
        return False
    if not hslug or not pslug:
        return False
    return hslug.startswith(f"{pslug}-") or pslug.startswith(f"{hslug}-")


def adjust(
    hits: list[SearchHit],
    *,
    mastered_concepts: Iterable[str] = (),
    uncertain_concepts: Iterable[str] = (),
) -> list[SearchHit]:
    """Return hits with scores adjusted by mastered/uncertain matches.

    Stable ordering: ties broken by original index (Python's sorted is stable).
    Mutation-free: each SearchHit replaced via dataclasses.replace.
    """
    mastered = [m for m in mastered_concepts if m]
    uncertain = [u for u in uncertain_concepts if u]
    if not hits or (not mastered and not uncertain):
        return list(hits)

    adjusted: list[SearchHit] = []
    for hit in hits:
        delta = 0.0
        if any(_matches_family(hit.concept_id, m) for m in mastered):
            delta += MASTERED_DELTA
        if any(_matches_family(hit.concept_id, u) for u in uncertain):
            delta += UNCERTAIN_DELTA
        adjusted.append(replace(hit, score=hit.score + delta))
    adjusted.sort(key=lambda h: -h.score)
    return adjusted
