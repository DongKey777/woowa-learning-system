"""Exact-shortcut ownership lint — pins the monotonic-quality guard.

Verifies curation/ownership.fire_and_shadow + diff_against_baseline: the live corpus
does not exceed its frozen baseline (no un-baselined steal), a synthetically injected
steal is caught, and benign same-tier alias ambiguities are NOT flagged (0 false positives).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from curation.ownership import (
    _owner_map,
    diff_against_baseline,
    fire_and_shadow,
)
from rag.corpus_loader import load_corpus
from rag.search import _exact_query_shortcut_hits, _norm_exact

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tests" / "fixtures" / "exact_owner_baseline.json"


def _baseline_norms() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))["norms"]


def test_live_corpus_has_no_unbaselined_steal() -> None:
    """The committed corpus must not introduce a fire+shadow steal absent from the
    baseline, nor flip a baselined owner — this is the gate bin/corpus-build runs."""
    corpus = load_corpus()
    diff = diff_against_baseline(fire_and_shadow(corpus), _baseline_norms())
    assert diff["new_steals"] == {}, f"un-baselined steals: {list(diff['new_steals'])}"
    assert diff["flipped"] == {}, f"owner flips: {diff['flipped']}"


def test_baseline_is_non_empty_and_covers_current() -> None:
    """Baseline grandfathers known fire+shadow rows. Rows that disappear are repairs,
    so current may be a strict subset of baseline but must not grow outside it."""
    corpus = load_corpus()
    current = fire_and_shadow(corpus)
    baseline = _baseline_norms()
    assert baseline, "baseline must not be empty"
    assert set(current) <= set(baseline), "current fire+shadow escaped the baseline"


def test_detects_injected_steal() -> None:
    """A new concept that shadows an existing canonical owner must be flagged — the
    future-regression case (the 'DI가 뭐야?' class) the lint exists to prevent."""
    corpus = load_corpus()
    baseline = _baseline_norms()
    # Take a concept's unique expected_query and plant it as an alias on an unrelated
    # concept whose own field outranks it → a new fire+shadow appears.
    victim = corpus.concepts["spring/bean-di-basics"]
    unique_eq = (victim.get("expected_queries") or ["스프링 빈 등록 왜"])[0]
    thief = "network/http-caching-conditional-request-basics"
    corpus.concepts[thief] = copy.deepcopy(corpus.concepts[thief])
    corpus.concepts[thief].setdefault("aliases", []).append(unique_eq)

    diff = diff_against_baseline(fire_and_shadow(corpus), baseline)
    assert _norm_exact(unique_eq) in diff["new_steals"], "injected steal not caught"


def test_no_false_positive_on_benign_alias_ambiguity() -> None:
    """Same-tier alias collisions (≥2 owners → shortcut returns [] → dense fallback)
    are benign (measured 97.6%) and must NOT appear in fire+shadow (0 overlap)."""
    corpus = load_corpus()
    norm_owners, surface = _owner_map(corpus)
    benign = set()
    for n, tiers in norm_owners.items():
        owners = set().union(*tiers.values())
        if len(owners) >= 2 and not _exact_query_shortcut_hits(surface[n], corpus):
            benign.add(n)  # ambiguity → [] → dense fallback
    fs = set(fire_and_shadow(corpus))
    assert benign, "expected some benign alias ambiguities in the corpus"
    assert fs.isdisjoint(benign), "fire+shadow overlaps benign ambiguity (false positive)"
