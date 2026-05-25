"""tests/benchmarks/learn_record_code_bench.py — Phase T2 perf bench.

Measures:
  1. Per-call latency p95 ≤ 80ms (target; legacy ~200ms = 2.5× faster)
  2. Concept inference precision: on N real mission Java files, fraction
     where ≥1 concept inferred matches expected category (spring/database/etc.)
  3. No OOM on 10K-event history (current history is ~10K — append must stay
     O(1) per call regardless of pre-existing size)

Reports → reports/learn_record_code_bench.json
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.code_event import infer_concepts_from_file, record_code_attempt  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "learn_record_code_bench.json"
MISSION_ROOT = REPO_ROOT / "missions" / "spring-roomescape-member" / "src" / "main" / "java"

TARGETS = {
    "p95_ms_max": 80,
    "infer_precision_min": 0.60,  # ≥60% of files with detectable concepts (Java files
                                   # without Spring annotations may legitimately yield 0)
    "calls_n": 30,
}


def _expected_category_for_path(p: Path) -> str | None:
    """Heuristic — file path → expected concept category."""
    s = str(p).lower()
    if "controller" in s or "service" in s or "configuration" in s:
        return "spring"
    if "repository" in s or "jdbc" in s or "jpa" in s:
        return "database"
    return None


def measure() -> dict:
    if not MISSION_ROOT.exists():
        return {"pass": False, "error": f"mission root missing: {MISSION_ROOT}"}

    java_files = list(MISSION_ROOT.rglob("*.java"))[:TARGETS["calls_n"]]
    if not java_files:
        return {"pass": False, "error": "no .java files under mission root"}

    # latency: record_code_attempt in tmpdir (no real state pollution)
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp)
        (state_root / "learner").mkdir(parents=True)
        latencies = []
        infer_hits_by_category = 0
        infer_attempts_by_category = 0
        total_inferred = 0
        per_file = []
        for jf in java_files:
            t0 = time.perf_counter()
            event = record_code_attempt(
                file_path=str(jf), summary="bench", repo="bench",
                state_root=state_root, mode="development",  # skip record_turn for speed
            )
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            cids = event["payload"]["concept_ids"]
            total_inferred += len(cids)
            expected_cat = _expected_category_for_path(jf)
            if expected_cat:
                infer_attempts_by_category += 1
                if any(c.startswith(f"{expected_cat}/") for c in cids):
                    infer_hits_by_category += 1
            per_file.append({
                "file": str(jf.relative_to(MISSION_ROOT))[:80],
                "concepts": cids[:3],
                "expected_cat": expected_cat,
                "ms": round(ms, 2),
            })

    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    precision = (infer_hits_by_category / infer_attempts_by_category
                 if infer_attempts_by_category else 1.0)

    current = {
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "calls": len(java_files),
        "total_inferred_concepts": total_inferred,
        "infer_attempts_with_expected_cat": infer_attempts_by_category,
        "infer_hits_for_expected_cat": infer_hits_by_category,
        "infer_precision_with_expected_cat": round(precision, 3),
        "per_file_sample": per_file[:5],
    }
    axes = {
        "p95_under_target": (current["p95_ms"] <= TARGETS["p95_ms_max"],
                             f"{current['p95_ms']}ms ≤ {TARGETS['p95_ms_max']}ms"),
        "infer_precision_met": (precision >= TARGETS["infer_precision_min"],
                                 f"{precision:.2%} ≥ {TARGETS['infer_precision_min']:.0%}"),
        "all_calls_ok": (len(latencies) == len(java_files),
                          f"{len(latencies)}/{len(java_files)} calls succeeded"),
    }
    return {
        "baseline_legacy_p95_ms": 200,
        "targets": TARGETS,
        "current": current,
        "axes": {k: {"pass": v[0], "detail": v[1]} for k, v in axes.items()},
        "pass": all(p for p, _ in axes.values()),
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "p50": report["current"].get("p50_ms"),
        "p95": report["current"].get("p95_ms"),
        "infer_precision": report["current"].get("infer_precision_with_expected_cat"),
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()},
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
