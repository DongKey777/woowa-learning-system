"""Local incremental dense retrieval measurement WITHOUT a RunPod full build.

Builds a TEMP index = released base index (state/index) + freshly-encoded NEW
concepts (ids in the current corpus but absent from the base index), then scores a
qrels probe set via in-process search (dense + relations + CC lexical fusion). This
lets the corpus-expansion pipeline verify a batch's dense retrieval impact per-batch
in ~seconds (BGE-M3 warm-load happens once), deferring the expensive RunPod full
build+release to once per cycle (CYCLE4-PIPELINE.md §2).

Core code is UNMODIFIED — this benchmark only imports:
  rag.index (_schema/TABLE_NAME/EMBED_DIM/DEFAULT_INDEX_DIR/open_index),
  rag.encoder.encode/encode_query, rag.corpus_loader.load_corpus/encoding_text,
  rag.search.search, core.lexical_fusion.make_lexical_fusion_fn.

Why it works (measured): lancedb Table.add appends rows to the existing table, the
base index copytree is ~0.02s, encoding N new concepts is ~18ms each, and rag.search
.search() accepts index_dir/encode_fn/corpus/rerank_fn so no daemon is needed. The
drift guard is daemon-startup-only and does not block in-process search.

CAVEAT: the temp index uses a flat lancedb add (no ANN retrain). Under the current
brute-force cosine search this is EQUIVALENT to a full rebuild, but if IVF/PQ is
later introduced this becomes an approximation — release MUST still be confirmed by
a RunPod full-build cohort-eval (the authority measurement). This tool is for
per-batch early regression catching, not for replacing the final build.

Usage:
  WOOWA_SESSION_MODE=development python3 tests/benchmarks/incremental_batch_eval.py \
      --qrels tests/fixtures/coverage_stratified_v1.json --out reports/incr_eval.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.lexical_fusion import make_lexical_fusion_fn  # noqa: E402
from rag.corpus_loader import DEFAULT_CORPUS_DIR, encoding_text, load_corpus  # noqa: E402
from rag.encoder import encode, encode_query  # noqa: E402
from rag.index import DEFAULT_INDEX_DIR, TABLE_NAME, open_index  # noqa: E402
from rag.search import search  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "incremental_batch_eval.json"
DEFAULT_QRELS = REPO_ROOT / "tests" / "fixtures" / "coverage_stratified_v1.json"


def _load_qrels_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    for key in ("queries", "records", "rows", "cases", "qrels"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    raise ValueError(f"could not find qrels rows in {path} (keys={list(payload)})")


def _base_index_ids(index_dir: Path) -> set[str]:
    table = open_index(index_dir=index_dir)
    return {str(cid) for cid in table.to_arrow()["concept_id"].to_pylist()}


def _build_temp_index(base_index_dir: Path, new_ids: list[str], corpus) -> Path:
    """Copy the base index and append freshly-encoded NEW concepts. Returns temp dir."""
    import lancedb

    tmp_dir = Path(tempfile.mkdtemp(prefix="woowa-incr-index-"))
    shutil.copytree(base_index_dir / f"{TABLE_NAME}.lance", tmp_dir / f"{TABLE_NAME}.lance")
    if new_ids:
        texts = [encoding_text(corpus.concepts[cid]) for cid in new_ids]
        vectors = encode(texts, batch_size=16)
        table = lancedb.connect(str(tmp_dir)).open_table(TABLE_NAME)
        table.add([
            {"concept_id": cid, "vector": vec.astype("float32").tolist()}
            for cid, vec in zip(new_ids, vectors)
        ])
    return tmp_dir


def run(qrels_path: Path, base_index_dir: Path, top_k: int, relations_expand: int,
        limit: int | None) -> dict[str, Any]:
    t_warm = time.perf_counter()
    encode(["warmup"], batch_size=1)  # one-time BGE-M3 warm load
    warm_s = time.perf_counter() - t_warm

    corpus = load_corpus(DEFAULT_CORPUS_DIR, strict=True)
    base_ids = _base_index_ids(base_index_dir)
    new_ids = [cid for cid in corpus.concepts if cid not in base_ids]

    t_idx = time.perf_counter()
    tmp_dir = _build_temp_index(base_index_dir, new_ids, corpus)
    index_s = time.perf_counter() - t_idx

    fusion = make_lexical_fusion_fn(corpus, expand=8)
    rows = _load_qrels_rows(qrels_path)
    if limit is not None:
        rows = rows[:limit]

    new_set = set(new_ids)
    overall = {"n": 0, "top1": 0, "recall5": 0, "new_in_top5": 0}
    per_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "top1": 0, "recall5": 0})
    forbidden_hits = 0
    misses: list[dict[str, Any]] = []
    latencies: list[float] = []

    for row in rows:
        prompt = str(row.get("prompt") or "")
        gold = set(str(x) for x in (row.get("expected_concepts") or []))
        forbidden = set(str(x) for x in (row.get("forbidden_concepts") or []))
        category = str(row.get("category") or "unknown")
        if not prompt or not gold:
            continue
        t0 = time.perf_counter()
        hits = search(prompt, top_k=top_k, relations_expand=relations_expand,
                      rerank_fn=fusion, encode_fn=encode_query, corpus=corpus, index_dir=tmp_dir)
        latencies.append((time.perf_counter() - t0) * 1000)
        top_ids = [h.concept_id for h in hits[:top_k]]

        overall["n"] += 1
        per_cat[category]["n"] += 1
        is_top1 = bool(top_ids) and top_ids[0] in gold
        is_recall5 = any(cid in gold for cid in top_ids)
        if is_top1:
            overall["top1"] += 1
            per_cat[category]["top1"] += 1
        if is_recall5:
            overall["recall5"] += 1
            per_cat[category]["recall5"] += 1
        else:
            misses.append({"prompt": prompt[:80], "gold": sorted(gold), "got": top_ids})
        if any(cid in new_set for cid in top_ids):
            overall["new_in_top5"] += 1
        if forbidden and any(cid in forbidden for cid in top_ids):
            forbidden_hits += 1

    n = max(1, overall["n"])
    report = {
        "benchmark": "incremental_batch_eval",
        "qrels": str(qrels_path),
        "base_index_ids": len(base_ids),
        "corpus_ids": len(corpus.concepts),
        "new_concepts_appended": len(new_ids),
        "warm_load_seconds": round(warm_s, 2),
        "temp_index_seconds": round(index_s, 3),
        "queries": overall["n"],
        "top1_match_rate": round(overall["top1"] / n, 4),
        "recall_at_5": round(overall["recall5"] / n, 4),
        "queries_with_new_concept_in_top5": overall["new_in_top5"],
        "forbidden_hit_count": forbidden_hits,
        "latency_p50_ms": round(sorted(latencies)[len(latencies) // 2], 1) if latencies else None,
        "per_category": {
            cat: {
                "n": v["n"],
                "top1": round(v["top1"] / max(1, v["n"]), 3),
                "recall5": round(v["recall5"] / max(1, v["n"]), 3),
            }
            for cat, v in sorted(per_cat.items())
        },
        "sample_misses": misses[:15],
    }
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrels", type=Path, default=DEFAULT_QRELS)
    parser.add_argument("--base-index", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--relations-expand", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    report = run(args.qrels, args.base_index, args.top_k, args.relations_expand, args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "new_concepts_appended", "temp_index_seconds", "queries", "top1_match_rate",
        "recall_at_5", "queries_with_new_concept_in_top5", "forbidden_hit_count")},
        ensure_ascii=False, indent=2))
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
