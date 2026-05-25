"""tests/benchmarks/profile_recompute_bench.py — Phase T6 perf bench.

Measures:
  1. Real 10K-event history recompute latency ≤ 1500ms
  2. v3 schema validity (all required keys present)
  3. Mode filter correctness — development events excluded
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.profile import SCHEMA_VERSION, recompute_profile  # noqa: E402
from core.state import DEFAULT_STATE_ROOT, read_history  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "profile_recompute_bench.json"

TARGETS = {
    "p95_ms_max": 1500,
}

REQUIRED_KEYS = {
    "schema_version", "learner_id", "computed_at", "experience_level",
    "concepts", "activity", "recent_code_changes_24h",
    "calibration_status", "next_recommendations",
}


def measure() -> dict:
    # 1. latency on real production history (paradigm-v2 state)
    history_n = len(read_history(state_root=DEFAULT_STATE_ROOT))
    times = []
    profile = None
    for _ in range(3):
        t0 = time.perf_counter()
        profile = recompute_profile(state_root=DEFAULT_STATE_ROOT)
        times.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(times)
    p95 = max(times)

    # 2. schema validity
    missing_keys = REQUIRED_KEYS - set(profile.keys())
    schema_ok = (profile["schema_version"] == SCHEMA_VERSION
                 and not missing_keys
                 and {"mastered", "proficient", "uncertain", "underexplored"} ==
                     set(profile["concepts"].keys()))

    # 3. mode filter — count dev events from raw vs learning_events
    all_events = read_history(state_root=DEFAULT_STATE_ROOT)
    dev_count = sum(1 for e in all_events if e.get("mode") in ("development", "test"))
    learning_count = sum(1 for e in all_events
                          if e.get("mode") not in ("development", "test"))
    filter_correct = profile["activity"]["events_total"] == learning_count

    current = {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "history_total": history_n,
        "history_dev_excluded": dev_count,
        "history_learning_included": learning_count,
        "profile_events_total": profile["activity"]["events_total"],
        "profile_mastered_n": len(profile["concepts"]["mastered"]),
        "profile_experience": profile["experience_level"],
        "missing_keys": list(missing_keys),
    }
    axes = {
        "latency_under_target": (p95 <= TARGETS["p95_ms_max"],
                                  f"p95 {current['p95_ms']}ms ≤ {TARGETS['p95_ms_max']}ms"),
        "schema_valid": (schema_ok,
                          f"missing={list(missing_keys)} schema_version={profile['schema_version']}"),
        "mode_filter_correct": (filter_correct,
                                 f"profile.events {profile['activity']['events_total']} == "
                                 f"learning {learning_count} (dev excluded={dev_count})"),
    }
    return {
        "baseline_legacy_ms": "n/a (legacy didn't expose recompute timing)",
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
        "p50": report["current"]["p50_ms"],
        "p95": report["current"]["p95_ms"],
        "history_total": report["current"]["history_total"],
        "profile_events": report["current"]["profile_events_total"],
        "mastered": report["current"]["profile_mastered_n"],
        "experience": report["current"]["profile_experience"],
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()},
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
