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
# W12: personalization reorders by bounded RANK MOVE (not a global score re-sort)
# so fusion's score-blind positional refinements survive. A matched hit's target
# rank shifts by this offset; among non-matched neighbors the effective move is a
# single adjacent swap (mastered = down, uncertain = up) — the full offset only
# applies across consecutive matched items.
RANK_STEP = 2
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
    """Return hits reordered by mastered/uncertain matches.

    W12: the score delta is kept as a telemetry/payload annotation, but ordering
    is by BOUNDED RANK MOVE rather than a global sort-by-score. This preserves
    fusion's score-blind positional refinements (``_refine_confusable_order``,
    ``_promote_canonical_candidate``) — which a global re-sort discarded — and
    avoids the old "uncertain hit jumps unconditionally to rank 2" behavior. The
    dense head (rank 0) is pinned so a strong top-1 is never displaced by an
    RRF-noise tail item. Non-matched items keep their relative fusion order.

    Pure function, mutation-free: each SearchHit replaced via dataclasses.replace.
    """
    mastered = [m for m in mastered_concepts if m]
    uncertain = [u for u in uncertain_concepts if u]
    if not hits or (not mastered and not uncertain):
        return list(hits)

    annotated: list[SearchHit] = []
    targets: list[int] = []
    for i, hit in enumerate(hits):
        delta = 0.0
        move = 0
        if any(_matches_family(hit.concept_id, m) for m in mastered):
            delta += MASTERED_DELTA
            move += RANK_STEP   # demote: learner already knows this
        if any(_matches_family(hit.concept_id, u) for u in uncertain):
            delta += UNCERTAIN_DELTA
            move -= RANK_STEP   # promote: learner's growth edge
        annotated.append(replace(hit, score=hit.score + delta))
        # Head (rank 0) is pinned; tail items move by a bounded step but can
        # never rise above the head (clamp to >= 1).
        targets.append(-1 if i == 0 else max(1, i + move))
    # Stable sort by target rank; ties keep fusion order (original index).
    order = sorted(range(len(hits)), key=lambda i: (targets[i], i))
    return [annotated[i] for i in order]
