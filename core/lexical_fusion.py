"""Cheap lexical candidate fusion for daemon RAG search.

This stays outside rag.search so dense retrieval remains small and generic.
The daemon injects this as a rerank_fn when it wants lexical recall.
"""
from __future__ import annotations

import re
from typing import Callable

from rag.corpus_loader import LoadedCorpus
from rag.search import SearchHit

RRF_K = 60
_TOKEN_RE = re.compile(r"@[a-z0-9_]+|[a-z0-9][a-z0-9_.-]*|[가-힣]{2,}", re.IGNORECASE)
_STOPWORDS = {
    "what", "why", "how", "when", "where", "vs", "the", "and", "or",
    "이게", "이건", "그게", "그건", "뭐야", "무엇", "차이", "정리", "설명",
    "알려줘", "왜", "언제", "어디", "어떻게", "하는", "되는", "기초",
}
_LEXICAL_CACHE: dict[int, tuple[dict, ...]] = {}


def make_lexical_fusion_fn(
    corpus: LoadedCorpus,
    expand: int = 8,
) -> Callable[[str, list[SearchHit]], list[SearchHit]]:
    """Return a rerank_fn that preserves dense top-1 and enriches top-5."""
    def _rerank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
        if expand <= 0 or not hits:
            return hits
        return _fuse_lexical(hits, _lexical_candidates(query, corpus, expand))

    return _rerank


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text or "")
        if token.lower() not in _STOPWORDS
    }


def _lexical_entries(corpus_key: int, corpus: LoadedCorpus) -> tuple[dict, ...]:
    cached = _LEXICAL_CACHE.get(corpus_key)
    if cached is not None:
        return cached
    entries: list[dict] = []
    for cid, concept in corpus.concepts.items():
        title = concept.get("title", "")
        strong_text = " ".join(
            str(x)
            for key_name in ("aliases", "expected_queries")
            for x in (concept.get(key_name) or [])
        )
        body_text = " ".join([
            str(concept.get("summary") or ""),
            str(concept.get("body_markdown") or ""),
        ])
        entries.append({
            "concept_id": cid,
            "category": concept.get("category", ""),
            "title": title,
            "title_tokens": _tokens(title),
            "strong_tokens": _tokens(strong_text),
            "body_tokens": _tokens(body_text),
        })
    built = tuple(entries)
    _LEXICAL_CACHE[corpus_key] = built
    return built


def _lexical_candidates(query: str, corpus: LoadedCorpus, limit: int) -> list[SearchHit]:
    query_tokens = _tokens(query)
    if not query_tokens or limit <= 0:
        return []
    scored: list[tuple[float, dict]] = []
    for entry in _lexical_entries(id(corpus), corpus):
        title_hits = query_tokens & entry["title_tokens"]
        strong_hits = query_tokens & entry["strong_tokens"]
        body_hits = query_tokens & entry["body_tokens"]
        score = len(title_hits) * 4.0 + len(strong_hits) * 2.5 + len(body_hits) * 0.75
        if score > 0:
            scored.append((score, entry))
    if not scored:
        return []
    scored.sort(key=lambda item: -item[0])
    best = scored[0][0]
    return [
        SearchHit(
            concept_id=entry["concept_id"],
            score=score / best,
            category=entry["category"],
            title=entry["title"],
            source="lexical",
        )
        for score, entry in scored[:limit]
    ]


def _fuse_lexical(hits: list[SearchHit], lexical_hits: list[SearchHit]) -> list[SearchHit]:
    if not lexical_hits:
        return hits
    head = hits[0] if hits else None
    by_id: dict[str, SearchHit] = {h.concept_id: h for h in hits}
    dense_rank = {h.concept_id: idx for idx, h in enumerate(hits, start=1)}
    lexical_rank = {h.concept_id: idx for idx, h in enumerate(lexical_hits, start=1)}
    for hit in lexical_hits:
        by_id.setdefault(hit.concept_id, hit)

    fused: list[SearchHit] = []
    for cid, hit in by_id.items():
        if head is not None and cid == head.concept_id:
            continue
        dense_part = 1.0 / (RRF_K + dense_rank[cid]) if cid in dense_rank else 0.0
        lexical_part = 1.0 / (RRF_K + lexical_rank[cid]) if cid in lexical_rank else 0.0
        score = dense_part * 0.65 + lexical_part * 0.35
        if cid in dense_rank and cid in lexical_rank:
            source = "fusion"
        elif cid in lexical_rank:
            source = "lexical"
        else:
            source = hit.source
        fused.append(SearchHit(
            concept_id=cid,
            score=score,
            category=hit.category,
            title=hit.title,
            source=source,
        ))
    fused.sort(key=lambda h: -h.score)
    return ([head] if head is not None else []) + fused
