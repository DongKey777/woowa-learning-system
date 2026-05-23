"""Tests for rag.search — uses mock encoder, real Lance build on tmp_path."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rag.corpus_loader import LoadedCorpus, load_corpus
from rag.index import EMBED_DIM, build_index
from rag.search import RELATIONS_DECAY, SearchHit, search


def _det_encode(seed: int):
    rng = np.random.default_rng(seed)
    cache: dict[str, np.ndarray] = {}

    def _encode(text: str) -> np.ndarray:
        if text not in cache:
            v = rng.standard_normal(EMBED_DIM).astype(np.float32)
            cache[text] = v / np.linalg.norm(v)
        return cache[text]

    return _encode


def _det_batch_encode(seed: int):
    rng = np.random.default_rng(seed)

    def _batch(texts: list[str]) -> np.ndarray:
        v = rng.standard_normal((len(texts), EMBED_DIM)).astype(np.float32)
        return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)

    return _batch


@pytest.fixture(scope="module")
def real_index(tmp_path_factory):
    """Build one Lance index on the real 3199-concept corpus, shared by tests."""
    index_dir = tmp_path_factory.mktemp("index_phase3")
    build_index(index_dir=index_dir, encode_fn=_det_batch_encode(seed=42))
    return index_dir


@pytest.fixture(scope="module")
def real_corpus() -> LoadedCorpus:
    return load_corpus(strict=True)


def test_search_returns_top_k_dense_hits(real_index: Path, real_corpus: LoadedCorpus) -> None:
    hits = search(
        "what is DI",
        top_k=5,
        relations_expand=0,
        encode_fn=_det_encode(seed=1),
        corpus=real_corpus,
        index_dir=real_index,
    )
    assert len(hits) == 5
    assert all(isinstance(h, SearchHit) for h in hits)
    assert all(h.source == "dense" for h in hits)
    assert all(-1.0 <= h.score <= 1.0 for h in hits)  # cosine similarity range


def test_search_expands_via_confusable_with(tmp_path: Path) -> None:
    """Build tiny corpus where seed has confusable_with → walk produces extra hits."""
    from rag.corpus_loader import LoadedCorpus

    concepts = {
        "spring/bean": {
            "id": "spring/bean", "title": "Bean", "category": "spring", "level": "beginner",
            "summary": "x", "body_markdown": "yy" * 10,
            "relations": {"confusable_with": ["spring/component"]},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "spring/component": {
            "id": "spring/component", "title": "Component", "category": "spring", "level": "beginner",
            "summary": "y", "body_markdown": "yy" * 10,
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])

    seed_hit = SearchHit(concept_id="spring/bean", score=0.9, category="spring", title="Bean", source="dense")
    from rag.search import _walk_relations

    expanded = _walk_relations([seed_hit], tiny, max_expand=5)
    assert len(expanded) == 1
    assert expanded[0].concept_id == "spring/component"
    assert expanded[0].source == "relations_walk"
    assert expanded[0].score == pytest.approx(0.9 * RELATIONS_DECAY)


def test_search_skips_dead_relation_ids(tmp_path: Path) -> None:
    """confusable_with → unknown id silently skipped, no exception."""
    concepts = {
        "spring/bean": {
            "id": "spring/bean", "title": "Bean", "category": "spring", "level": "beginner",
            "summary": "x", "body_markdown": "yy" * 10,
            "relations": {"confusable_with": ["spring/ghost", "spring/missing"]},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    seed = SearchHit("spring/bean", 0.9, "spring", "Bean", "dense")
    from rag.search import _walk_relations

    assert _walk_relations([seed], tiny, max_expand=5) == []


def test_search_applies_rerank_fn(real_index: Path, real_corpus: LoadedCorpus) -> None:
    """Pass a rerank_fn that reverses the list → output reflects override."""
    def reverse_rerank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
        return list(reversed(hits))

    hits_before = search(
        "ioc container", top_k=3, relations_expand=0,
        encode_fn=_det_encode(seed=2), corpus=real_corpus, index_dir=real_index,
    )
    hits_after = search(
        "ioc container", top_k=3, relations_expand=0,
        encode_fn=_det_encode(seed=2), corpus=real_corpus, index_dir=real_index,
        rerank_fn=reverse_rerank,
    )
    assert [h.concept_id for h in hits_after] == list(reversed([h.concept_id for h in hits_before]))


def test_search_empty_query_returns_empty() -> None:
    assert search("   ", top_k=5) == []


def test_search_no_expand_when_relations_zero(real_index: Path, real_corpus: LoadedCorpus) -> None:
    hits = search(
        "transactional propagation", top_k=5, relations_expand=0,
        encode_fn=_det_encode(seed=3), corpus=real_corpus, index_dir=real_index,
    )
    assert all(h.source == "dense" for h in hits)
