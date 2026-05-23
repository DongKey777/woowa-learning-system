"""Dense concept search + confusable_with graph walk + optional rerank.

Hypothesis (Phase 3): dense top-K=5 + confusable_with expand=5 + lazy rerank
fits ≤150 LOC and supports D3/D4 (no chunk + dense only) without losing
confusable_pairs coverage (relations walk recovers the disambiguation signal).

D5 intent-conditional rerank: callers pass rerank_fn or None. Phase 6
orchestrator decides per intent — search.py stays unopinionated.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from rag.corpus_loader import DEFAULT_CORPUS_DIR, LoadedCorpus, load_corpus
from rag.index import DEFAULT_INDEX_DIR, open_index

RELATIONS_DECAY = 0.7
DEFAULT_TOP_K = 5
DEFAULT_RELATIONS_EXPAND = 5


@dataclass(frozen=True)
class SearchHit:
    concept_id: str
    score: float
    category: str
    title: str
    source: str  # "dense" | "relations_walk" | "reranked"


def search(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    relations_expand: int = DEFAULT_RELATIONS_EXPAND,
    rerank_fn: Callable[[str, list[SearchHit]], list[SearchHit]] | None = None,
    encode_fn: Callable[[str], np.ndarray] | None = None,
    corpus: LoadedCorpus | None = None,
    index_dir: Path = DEFAULT_INDEX_DIR,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
) -> list[SearchHit]:
    """Three-stage retrieval: dense → relations walk → optional rerank."""
    if not query.strip():
        return []

    if encode_fn is None:
        from rag.encoder import encode_query  # lazy ML import

        encode_fn = encode_query
    q_vec = np.asarray(encode_fn(query), dtype=np.float32)

    table = open_index(index_dir=index_dir)
    raw = table.search(q_vec).metric("cosine").limit(top_k).to_pandas()

    dense_hits: list[SearchHit] = []
    for _, row in raw.iterrows():
        dense_hits.append(
            SearchHit(
                concept_id=row["concept_id"],
                score=float(1.0 - row["_distance"]),  # cosine→similarity
                category=row["category"],
                title=row["title"],
                source="dense",
            )
        )

    if relations_expand > 0 and dense_hits:
        if corpus is None:
            corpus = load_corpus(corpus_dir=corpus_dir, strict=True)
        dense_hits.extend(_walk_relations(dense_hits, corpus, relations_expand))

    if rerank_fn is not None and dense_hits:
        dense_hits = rerank_fn(query, dense_hits)

    return dense_hits


def _walk_relations(
    seed_hits: list[SearchHit],
    corpus: LoadedCorpus,
    max_expand: int,
) -> list[SearchHit]:
    """Expand seed hits via confusable_with relations (1 hop, score-decayed)."""
    seen = {h.concept_id for h in seed_hits}
    expanded: list[SearchHit] = []
    for hit in seed_hits:
        if len(expanded) >= max_expand:
            break
        seed = corpus.concepts.get(hit.concept_id)
        if seed is None:
            continue
        related_ids = seed.get("relations", {}).get("confusable_with", [])
        for rid in related_ids:
            if rid in seen:
                continue
            related = corpus.concepts.get(rid)
            if related is None:
                continue
            seen.add(rid)
            expanded.append(
                SearchHit(
                    concept_id=rid,
                    score=hit.score * RELATIONS_DECAY,
                    category=related["category"],
                    title=related["title"],
                    source="relations_walk",
                )
            )
            if len(expanded) >= max_expand:
                break
    return expanded
