"""Tests for rag.personalization — pure function, no I/O."""
from __future__ import annotations

import pytest

from rag.personalization import (
    MASTERED_DELTA,
    UNCERTAIN_DELTA,
    _matches_family,
    adjust,
)
from rag.search import SearchHit


def _hit(cid: str, score: float = 0.8, source: str = "dense") -> SearchHit:
    return SearchHit(concept_id=cid, score=score, category=cid.split("/")[0], title=cid, source=source)


def test_no_profile_returns_unchanged() -> None:
    hits = [_hit("spring/bean", 0.9), _hit("spring/component", 0.8)]
    out = adjust(hits)
    assert out == hits


def test_mastered_head_keeps_rank_but_score_annotated() -> None:
    # W12: the dense head (rank 0) is pinned for stability; the mastered delta
    # still annotates its score, but the head is not displaced by a tail item.
    hits = [_hit("spring/bean", 0.9), _hit("spring/component", 0.85)]
    out = adjust(hits, mastered_concepts=["spring/bean"])
    bean = next(h for h in out if h.concept_id == "spring/bean")
    component = next(h for h in out if h.concept_id == "spring/component")
    assert bean.score == pytest.approx(0.9 + MASTERED_DELTA)
    assert component.score == pytest.approx(0.85)
    assert out[0].concept_id == "spring/bean"  # head pinned (W12)


def test_uncertain_head_keeps_rank_score_annotated() -> None:
    # W12: fusion order is preserved — the head stays #1 even though a tail item
    # has a higher raw score (the old code re-sorted by score and lost this).
    hits = [_hit("spring/bean", 0.7), _hit("database/index", 0.85)]
    out = adjust(hits, uncertain_concepts=["spring/bean"])
    bean = next(h for h in out if h.concept_id == "spring/bean")
    assert bean.score == pytest.approx(0.7 + UNCERTAIN_DELTA)
    assert out[0].concept_id == "spring/bean"  # head pinned, fusion order kept


def test_both_deltas_combine() -> None:
    hits = [_hit("spring/bean", 1.0)]
    out = adjust(
        hits,
        mastered_concepts=["spring/bean"],
        uncertain_concepts=["spring/bean"],
    )
    assert out[0].score == pytest.approx(1.0 + MASTERED_DELTA + UNCERTAIN_DELTA)


def test_concept_prefix_stripped() -> None:
    """`concept:spring/bean` ↔ `spring/bean` match (cycle 9.2 rule)."""
    assert _matches_family("spring/bean", "concept:spring/bean") is True
    assert _matches_family("concept:spring/bean", "spring/bean") is True


def test_family_match_by_root_segment() -> None:
    """`spring/bean-di-basics` matches `spring/bean` (shared root)."""
    assert _matches_family("spring/bean-di-basics", "spring/bean") is True
    assert _matches_family("spring/bean", "spring/bean-lifecycle-deep") is True


def test_family_match_does_not_merge_sibling_prefixes() -> None:
    """`bean-di-*` and `bean-lifecycle-*` are siblings, not one family."""
    assert _matches_family("spring/bean-di-basics", "spring/bean-lifecycle") is False


def test_family_mismatch_across_categories() -> None:
    assert _matches_family("spring/bean", "database/bean") is False


def test_family_mismatch_different_slugs() -> None:
    assert _matches_family("spring/transaction-propagation", "spring/bean") is False


def test_pure_function_no_mutation() -> None:
    """Same input → same output across repeated calls (cache-safety, D8)."""
    hits = [_hit("spring/bean", 0.9), _hit("spring/component", 0.8)]
    p = ["spring/bean"]
    u = ["spring/component"]
    out1 = adjust(hits, mastered_concepts=p, uncertain_concepts=u)
    out2 = adjust(hits, mastered_concepts=p, uncertain_concepts=u)
    assert [(h.concept_id, h.score) for h in out1] == [(h.concept_id, h.score) for h in out2]
    assert hits[0].score == 0.9  # original untouched


def test_stable_ordering_under_ties() -> None:
    hits = [_hit("a/x", 0.8), _hit("a/y", 0.8), _hit("a/z", 0.8)]
    out = adjust(hits)
    assert [h.concept_id for h in out] == ["a/x", "a/y", "a/z"]


def test_empty_profile_ids_ignored() -> None:
    """Empty / whitespace profile ids must not raise."""
    hits = [_hit("spring/bean", 0.9)]
    out = adjust(hits, mastered_concepts=["", "  "], uncertain_concepts=[None])  # type: ignore[list-item]
    assert out[0].score == 0.9


def test_uncertain_tail_item_promoted_bounded_not_to_rank_2() -> None:
    # W12: an uncertain TAIL item moves up by a bounded step, NOT an unconditional
    # jump to rank 2; the head and non-matched relative order are preserved.
    hits = [_hit("spring/head", 0.9), _hit("a/one", 0.5), _hit("a/two", 0.4),
            _hit("spring/grow", 0.3), _hit("a/four", 0.2)]
    out = [h.concept_id for h in adjust(hits, uncertain_concepts=["spring/grow"])]
    assert out[0] == "spring/head"                  # head pinned
    assert out.index("spring/grow") == 2            # 3 -> 2 (bounded), not rank 1
    assert out.index("a/one") < out.index("a/two")  # non-matched order preserved


def test_fusion_tail_order_survives_personalization() -> None:
    # W12 core contract: fusion may order tail items against their raw score (a
    # refinement). Personalization must NOT re-sort by score and lose it. Here
    # b/x(0.30) precedes b/y(0.40) in fusion order; an unrelated uncertain match
    # must keep b/x before b/y (the old global score sort would have flipped them).
    hits = [_hit("spring/head", 0.9), _hit("spring/grow", 0.5),
            _hit("b/x", 0.30), _hit("b/y", 0.40)]
    out = [h.concept_id for h in adjust(hits, uncertain_concepts=["spring/grow"])]
    assert out[0] == "spring/head"               # head pinned
    assert out.index("b/x") < out.index("b/y")   # fusion tail order preserved


def test_head_never_displaced_by_promoted_tail() -> None:
    # W12: even a promoted rank-1 item cannot rise above the pinned head.
    hits = [_hit("spring/head", 0.9), _hit("spring/grow", 0.5), _hit("a/z", 0.4)]
    out = [h.concept_id for h in adjust(hits, uncertain_concepts=["spring/grow"])]
    assert out[0] == "spring/head"
