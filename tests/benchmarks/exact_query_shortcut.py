"""Exact query shortcut benchmark.

Validates that corpus-authored exact learner phrasings can return a high-quality
RAG hit without loading or calling BGE-M3. Ambiguous short aliases must continue
to fall through to dense retrieval.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "exact_query_shortcut.json"
LEGACY_ROOT = Path("/Users/idonghun/IdeaProjects/woowa-learning-hub")

sys.path.insert(0, str(REPO_ROOT))

from rag.corpus_loader import load_corpus_runtime  # noqa: E402
from rag.index import EMBED_DIM  # noqa: E402
from rag.search import search  # noqa: E402


CASES = [
    {
        "kind": "expected_query",
        "query": "DI가 뭐야",
        "expected": "spring/bean-di-basics",
    },
    {
        "kind": "expected_query",
        "query": "MVC가 뭐야",
        "expected": "spring/mvc-controller-basics",
    },
    {
        "kind": "expected_query",
        "query": "Optional 사용",
        "expected": "language/java-optional-basics",
    },
    {
        "kind": "expected_query",
        # corpus owns the fuller "Stream API 사용" (bare "Stream API" is ambiguous);
        # match the phrasing the concept actually registers as its expected_query.
        "query": "Stream API 사용",
        "expected": "language/java-stream-lambda-basics",
    },
    {
        "kind": "expected_query",
        "query": "Bean이랑 DI는 뭐가 달라?",
        "expected": "spring/bean-di-basics",
    },
    {
        "kind": "expected_query",
        "query": "왜 new 대신 주입받아?",
        "expected": "software-engineering/dependency-injection-basics",
    },
    {
        "kind": "alias",
        "query": "bean이 뭐야",
        "expected": "spring/bean-di-basics",
    },
    {
        "kind": "title",
        "query": "A* vs Dijkstra",
        "expected": "algorithm/a-star-vs-dijkstra",
    },
]


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[idx]


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 3) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 3) if values else None,
        "max_ms": round(max(values), 3) if values else None,
    }


def _failing_encode(_: str) -> np.ndarray:
    raise AssertionError("encoder called despite exact shortcut")


def _marker_encode(called: dict[str, int]):
    def _encode(_: str) -> np.ndarray:
        called["n"] += 1
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        v[0] = 1.0
        return v

    return _encode


def _run_legacy(cases: list[dict[str, str]]) -> dict[str, Any]:
    if not (LEGACY_ROOT / "bin" / "rag-ask").exists():
        return {"available": False, "reason": "legacy bin/rag-ask missing"}
    rows: list[dict[str, Any]] = []
    for case in cases:
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                ["bin/rag-ask", case["query"]],
                cwd=LEGACY_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            ms = (time.perf_counter() - t0) * 1000
            payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
            hits_obj = payload.get("hits") if isinstance(payload, dict) else {}
            rows.append({
                "query": case["query"],
                "rc": proc.returncode,
                "ms": round(ms, 1),
                "mode": (payload.get("decision") or {}).get("mode"),
                "stdout_json": isinstance(payload, dict),
                "top_hit_fragment": json.dumps(hits_obj, ensure_ascii=False)[:300],
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "query": case["query"],
                "error": f"{type(exc).__name__}: {exc}"[:200],
            })
    latencies = [row["ms"] for row in rows if isinstance(row.get("ms"), (int, float))]
    return {"available": True, "latency_ms": _summary(latencies), "rows": rows}


def run(iterations: int, include_legacy: bool) -> dict[str, Any]:
    corpus = load_corpus_runtime()
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for case in CASES:
        case_latencies: list[float] = []
        top1_values: list[str] = []
        source_values: list[str] = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            hits = search(
                case["query"],
                corpus=corpus,
                relations_expand=3,
                encode_fn=_failing_encode,
            )
            ms = (time.perf_counter() - t0) * 1000
            case_latencies.append(ms)
            latencies.append(ms)
            top1_values.append(hits[0].concept_id if hits else "")
            source_values.append(hits[0].source if hits else "")
        rows.append({
            **case,
            "top1_values": sorted(set(top1_values)),
            "source_values": sorted(set(source_values)),
            "pass": (
                set(top1_values) == {case["expected"]}
                and set(source_values) == {"lexical_exact"}
            ),
            "latency_ms": _summary(case_latencies),
        })

    called = {"n": 0}
    ambiguous_fell_through = False
    try:
        search(
            "DI",
            corpus=corpus,
            relations_expand=3,
            encode_fn=_marker_encode(called),
            index_dir=Path("/tmp/woowa-missing-index-for-exact-shortcut"),
        )
    except FileNotFoundError:
        ambiguous_fell_through = called["n"] == 1

    latency = _summary(latencies)
    checks = {
        "all_cases_top1": all(row["pass"] for row in rows),
        "encoder_not_called_for_exact": all(
            "lexical_exact" in row["source_values"] for row in rows
        ),
        "ambiguous_alias_falls_through": ambiguous_fell_through,
        "p95_under_30ms": (latency["p95_ms"] or 10**9) <= 30.0,
    }
    report: dict[str, Any] = {
        "benchmark": "exact_query_shortcut",
        "iterations": iterations,
        "cases": rows,
        "latency_ms": latency,
        "checks": checks,
        "pass": all(checks.values()),
    }
    if include_legacy:
        report["legacy_compare"] = _run_legacy(CASES[:3])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    report = run(args.iterations, args.legacy)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "pass": report["pass"],
        "latency_ms": report["latency_ms"],
        "checks": report["checks"],
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
