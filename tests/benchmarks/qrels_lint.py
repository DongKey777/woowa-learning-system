#!/usr/bin/env python3
"""qrels_lint — anti-circularity / integrity lint for eval fixtures (eval-framework-v2 Phase 1).

A retrieval eval is only honest if its queries are *held-out* from the concept's
own indexed surfaces. The embedding text is ``title | aliases | expected_queries
| summary`` (rag/corpus_loader.encoding_text), so a query that re-uses those
tokens is graded by lexical self-match, not by generalisation — that is exactly
the y14 synth circularity. This module flags it.

Checks per (query, expected-concept):
  - lexical_jaccard : char-3gram Jaccard(query, concept surfaces). High → leakage.
  - token_ngram     : longest contiguous word n-gram shared with surfaces.
  - leaked          : jaccard >= JACCARD_MAX or token_ngram > TOKEN_NGRAM_MAX.
Fixture-level:
  - expected_in_corpus : every expected concept id resolves in concept_graph.
  - authoring_method   : provenance present (held-out authoring claim).

CLI: python tests/benchmarks/qrels_lint.py <fixture.json> [<fixture.json> ...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

JACCARD_MAX = 0.30      # >= → leaked (eval-framework-v2 §3.2)
TOKEN_NGRAM_MAX = 2     # > → leaked (3+ contiguous shared word n-gram)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _char_grams(s: str, n: int = 3) -> set[str]:
    s = re.sub(r"\s+", "", _norm(s))
    return {s[i : i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[가-힣]+", _norm(s))


def _max_shared_ngram(q_tokens: list[str], surf_tokens: set[str], surf_text: str) -> int:
    """Longest contiguous query word n-gram that appears verbatim in the surfaces."""
    best = 0
    surf_norm = _norm(surf_text)
    for i in range(len(q_tokens)):
        for j in range(i + 1, len(q_tokens) + 1):
            gram = " ".join(q_tokens[i:j])
            if len(gram) >= 4 and gram in surf_norm:
                best = max(best, j - i)
    return best


def concept_surfaces(concept: dict) -> str:
    parts = [concept.get("title") or ""]
    parts += [str(x) for x in (concept.get("aliases") or [])]
    parts += [str(x) for x in (concept.get("expected_queries") or [])]
    return " | ".join(p for p in parts if p)


def lint_query(query: str, surfaces: str) -> dict:
    qg, sg = _char_grams(query), _char_grams(surfaces)
    jac = len(qg & sg) / len(qg | sg) if (qg or sg) else 0.0
    ngram = _max_shared_ngram(_tokens(query), set(_tokens(surfaces)), surfaces)
    return {
        "lexical_jaccard": round(jac, 3),
        "token_ngram": ngram,
        "leaked": jac >= JACCARD_MAX or ngram > TOKEN_NGRAM_MAX,
    }


def _load_corpus_surfaces() -> dict[str, str]:
    out: dict[str, str] = {}
    for f in (REPO_ROOT / "corpus" / "concepts").rglob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out[d["id"]] = concept_surfaces(d)
    return out


def _expected_ids(record: dict) -> list[str]:
    out: list[str] = []
    for k in ("expected_concepts", "expected_top1_concept", "primary_paths"):
        v = record.get(k)
        if v:
            out += v if isinstance(v, list) else [v]
    return out


def lint_fixture(path: Path, surfaces: dict[str, str]) -> dict:
    blob = json.loads(path.read_text(encoding="utf-8"))
    records = blob if isinstance(blob, list) else blob.get("queries", [])
    meta = {} if isinstance(blob, list) else blob
    n = leaked = no_expected = missing = 0
    max_jac = 0.0
    worst: list[tuple] = []
    for r in records:
        q = r.get("prompt") or r.get("query") or ""
        exp = _expected_ids(r)
        if not exp:
            no_expected += 1
            continue
        # path-style expected (e.g. "category/...#anchor") → take the concept id head
        primary = exp[0].split("#")[0]
        if primary not in surfaces:
            missing += 1
            continue
        n += 1
        res = lint_query(q, surfaces[primary])
        if res["leaked"]:
            leaked += 1
            worst.append((res["lexical_jaccard"], res["token_ngram"], q[:45], primary.split("/")[-1]))
        max_jac = max(max_jac, res["lexical_jaccard"])
    worst.sort(reverse=True)
    return {
        "fixture": path.name,
        "authoring_method": meta.get("authoring_method", "UNDECLARED"),
        "n_linted": n,
        "leaked": leaked,
        "leak_rate": round(leaked / n, 3) if n else None,
        "max_jaccard": round(max_jac, 3),
        "no_expected": no_expected,
        "missing_from_corpus": missing,
        "worst": worst[:6],
    }


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (argv or sys.argv[1:])]
    if not paths:
        print("usage: qrels_lint.py <fixture.json> ...", file=sys.stderr)
        return 2
    surfaces = _load_corpus_surfaces()
    print(f"[qrels_lint] corpus surfaces: {len(surfaces)} concepts\n")
    for p in paths:
        if not p.exists():
            print(f"  {p}: NOT FOUND")
            continue
        r = lint_fixture(p, surfaces)
        flag = "⚠ LEAKY" if (r["leak_rate"] or 0) >= 0.20 else "ok"
        print(f"{flag}  {r['fixture']}  (authoring={r['authoring_method']})")
        print(f"   linted {r['n_linted']} | leaked {r['leaked']} ({r['leak_rate']}) | "
              f"max_jac {r['max_jaccard']} | no_expected {r['no_expected']} | missing {r['missing_from_corpus']}")
        for jac, ng, q, c in r["worst"]:
            print(f"     leak jac={jac} ngram={ng}  «{q}» → {c}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
