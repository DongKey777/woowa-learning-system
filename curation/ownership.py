"""Exact-shortcut ownership model — the monotonic-quality guard for corpus growth.

The exact-query shortcut (rag/search._exact_query_shortcut_hits) resolves a query
to a single concept by tier precedence (expected_queries > aliases > title); a tier
wins only when exactly ONE concept owns the normalized string there, otherwise the
ambiguity guard returns [] (dense fallback). Ownership is therefore a GLOBAL property
of the whole corpus, but nothing in author→build→release computes it — so a newly
added concept can register an expected_query/alias that an EXISTING canonical concept
already owns and silently STEAL the shortcut (verified instance: "DI가 뭐야?" now
resolves to software-engineering/dependency-injection-basics, shadowing the canonical
spring/bean-di-basics).

This module computes the "fire+shadow" set — every normalized string where the
shortcut FIRES (single winner) AND a DIFFERENT concept also carries that string at
some tier (i.e. the winner shadows a sibling). A FROZEN baseline of this set
grandfathers what exists today; the lint (bin/corpus-lint) fails the build on any NEW
fire+shadow or any winner FLIP, so a future steal is caught before BGE-M3 even encodes.

Verified design (hypothesis→measured, 2026-06-24, live corpus 3654 concepts):
  - fire+shadow = exactly 12 today; only "DI가 뭐야" currently breaks a gate.
  - Same-tier alias ambiguity (the shortcut returns [] → dense) = 1017, and a full
    dense sweep showed 97.6% benign — those are NOT fire+shadow and are NOT flagged
    (overlap with fire+shadow = 0), so the lint has 0 false positives on them.
  - A blanket "block all multi-owner norms" lint would false-positive on all 1017;
    an expected_queries-only lint would miss 7 alias→title steals; a created_at
    "newer shadows older" heuristic fails (steal pairs share a day). The fire+shadow
    set computed via the production resolver is the only criterion that is both
    complete (12/12) and FP-free (0/1017).

Reuses rag.search._exact_query_shortcut_hits as the single source of truth for the
winner so the lint sees EXACTLY what the runtime resolver sees (no reimplementation
drift). Runs over the field-only corpus (rag.corpus_loader.load_corpus, ~1.1s, no
dense index / no BGE-M3), so it is cheap enough for a pre-commit / pre-build gate.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from rag.search import _exact_query_shortcut_hits, _norm_exact

# Tier name → concept field. Order encodes the resolver's precedence (informational;
# the actual winner comes from _exact_query_shortcut_hits, not this list).
_TIERS = (("expected_queries", "expected_queries"),
          ("aliases", "aliases"),
          ("title", None))


def _owner_map(corpus) -> tuple[dict, dict]:
    """Return ({norm: {tier: set(concept_id)}}, {norm: one surface string})."""
    norm_owners: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    surface: dict[str, str] = {}
    for cid, c in corpus.concepts.items():
        for tier, field in _TIERS:
            values = [c.get("title")] if field is None else (c.get(field) or [])
            for v in values:
                if not v:
                    continue
                n = _norm_exact(v)
                if not n:
                    continue
                norm_owners[n][tier].add(cid)
                surface.setdefault(n, v)
    return norm_owners, surface


def fire_and_shadow(corpus) -> dict[str, dict[str, Any]]:
    """{norm: {"winner": cid, "shadowed": [cid, ...]}} for every normalized string
    where the exact shortcut resolves to a single winner AND ≥1 OTHER concept also
    carries that string (the winner shadows a sibling — the steal surface).

    Norms with no single winner (≥2 owners at the effective tier → ambiguity guard
    returns []) are NOT included: they fall back to dense and are overwhelmingly
    benign, so flagging them would be a false positive."""
    norm_owners, surface = _owner_map(corpus)
    out: dict[str, dict[str, Any]] = {}
    for n, tiers in norm_owners.items():
        owners = set().union(*tiers.values())
        if len(owners) < 2:
            continue  # single owner across all tiers → cannot shadow anyone
        hits = _exact_query_shortcut_hits(surface[n], corpus)
        if not hits:
            continue  # ambiguity → [] → dense fallback (benign), not a fire+shadow
        winner = hits[0].concept_id
        shadowed = sorted(owners - {winner})
        if shadowed:
            out[n] = {"winner": winner, "shadowed": shadowed}
    return out


def diff_against_baseline(current: dict, baseline: dict) -> dict[str, dict]:
    """Compare the candidate fire+shadow set against a frozen baseline.

    Returns {"new_steals": {...}, "flipped": {...}}:
      - new_steals: norms that newly fire+shadow (a new concept stole/shadowed an
        existing owner) — absent from the baseline. THESE BLOCK the build.
      - flipped: norms present in both but whose winner concept changed (ownership
        moved to a different concept) — also a regression unless intended. BLOCK.
    A baselined norm that DISAPPEARS from `current` is a FIX (allowed, not reported).
    """
    new_steals = {n: info for n, info in current.items() if n not in baseline}
    flipped = {
        n: {"baseline_winner": baseline[n]["winner"], "now_winner": info["winner"]}
        for n, info in current.items()
        if n in baseline and info["winner"] != baseline[n]["winner"]
    }
    return {"new_steals": new_steals, "flipped": flipped}
