"""Dense concept search + exact shortcut + relation expansion + optional rerank."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rag.corpus_loader import (
    DEFAULT_CORPUS_DIR,
    LoadedCorpus,
    load_corpus,
    load_corpus_runtime,
)
from rag.index import DEFAULT_INDEX_DIR, open_index

if TYPE_CHECKING:
    import numpy as np

RELATIONS_DECAY = 0.7
DEFAULT_TOP_K = 5
DEFAULT_RELATIONS_EXPAND = 5
EXACT_SHORTCUT_SCORE = 1.0

# Module-level Lance table cache (daemon side hot path; 0.6ms/call → 0)
_TABLE_CACHE: dict[str, object] = {}
_EXACT_SHORTCUT_CACHE: dict[int, tuple[LoadedCorpus, dict[str, dict[str, list[SearchHit]]]]] = {}
_SEARCH_RESULT_CACHE: dict[tuple, tuple["SearchHit", ...]] = {}
_SEARCH_CACHE_MAX = 512


def _cached_open_index(index_dir: Path = DEFAULT_INDEX_DIR):
    key = str(index_dir)
    if key not in _TABLE_CACHE:
        _TABLE_CACHE[key] = open_index(index_dir=index_dir)
    return _TABLE_CACHE[key]


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
    if not query.strip():
        return []

    loaded_corpus = corpus
    exact_hits: list[SearchHit] = []
    if loaded_corpus is not None:
        exact_hits = _exact_query_shortcut_hits(query, loaded_corpus)
    elif relations_expand > 0:
        loaded_corpus = _load_search_corpus(corpus_dir)
        exact_hits = _exact_query_shortcut_hits(query, loaded_corpus)

    if exact_hits:
        shortcut_hits = list(exact_hits)
        if relations_expand > 0:
            assert loaded_corpus is not None
            shortcut_hits.extend(_walk_relations(shortcut_hits, loaded_corpus, relations_expand))
        return shortcut_hits

    cache_key = None
    if encode_fn is None:
        cache_key = (
            id(loaded_corpus) if loaded_corpus is not None else 0,
            str(index_dir),
            str(corpus_dir),
            query,
            top_k,
            relations_expand,
            getattr(rerank_fn, "search_cache_key", id(rerank_fn)) if rerank_fn is not None else 0,
        )
        cached = _SEARCH_RESULT_CACHE.get(cache_key)
        if cached is not None:
            return list(cached)

    if encode_fn is None:
        from rag.encoder import encode_query  # lazy ML import

        encode_fn = encode_query
    import numpy as np

    q_vec = np.asarray(encode_fn(query), dtype=np.float32)

    table = _cached_open_index(index_dir)
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
        if loaded_corpus is None:
            loaded_corpus = _load_search_corpus(corpus_dir)
        dense_hits.extend(_walk_relations(dense_hits, loaded_corpus, relations_expand))

    if rerank_fn is not None and dense_hits:
        dense_hits = rerank_fn(query, dense_hits)

    if cache_key is not None:
        if len(_SEARCH_RESULT_CACHE) >= _SEARCH_CACHE_MAX:
            _SEARCH_RESULT_CACHE.pop(next(iter(_SEARCH_RESULT_CACHE)))
        _SEARCH_RESULT_CACHE[cache_key] = tuple(dense_hits)

    return dense_hits


def _load_search_corpus(corpus_dir: Path) -> LoadedCorpus:
    if corpus_dir == DEFAULT_CORPUS_DIR:
        return load_corpus_runtime()
    return load_corpus(corpus_dir=corpus_dir, strict=True)


def _norm_exact(text: str) -> str:
    text = re.sub(r"^[\s\"'`“”‘’]+|[\s\"'`“”‘’?.!,;:。？！]+$", "", text or "")
    return " ".join(text.lower().split())


def _exact_query_shortcut_hits(query: str, corpus: LoadedCorpus) -> list[SearchHit]:
    query_norm = _norm_exact(query)
    if not query_norm:
        return []
    field_groups = (
        ("expected_queries", EXACT_SHORTCUT_SCORE),
        ("aliases", EXACT_SHORTCUT_SCORE - 0.02),
        ("title", EXACT_SHORTCUT_SCORE - 0.04),
    )
    lookup = _exact_shortcut_lookup(corpus)
    for field, _score in field_groups:
        matches = lookup[field].get(query_norm, [])
        if len({hit.concept_id for hit in matches}) == 1:
            return matches[:1]
        if matches:
            return []
    return []


def _exact_shortcut_lookup(corpus: LoadedCorpus) -> dict[str, dict[str, list[SearchHit]]]:
    corpus_key = id(corpus)
    cached = _EXACT_SHORTCUT_CACHE.get(corpus_key)
    if cached is not None:
        cached_corpus, cached_lookup = cached
        if cached_corpus is corpus:
            return cached_lookup
        _EXACT_SHORTCUT_CACHE.pop(corpus_key, None)
    lookup: dict[str, dict[str, list[SearchHit]]] = {
        "expected_queries": {},
        "aliases": {},
        "title": {},
    }
    for cid, concept in corpus.concepts.items():
        _add_exact_values(
            lookup["expected_queries"],
            concept,
            [str(v) for v in (concept.get("expected_queries") or [])],
            EXACT_SHORTCUT_SCORE,
        )
        _add_exact_values(
            lookup["aliases"],
            concept,
            [str(v) for v in (concept.get("aliases") or [])],
            EXACT_SHORTCUT_SCORE - 0.02,
        )
        _add_exact_values(
            lookup["title"],
            concept,
            [str(concept.get("title") or "")],
            EXACT_SHORTCUT_SCORE - 0.04,
        )
    _EXACT_SHORTCUT_CACHE[corpus_key] = (corpus, lookup)
    return lookup


def _add_exact_values(
    target: dict[str, list[SearchHit]],
    concept: dict,
    values: list[str],
    score: float,
) -> None:
    cid = str(concept.get("id") or "")
    if not cid:
        return
    hit = SearchHit(
        concept_id=cid,
        score=score,
        category=str(concept.get("category") or ""),
        title=str(concept.get("title") or cid),
        source="lexical_exact",
    )
    for value in values:
        norm = _norm_exact(value)
        if not norm:
            continue
        bucket = target.setdefault(norm, [])
        if not any(existing.concept_id == cid for existing in bucket):
            bucket.append(hit)


def _walk_relations(
    seed_hits: list[SearchHit],
    corpus: LoadedCorpus,
    max_expand: int,
) -> list[SearchHit]:
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
