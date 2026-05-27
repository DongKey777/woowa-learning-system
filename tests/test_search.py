"""Tests for rag.search — uses mock encoder, real Lance build on tmp_path."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from rag.corpus_loader import LoadedCorpus, load_corpus
from rag.index import EMBED_DIM, build_index
from rag.search import RELATIONS_DECAY, SearchHit, _SEARCH_RESULT_CACHE, search


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
    """Build one Lance index on the real corpus, shared by tests."""
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
        "transactional propagation synthetic dense probe", top_k=5, relations_expand=0,
        encode_fn=_det_encode(seed=3), corpus=real_corpus, index_dir=real_index,
    )
    assert all(h.source == "dense" for h in hits)


def test_search_exact_expected_query_shortcuts_encoder(real_corpus: LoadedCorpus) -> None:
    def fail_encode(_: str) -> np.ndarray:
        raise AssertionError("encoder should not be called for unique exact expected query")

    hits = search(
        "Bean이랑 DI는 뭐가 달라?",
        top_k=5,
        relations_expand=3,
        encode_fn=fail_encode,
        corpus=real_corpus,
    )

    assert hits[0].concept_id == "spring/bean-di-basics"
    assert hits[0].source == "lexical_exact"


def test_search_unique_alias_shortcuts_encoder(real_corpus: LoadedCorpus) -> None:
    def fail_encode(_: str) -> np.ndarray:
        raise AssertionError("encoder should not be called for unique exact alias")

    hits = search(
        "bean이 뭐야",
        top_k=5,
        relations_expand=3,
        encode_fn=fail_encode,
        corpus=real_corpus,
    )

    assert hits[0].concept_id == "spring/bean-di-basics"
    assert hits[0].source == "lexical_exact"


def test_search_exact_expected_query_ignores_trailing_punctuation(real_corpus: LoadedCorpus) -> None:
    def fail_encode(_: str) -> np.ndarray:
        raise AssertionError("encoder should not be called for punctuation variant")

    hits = search(
        "Spring MVC가 뭐야",
        top_k=5,
        relations_expand=3,
        encode_fn=fail_encode,
        corpus=real_corpus,
    )

    assert hits[0].concept_id == "spring/mvc-controller-basics"
    assert hits[0].source == "lexical_exact"


def test_search_ambiguous_alias_does_not_shortcut(real_corpus: LoadedCorpus) -> None:
    called = {"n": 0}

    def marker_encode(_: str) -> np.ndarray:
        called["n"] += 1
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        v[0] = 1.0
        return v

    with pytest.raises(FileNotFoundError):
        search(
            "DI",
            top_k=5,
            relations_expand=3,
            encode_fn=marker_encode,
            corpus=real_corpus,
            index_dir=Path("/tmp/woowa-missing-index"),
        )

    assert called["n"] == 1


def test_search_caches_default_encoder_results(monkeypatch, tmp_path: Path) -> None:
    concepts = {
        "spring/cache": {
            "id": "spring/cache", "title": "Cache", "category": "spring",
            "level": "beginner", "summary": "", "body_markdown": "",
            "metadata": {"schema_version": "v2"},
        },
    }
    corpus = LoadedCorpus(concepts=concepts, failures=[])
    calls = {"encode": 0, "table": 0}

    def fake_encode(_: str) -> np.ndarray:
        calls["encode"] += 1
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        v[0] = 1.0
        return v

    class FakeRows:
        def iterrows(self):
            yield 0, {
                "concept_id": "spring/cache",
                "_distance": 0.1,
                "category": "spring",
                "title": "Cache",
            }

    class FakeSearch:
        def metric(self, _name):
            return self

        def limit(self, _top_k):
            return self

        def to_pandas(self):
            calls["table"] += 1
            return FakeRows()

    class FakeTable:
        def search(self, _vec):
            return FakeSearch()

    import sys

    _SEARCH_RESULT_CACHE.clear()
    monkeypatch.setitem(sys.modules, "rag.encoder", SimpleNamespace(encode_query=fake_encode))
    monkeypatch.setattr("rag.search._cached_open_index", lambda index_dir: FakeTable())

    first = search("cache stampede", corpus=corpus, relations_expand=0, index_dir=tmp_path)
    second = search("cache stampede", corpus=corpus, relations_expand=0, index_dir=tmp_path)

    assert [h.concept_id for h in first] == ["spring/cache"]
    assert [h.concept_id for h in second] == ["spring/cache"]
    assert calls == {"encode": 1, "table": 1}


def test_search_cache_uses_stable_rerank_key(monkeypatch, tmp_path: Path) -> None:
    corpus = LoadedCorpus(concepts={
        "spring/cache": {
            "id": "spring/cache", "title": "Cache", "category": "spring",
            "level": "beginner", "summary": "", "body_markdown": "",
            "metadata": {"schema_version": "v2"},
        },
    }, failures=[])
    calls = {"encode": 0}

    def fake_encode(_: str) -> np.ndarray:
        calls["encode"] += 1
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        v[0] = 1.0
        return v

    class FakeRows:
        def iterrows(self):
            yield 0, {
                "concept_id": "spring/cache", "_distance": 0.1,
                "category": "spring", "title": "Cache",
            }

    class FakeSearch:
        def metric(self, _name):
            return self

        def limit(self, _top_k):
            return self

        def to_pandas(self):
            return FakeRows()

    class FakeTable:
        def search(self, _vec):
            return FakeSearch()

    def rerank_a(_query: str, hits: list[SearchHit]) -> list[SearchHit]:
        return hits

    def rerank_b(_query: str, hits: list[SearchHit]) -> list[SearchHit]:
        return hits

    rerank_a.search_cache_key = ("same", 1)
    rerank_b.search_cache_key = ("same", 1)

    import sys

    _SEARCH_RESULT_CACHE.clear()
    monkeypatch.setitem(sys.modules, "rag.encoder", SimpleNamespace(encode_query=fake_encode))
    monkeypatch.setattr("rag.search._cached_open_index", lambda index_dir: FakeTable())

    search("cache stampede", corpus=corpus, relations_expand=0, index_dir=tmp_path, rerank_fn=rerank_a)
    search("cache stampede", corpus=corpus, relations_expand=0, index_dir=tmp_path, rerank_fn=rerank_b)

    assert calls["encode"] == 1
