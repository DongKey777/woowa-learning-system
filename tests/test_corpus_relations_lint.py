"""confusable_with relation integrity lint — pins the structural guard.

Verifies curation/relations.scan_relations: an injected asymmetric / dangling / self
defect on a synthetic corpus is detected, a fully symmetric corpus passes clean, and
the tool runs over the LIVE corpus and returns counts (the live corpus is NOT asserted
clean — a known ~handful of one-way confusable_with edges exist and are an external
corpus-edit task; the test only checks the tool runs + the synthetic detect logic is
exact).
"""
from __future__ import annotations

from rag.corpus_loader import LoadedCorpus, load_corpus

from curation.relations import (
    has_defects,
    scan_relations,
    summary_counts,
)


def _concept(cid: str, confusable: list[str]) -> dict:
    """Minimal concept dict carrying only the field scan_relations reads."""
    return {"id": cid, "relations": {"confusable_with": list(confusable)}}


def _corpus(*concepts: dict) -> LoadedCorpus:
    return LoadedCorpus(concepts={c["id"]: c for c in concepts}, failures=[])


def test_symmetric_pair_passes() -> None:
    """A ↔ B mutually listed → no defect (the well-formed case)."""
    corpus = _corpus(
        _concept("a/x", ["a/y"]),
        _concept("a/y", ["a/x"]),
        _concept("a/z", []),
    )
    scan = scan_relations(corpus)
    assert scan == {"asymmetric": [], "dangling": [], "self": []}
    assert not has_defects(scan)
    assert summary_counts(scan) == {"asymmetric": 0, "dangling": 0, "self": 0}


def test_detects_asymmetric() -> None:
    """A lists B but B does not list A back → one asymmetric pair [A, B]."""
    corpus = _corpus(
        _concept("a/x", ["a/y"]),  # x → y
        _concept("a/y", []),       # y has no back-reference
    )
    scan = scan_relations(corpus)
    assert scan["asymmetric"] == [["a/x", "a/y"]]
    assert scan["dangling"] == []
    assert scan["self"] == []
    assert has_defects(scan)


def test_symmetric_does_not_count_as_asymmetric() -> None:
    """A symmetric edge must never be reported in either direction."""
    corpus = _corpus(
        _concept("a/x", ["a/y"]),
        _concept("a/y", ["a/x"]),
    )
    assert scan_relations(corpus)["asymmetric"] == []


def test_detects_dangling() -> None:
    """A target id that no concept owns → dangling [cid, target]."""
    corpus = _corpus(
        _concept("a/x", ["a/ghost"]),  # a/ghost is not in the corpus
        _concept("a/y", []),
    )
    scan = scan_relations(corpus)
    assert scan["dangling"] == [["a/x", "a/ghost"]]
    # a dangling target is not also reported as asymmetric.
    assert scan["asymmetric"] == []
    assert scan["self"] == []
    assert has_defects(scan)


def test_detects_self() -> None:
    """A concept listing its own id → self, and not asymmetric/dangling."""
    corpus = _corpus(
        _concept("a/x", ["a/x"]),
    )
    scan = scan_relations(corpus)
    assert scan["self"] == ["a/x"]
    assert scan["asymmetric"] == []
    assert scan["dangling"] == []
    assert has_defects(scan)


def test_detects_all_three_together() -> None:
    """asymmetric + dangling + self in one corpus are reported independently."""
    corpus = _corpus(
        _concept("a/x", ["a/y", "a/ghost", "a/x"]),  # asym(→y), dangling(ghost), self
        _concept("a/y", []),                          # no back-reference to x
    )
    scan = scan_relations(corpus)
    assert scan["asymmetric"] == [["a/x", "a/y"]]
    assert scan["dangling"] == [["a/x", "a/ghost"]]
    assert scan["self"] == ["a/x"]
    assert summary_counts(scan) == {"asymmetric": 1, "dangling": 1, "self": 1}


def test_missing_relations_field_is_safe() -> None:
    """Concepts with no relations / no confusable_with contribute no edges."""
    corpus = _corpus(
        {"id": "a/x"},                        # no relations key
        {"id": "a/y", "relations": {}},       # relations but no confusable_with
        {"id": "a/z", "relations": {"confusable_with": []}},
    )
    assert scan_relations(corpus) == {"asymmetric": [], "dangling": [], "self": []}


def test_live_corpus_runs_and_returns_counts() -> None:
    """The tool runs over the live corpus (3654 concepts) and returns integer counts.

    The live corpus is NOT asserted clean: known one-way confusable_with edges exist
    (asymmetric may be > 0) and reconciling them is an external corpus-edit task. This
    only pins that scan_relations executes index-free and returns a well-formed result.
    """
    corpus = load_corpus()
    assert len(corpus.concepts) > 1000
    scan = scan_relations(corpus)
    assert set(scan) == {"asymmetric", "dangling", "self"}
    counts = summary_counts(scan)
    for category in ("asymmetric", "dangling", "self"):
        assert isinstance(counts[category], int)
        assert counts[category] >= 0
    # Every reported pair/id references real concept ids on the carrying side.
    ids = set(corpus.concepts)
    for a, b in scan["asymmetric"]:
        assert a in ids and b in ids
    for cid, _target in scan["dangling"]:
        assert cid in ids
    for cid in scan["self"]:
        assert cid in ids
