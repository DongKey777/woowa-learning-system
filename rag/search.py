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
from rag.index import DEFAULT_INDEX_DIR, TABLE_NAME, open_index

if TYPE_CHECKING:
    import numpy as np

RELATIONS_DECAY = 0.7
DEFAULT_TOP_K = 5
DEFAULT_RELATIONS_EXPAND = 5
EXACT_SHORTCUT_SCORE = 1.0
# Chunk over-fetch (Pillar 1): fetch top_k*N chunk rows, then max-pool to
# concepts. N covers the worst case where one concept owns many of the top
# chunks. No-op for a single-vector index (one row per concept).
CHUNK_OVERFETCH = 12

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

    if (
        encode_fn is None
        and index_dir == DEFAULT_INDEX_DIR
        and not (index_dir / f"{TABLE_NAME}.lance").exists()
    ):
        if loaded_corpus is None:
            loaded_corpus = _load_search_corpus(corpus_dir)
        lexical_hits = _lexical_fallback_hits(query, loaded_corpus, top_k)
        if lexical_hits:
            if relations_expand > 0:
                lexical_hits.extend(_walk_relations(lexical_hits, loaded_corpus, relations_expand))
            return lexical_hits

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
    raw = table.search(q_vec).metric("cosine").limit(top_k * CHUNK_OVERFETCH).to_pandas()

    # Max-pool chunk rows to concepts: a concept's dense score is its best
    # chunk. Single-vector indexes have one row per concept, so this reduces to
    # the previous top_k ordering exactly.
    best: dict[str, SearchHit] = {}
    for _, row in raw.iterrows():
        cid = row["concept_id"]
        score = float(1.0 - row["_distance"])  # cosine→similarity
        existing = best.get(cid)
        if existing is None or score > existing.score:
            best[cid] = SearchHit(
                concept_id=cid,
                score=score,
                category=row["category"],
                title=row["title"],
                source="dense",
            )
    dense_hits: list[SearchHit] = sorted(
        best.values(), key=lambda h: h.score, reverse=True
    )[:top_k]

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


def _lexical_fallback_hits(
    query: str,
    corpus: LoadedCorpus,
    top_k: int,
) -> list[SearchHit]:
    """Small no-index fallback for tests and first-run recovery.

    Dense search remains the normal path. This only runs when the Lance table
    is absent, so `bin/ask --no-daemon` can still cite obvious corpus matches
    before `bin/index-fetch` has completed.
    """
    query_norm = _norm_exact(query)
    tokens = _lexical_tokens(query_norm)
    if not tokens:
        return []
    scored: list[tuple[float, str, dict]] = []
    for cid, concept in corpus.concepts.items():
        title = str(concept.get("title") or "")
        aliases = " ".join(str(v) for v in (concept.get("aliases") or []))
        expected = " ".join(str(v) for v in (concept.get("expected_queries") or []))
        summary = str(concept.get("summary") or "")
        fields = (
            (title, 3.0),
            (aliases, 2.5),
            (expected, 2.0),
            (summary, 0.8),
        )
        score = 0.0
        for text, weight in fields:
            text_norm = _norm_exact(text)
            if not text_norm:
                continue
            if query_norm and query_norm in text_norm:
                score += weight * 2.0
            field_tokens = set(_lexical_tokens(text_norm))
            if field_tokens:
                score += weight * len(tokens & field_tokens) / len(tokens)
        if score > 0:
            scored.append((score, cid, concept))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return []
    top_score = scored[0][0]
    hits: list[SearchHit] = []
    for raw_score, cid, concept in scored[:top_k]:
        hits.append(
            SearchHit(
                concept_id=cid,
                score=round(min(0.89, 0.55 + (raw_score / max(top_score, 1e-9)) * 0.34), 4),
                category=str(concept.get("category") or ""),
                title=str(concept.get("title") or cid),
                source="lexical_fallback",
            )
        )
    return hits


def _lexical_tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"[0-9a-zA-Z가-힣@+#.:-]+", text.lower())
    stop = {"어떻게", "뭐야", "무엇", "설명", "알려줘", "차이", "정의", "처음"}
    return {t for t in raw_tokens if len(t) >= 2 and t not in stop}


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
