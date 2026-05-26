#!/usr/bin/env python3
"""Synthetic active-path gate for learner-profile score adjustment.

Measures all three Y13 dimensions without loading the embedding model:
- quality: mastered concept is demoted and uncertain concept is boosted
- performance: iterations/sec for the adjustment step
- latency: p50/p95/max CPU time per adjustment
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from core.lazy_loader import _personalize_hits, personalization_active  # noqa: E402
from core.state import LearnerProfile  # noqa: E402
from rag.search import SearchHit  # noqa: E402


def _hit(cid: str, score: float) -> SearchHit:
    return SearchHit(
        concept_id=cid,
        score=score,
        category=cid.split("/", 1)[0],
        title=cid,
        source="synthetic",
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return ordered[idx]


def run(iterations: int) -> dict:
    hits = [
        _hit("spring/bean", 0.90),
        _hit("spring/component", 0.80),
        _hit("database/index", 0.75),
    ]
    profile = LearnerProfile(
        learner_id="synthetic",
        mastered_concepts=["spring/bean"],
        uncertain_concepts=["database/index"],
    )
    before_rank = {h.concept_id: idx + 1 for idx, h in enumerate(hits)}

    durations_ms: list[float] = []
    adjusted: list[SearchHit] = []
    meta: dict | None = None
    started_all = time.perf_counter()
    for _ in range(iterations):
        started = time.perf_counter()
        adjusted, meta = _personalize_hits(hits, profile)
        durations_ms.append((time.perf_counter() - started) * 1000)
    total_s = time.perf_counter() - started_all

    after_rank = {h.concept_id: idx + 1 for idx, h in enumerate(adjusted)}
    quality = {
        "enabled": personalization_active(),
        "top1_after": adjusted[0].concept_id if adjusted else None,
        "mastered_rank_delta": (
            after_rank["spring/bean"] - before_rank["spring/bean"]
        ),
        "uncertain_rank_delta": (
            before_rank["database/index"] - after_rank["database/index"]
        ),
        "mastered_applied": (meta or {}).get("mastered_applied", []),
        "uncertain_applied": (meta or {}).get("uncertain_applied", []),
    }
    latency = {
        "p50_ms": statistics.median(durations_ms),
        "p95_ms": _percentile(durations_ms, 0.95),
        "max_ms": max(durations_ms),
    }
    performance = {
        "iterations": iterations,
        "total_s": total_s,
        "iterations_per_s": iterations / total_s if total_s > 0 else 0.0,
    }
    thresholds = {
        "mastered_rank_delta_min": 2,
        "uncertain_rank_delta_min": 2,
        "adjust_p95_ms_max": 20.0,
    }
    passed = (
        quality["enabled"] is True
        and quality["top1_after"] == "database/index"
        and quality["mastered_rank_delta"] >= thresholds["mastered_rank_delta_min"]
        and quality["uncertain_rank_delta"] >= thresholds["uncertain_rank_delta_min"]
        and latency["p95_ms"] <= thresholds["adjust_p95_ms_max"]
    )
    return {
        "passed": passed,
        "quality": quality,
        "performance": performance,
        "latency": latency,
        "thresholds": thresholds,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "reports" / "personalization_active_path.json",
    )
    args = parser.parse_args(argv)

    report = run(args.iterations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
