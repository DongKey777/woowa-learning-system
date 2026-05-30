"""Tests for rag.index — uses mock encoder so no BGE-M3 download on M4."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rag.corpus_loader import DEFAULT_CORPUS_DIR
from rag.index import EMBED_DIM, IndexBuildReport, build_index, open_index


def _concept_file_count() -> int:
    return sum(1 for _ in DEFAULT_CORPUS_DIR.rglob("*.json"))


def _mock_encode_factory(seed: int = 0):
    """Deterministic fake encoder — produces normalized random vectors."""
    rng = np.random.default_rng(seed)

    def _encode(texts: list[str]) -> np.ndarray:
        vecs = rng.standard_normal((len(texts), EMBED_DIM)).astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.clip(norms, 1e-9, None)

    return _encode


def test_build_index_round_trip(tmp_path: Path) -> None:
    """Build index from the real corpus using mock encoder, verify Lance table."""
    index_dir = tmp_path / "index"
    report = build_index(index_dir=index_dir, encode_fn=_mock_encode_factory(seed=42))

    assert isinstance(report, IndexBuildReport)
    expected = _concept_file_count()
    assert report.concepts_indexed == expected
    assert report.index_path == index_dir
    # Chunk index: one card chunk + N body windows per concept, so rows >= concepts.
    assert report.chunks_indexed >= expected

    table = open_index(index_dir=index_dir)
    assert table.count_rows() == report.chunks_indexed

    rows = table.to_pandas()
    # Every concept is present exactly once as a group key (chunk rows repeat it).
    assert rows["concept_id"].nunique() == expected
    assert {"concept_id", "chunk_index", "vector", "category", "title"} <= set(rows.columns)
    # chunk 0 (the card) must exist for every concept (top1-precision guard).
    assert set(rows[rows["chunk_index"] == 0]["concept_id"]) == set(rows["concept_id"].unique())
    first_vec = rows["vector"].iloc[0]
    assert len(first_vec) == EMBED_DIM


def test_build_index_rejects_shape_mismatch(tmp_path: Path) -> None:
    """Encoder returning wrong shape raises before Lance write."""
    bad = lambda texts: np.zeros((len(texts), 512), dtype=np.float32)  # wrong dim
    with pytest.raises(ValueError, match="encoder output shape"):
        build_index(index_dir=tmp_path / "index", encode_fn=bad)


def test_open_index_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_index(index_dir=tmp_path / "nope")


def test_chunk_index_size_tracks_raw_vector_floor(tmp_path: Path) -> None:
    """Lance index size should stay close to its raw chunk-vector floor.

    The chunk index holds ~26k vectors (card + body windows per concept) at
    1024 float32 = ~102MB raw. Real BGE-M3 vectors are the same bytes as mock
    ones, so this confirms Lance overhead does not balloon the index beyond a
    small multiple of the raw float floor.
    """
    index_dir = tmp_path / "index"
    report = build_index(index_dir=index_dir, encode_fn=_mock_encode_factory())
    mb = report.index_size_bytes / (1024 * 1024)
    raw_floor_mb = report.chunks_indexed * EMBED_DIM * 4 / (1024 * 1024)
    assert mb < raw_floor_mb * 1.5, (
        f"index unexpectedly large: {mb:.1f}MB vs raw floor {raw_floor_mb:.1f}MB"
    )


def test_index_size_excludes_neighbor_sidecars(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "corpus_snapshot.json").write_text("x" * 1024, encoding="utf-8")

    report = build_index(index_dir=index_dir, encode_fn=_mock_encode_factory())
    actual_table_size = sum(
        p.stat().st_size for p in (index_dir / "concepts.lance").rglob("*") if p.is_file()
    )

    assert report.index_size_bytes == actual_table_size


def test_lance_search_returns_ranked_neighbors(tmp_path: Path) -> None:
    """Sanity: query vector → top-K results ordered by distance."""
    index_dir = tmp_path / "index"
    build_index(index_dir=index_dir, encode_fn=_mock_encode_factory(seed=7))
    table = open_index(index_dir=index_dir)
    q = np.random.default_rng(7).standard_normal(EMBED_DIM).astype(np.float32)
    q = q / np.linalg.norm(q)
    results = table.search(q).limit(5).to_pandas()
    assert len(results) == 5
    assert "_distance" in results.columns
    assert list(results["_distance"]) == sorted(results["_distance"])
