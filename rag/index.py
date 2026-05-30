"""Lance concept dense index — build + load.

Hypothesis (Phase 2):
- Current corpus × 1024-d float32 vectors should keep Lance index ≤50MB
- Index build time on RunPod H100 warm BGE-M3: <5min
- Index build time on M4 warm BGE-M3: <30min (encode dominates)

Builder reads JSON corpus, encodes via rag.encoder, writes Lance table.
Loader opens existing Lance table read-only for search.

Lance schema (intentionally minimal — body lives in JSON corpus, not index):
- concept_id: str
- vector: list[float32, 1024]
- category: str
- title: str
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rag.corpus_loader import (
    DEFAULT_CORPUS_DIR,
    corpus_fingerprint,
    encoding_text,
    load_corpus,
)

if TYPE_CHECKING:
    import numpy as np
    import pyarrow as pa
    from lancedb.table import Table

DEFAULT_INDEX_DIR = Path(__file__).resolve().parent.parent / "state" / "index"
TABLE_NAME = "concepts"
EMBED_DIM = 1024


def _schema() -> pa.Schema:
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("concept_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
            pa.field("category", pa.string()),
            pa.field("title", pa.string()),
        ]
    )


@dataclass(frozen=True)
class IndexBuildReport:
    concepts_indexed: int
    elapsed_seconds: float
    index_size_bytes: int
    index_path: Path
    corpus_sha256: str = ""


def build_index(
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    index_dir: Path = DEFAULT_INDEX_DIR,
    batch_size: int = 16,
    encode_fn: Callable[[list[str]], np.ndarray] | None = None,
) -> IndexBuildReport:
    """Build Lance dense index from JSON corpus.

    encode_fn override exists for tests — production uses rag.encoder.encode.
    """
    import lancedb
    import numpy as np
    import pyarrow as pa

    loaded = load_corpus(corpus_dir=corpus_dir, strict=True)
    ids: list[str] = []
    texts: list[str] = []
    categories: list[str] = []
    titles: list[str] = []
    for cid in sorted(loaded.concepts):
        c = loaded.concepts[cid]
        ids.append(cid)
        texts.append(encoding_text(c))
        categories.append(c["category"])
        titles.append(c["title"])

    if encode_fn is None:
        from rag.encoder import encode as _encode  # lazy ML import

        encode_fn = lambda batch: _encode(batch, batch_size=batch_size)  # noqa: E731

    t0 = time.perf_counter()
    vectors = encode_fn(texts)
    elapsed = time.perf_counter() - t0
    if vectors.shape != (len(ids), EMBED_DIM):
        raise ValueError(f"encoder output shape {vectors.shape} != expected {(len(ids), EMBED_DIM)}")

    index_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(index_dir))
    if TABLE_NAME in db.list_tables().tables:
        db.drop_table(TABLE_NAME)
    table = db.create_table(
        TABLE_NAME,
        data=pa.table(
            {
                "concept_id": ids,
                "vector": vectors.astype(np.float32).tolist(),
                "category": categories,
                "title": titles,
            },
            schema=_schema(),
        ),
    )
    _ = table.count_rows()

    table_path = index_dir / f"{TABLE_NAME}.lance"
    size = sum(p.stat().st_size for p in table_path.rglob("*") if p.is_file())
    return IndexBuildReport(
        concepts_indexed=len(ids),
        elapsed_seconds=elapsed,
        index_size_bytes=size,
        index_path=index_dir,
        corpus_sha256=corpus_fingerprint(loaded),
    )


def open_index(index_dir: Path = DEFAULT_INDEX_DIR) -> "Table":
    import lancedb

    db = lancedb.connect(str(index_dir))
    if TABLE_NAME not in db.list_tables().tables:
        raise FileNotFoundError(f"no '{TABLE_NAME}' table at {index_dir}")
    return db.open_table(TABLE_NAME)


def _write_manifest(report: IndexBuildReport) -> None:
    manifest = {
        "schema_version": "v2",
        "concepts_indexed": report.concepts_indexed,
        "encode_seconds": round(report.elapsed_seconds, 2),
        "index_size_bytes": report.index_size_bytes,
        "embed_dim": EMBED_DIM,
        "encoder_model": "BAAI/bge-m3",
        "corpus_sha256": report.corpus_sha256,
        "built_at": time.time(),
    }
    (report.index_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def check_corpus_drift(
    corpus, index_dir: Path = DEFAULT_INDEX_DIR
) -> str | None:
    """Return a warning string if the on-disk index is stale vs the corpus.

    Compares manifest.json's corpus_sha256 against the current corpus
    fingerprint. Soft by design: returns None when the manifest is missing or
    predates the corpus_sha256 key (older fetched indexes), so a stale-key index
    never hard-fails startup — only a confirmed mismatch warns.
    """
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    indexed_sha = manifest.get("corpus_sha256")
    if not indexed_sha:
        return None
    current_sha = corpus_fingerprint(corpus)
    if indexed_sha == current_sha:
        return None
    return (
        f"corpus↔index drift: corpus_sha256={current_sha[:12]} but index manifest "
        f"has {str(indexed_sha)[:12]} — run bin/index-fetch (learner) or rebuild "
        f"(maintainer); search may use stale embeddings."
    )
