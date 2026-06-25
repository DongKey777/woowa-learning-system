"""confusable_with relation integrity — a structural guard for corpus growth.

`relations.confusable_with` is the per-concept list of sibling concept ids that a
query can plausibly confuse with this one (the rerank/disambiguation surface). It is
meant to be a SYMMETRIC, INTRA-corpus graph: if A says it is confusable with B then a
query mistaken between them is mistaken in both directions, so B should also list A.
Nothing in author→build computes this property, so a hand-edited concept can drift
into three structural defects that silently degrade disambiguation:

  - asymmetric: A lists B in confusable_with but B does NOT list A. The pair is only
    half-wired, so whichever side is missing never surfaces its sibling on a rerank.
  - dangling: a confusable_with target id that no concept in the corpus owns (typo,
    rename, or deleted concept) — a reference into the void.
  - self: a concept lists its OWN id, which is meaningless (a concept is never a
    sibling of itself) and pollutes any "siblings of X" set.

This module scans the field-only corpus (rag.corpus_loader.load_corpus, ~1s, no dense
index / no BGE-M3) and reports all three, so a future edit's broken relation is caught
at the cheapest point — before BGE-M3 ever encodes. It mirrors curation/ownership.py:
load_corpus is the single source of truth, the scan is pure over the loaded concepts,
and bin/corpus-relations-lint is the thin CLI gate on top.

Note on scope: a known ~handful of confusable_with edges are intentionally one-way in
today's corpus (the asymmetric count is therefore expected to be > 0 on the live
corpus until those are reconciled). The lint REPORTS the full asymmetric/dangling/self
set; reconciling the known one-way edges is a separate corpus-edit task. dangling and
self are always defects.
"""
from __future__ import annotations

from typing import Any


def _confusable_targets(concept: dict) -> list[str]:
    """The confusable_with id list for one concept (empty if absent/malformed)."""
    relations = concept.get("relations") or {}
    targets = relations.get("confusable_with") or []
    return [t for t in targets if isinstance(t, str) and t]


def scan_relations(corpus) -> dict[str, list]:
    """Scan every concept's relations.confusable_with for structural defects.

    Returns {"asymmetric": [[A, B], ...], "dangling": [[cid, target], ...],
             "self": [cid, ...]}:
      - asymmetric: A lists B but B does NOT list A back (both target ids exist in
        the corpus). Reported once as the ordered pair [A, B] where A is the side
        that carries the edge and B is the side missing the back-reference. Sorted.
      - dangling: cid lists a target id that no concept in the corpus owns. [cid,
        target] pairs, sorted.
      - self: cid lists its own id in confusable_with. Sorted unique cids.

    load_corpus is the single source of truth (same as curation/ownership), so the
    scan sees exactly the concepts the runtime sees — no reimplementation drift.
    """
    ids = set(corpus.concepts)
    edges: dict[str, set[str]] = {}
    for cid, concept in corpus.concepts.items():
        edges[cid] = set(_confusable_targets(concept))

    dangling: list[list[str]] = []
    self_refs: set[str] = set()
    asymmetric: set[tuple[str, str]] = set()

    for cid, targets in edges.items():
        for target in sorted(targets):
            if target == cid:
                self_refs.add(cid)
                continue
            if target not in ids:
                dangling.append([cid, target])
                continue
            # target exists and is not self → check for the back-reference.
            if cid not in edges.get(target, set()):
                asymmetric.add((cid, target))

    return {
        "asymmetric": [list(pair) for pair in sorted(asymmetric)],
        "dangling": sorted(dangling),
        "self": sorted(self_refs),
    }


def has_defects(scan: dict[str, list]) -> bool:
    """True if the scan found any asymmetric, dangling, or self defect."""
    return bool(scan["asymmetric"] or scan["dangling"] or scan["self"])


def summary_counts(scan: dict[str, list]) -> dict[str, int]:
    """Per-category defect counts (for human + --json output)."""
    return {k: len(v) for k, v in scan.items()}


def _scan_for_cli(corpus) -> dict[str, Any]:
    """scan_relations plus its counts, the payload bin/corpus-relations-lint emits."""
    scan = scan_relations(corpus)
    return {**scan, "counts": summary_counts(scan)}
