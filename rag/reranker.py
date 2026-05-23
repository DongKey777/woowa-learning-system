"""bge-reranker-v2-m3 cross-encoder rerank (D5 lazy-loaded).

Used by core.context.collect_cs_qa_context to rescore top-K dense hits +
relations walk hits using (query, title+summary) pairs. Adds ~50-100ms
warm latency for top-K=8.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Callable

from rag.corpus_loader import LoadedCorpus
from rag.search import SearchHit

RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"


@lru_cache(maxsize=1)
def _get_reranker():
    """Lazy load — first call downloads (~500MB)."""
    from sentence_transformers import CrossEncoder
    from rag.encoder import _resolve_device

    return CrossEncoder(RERANKER_MODEL_ID, device=_resolve_device())


def make_rerank_fn(corpus: LoadedCorpus) -> Callable[[str, list[SearchHit]], list[SearchHit]]:
    """Return a rerank_fn closure bound to the loaded corpus."""
    def _rerank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
        if not hits:
            return hits
        model = _get_reranker()
        pairs: list[tuple[str, str]] = []
        for h in hits:
            c = corpus.concepts.get(h.concept_id)
            text = h.title if c is None else f"{c['title']} | {c.get('summary', '')}"
            pairs.append((query, text))
        scores = model.predict(pairs, show_progress_bar=False)
        rescored = [
            SearchHit(
                concept_id=h.concept_id,
                score=float(s),
                category=h.category,
                title=h.title,
                source="reranked",
            )
            for h, s in zip(hits, scores)
        ]
        rescored.sort(key=lambda h: -h.score)
        return rescored

    return _rerank
