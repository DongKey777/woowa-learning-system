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


def test_mastered_concept_demotes_score() -> None:
    hits = [_hit("spring/bean", 0.9), _hit("spring/component", 0.85)]
    out = adjust(hits, mastered_concepts=["spring/bean"])
    bean = next(h for h in out if h.concept_id == "spring/bean")
    component = next(h for h in out if h.concept_id == "spring/component")
    assert bean.score == pytest.approx(0.9 + MASTERED_DELTA)
    assert component.score == pytest.approx(0.85)
    assert out[0].concept_id == "spring/component"  # reordering


def test_uncertain_concept_boosts_score() -> None:
    hits = [_hit("spring/bean", 0.7), _hit("database/index", 0.85)]
    out = adjust(hits, uncertain_concepts=["spring/bean"])
    bean = next(h for h in out if h.concept_id == "spring/bean")
    assert bean.score == pytest.approx(0.7 + UNCERTAIN_DELTA)
    assert out[0].concept_id == "database/index"  # 0.85 still highest


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
