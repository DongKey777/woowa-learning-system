"""Tests for rag.corpus_loader — pure I/O + jsonschema, no ML deps."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.corpus_loader import (
    DEFAULT_CORPUS_DIR,
    DEFAULT_SCHEMA_PATH,
    encoding_text,
    load_corpus,
    load_corpus_snapshot,
    write_corpus_snapshot,
)


def test_load_real_corpus_strict() -> None:
    """Production corpus must load strict + every concept file + 0 failures."""
    loaded = load_corpus(strict=True)
    expected = sum(1 for _ in DEFAULT_CORPUS_DIR.rglob("*.json"))
    assert len(loaded.concepts) == expected, (
        f"expected {expected} concepts, got {len(loaded.concepts)}"
    )
    assert loaded.failures == []
    # Phase 0 spot-check 5 categories present
    cats = loaded.categories
    for required in ("spring", "database", "network", "algorithm", "language"):
        assert required in cats and cats[required] > 0, f"missing category {required}"


def test_encoding_text_includes_aliases_and_queries(tmp_path: Path) -> None:
    """encoding_text() builds 4-section composite for embedding (D2-aligned)."""
    sample = {
        "id": "spring/bean",
        "title": "Bean",
        "category": "spring",
        "level": "beginner",
        "summary": "Container managed object.",
        "body_markdown": "Body content here for testing.",
        "aliases": ["스프링빈", "spring bean"],
        "expected_queries": ["Bean이 뭐야", "what is bean"],
        "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
    }
    text = encoding_text(sample)
    assert "Bean" in text
    assert "스프링빈" in text
    assert "Bean이 뭐야" in text
    assert "Container managed object." in text


def test_load_corpus_rejects_invalid_schema(tmp_path: Path) -> None:
    """Missing required field → failures list captured, strict raises."""
    bad_dir = tmp_path / "concepts"
    (bad_dir / "spring").mkdir(parents=True)
    bad = {"id": "spring/bad", "title": "Bad"}  # missing category/level/summary/body/metadata
    (bad_dir / "spring" / "bad.json").write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ValueError, match="corpus load failed"):
        load_corpus(corpus_dir=bad_dir, strict=True)

    non_strict = load_corpus(corpus_dir=bad_dir, strict=False)
    assert non_strict.concepts == {}
    assert len(non_strict.failures) == 1
    assert "spring/bad.json" in non_strict.failures[0][0]


def test_load_corpus_rejects_duplicate_ids(tmp_path: Path) -> None:
    """Two files with same id → failure."""
    base = {
        "id": "spring/x",
        "title": "Spring X",
        "category": "spring",
        "level": "beginner",
        "summary": "Some summary.",
        "body_markdown": "Body content here for testing the file.",
        "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
    }
    bad_dir = tmp_path / "concepts" / "spring"
    bad_dir.mkdir(parents=True)
    (bad_dir / "a.json").write_text(json.dumps(base), encoding="utf-8")
    (bad_dir / "b.json").write_text(json.dumps(base), encoding="utf-8")

    loaded = load_corpus(corpus_dir=tmp_path / "concepts", strict=False)
    assert len(loaded.concepts) == 1
    assert len(loaded.failures) == 1
    assert "duplicate id" in loaded.failures[0][1]


def test_corpus_snapshot_round_trip(tmp_path: Path) -> None:
    corpus = load_corpus(strict=True)
    path = tmp_path / "corpus_snapshot.json"

    stats = write_corpus_snapshot(corpus, path)
    loaded = load_corpus_snapshot(path)

    assert stats["concepts"] == len(corpus.concepts)
    assert loaded is not None
    assert loaded.failures == []
    assert loaded.concepts["spring/ioc-di-basics"]["title"]


def test_corpus_snapshot_rejects_stale_count(tmp_path: Path) -> None:
    corpus = load_corpus(strict=True)
    path = tmp_path / "corpus_snapshot.json"
    write_corpus_snapshot(corpus, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["concept_count"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_corpus_snapshot(path) is None


def test_schema_path_exists() -> None:
    assert DEFAULT_SCHEMA_PATH.is_file()
    assert DEFAULT_CORPUS_DIR.is_dir()
