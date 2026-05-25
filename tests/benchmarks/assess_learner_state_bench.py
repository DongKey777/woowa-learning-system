"""tests/benchmarks/assess_learner_state_bench.py — Phase T5 perf bench.

Measures:
  1. Wall-clock p95 ≤ 45s (target; legacy 60s)
  2. Thread classification correctness on synthetic fixture (3 known cases)
  3. Budget timeout → partial coverage gracefully (no crash)

Real-archive smoke: spring-roomescape-member (paradigm-v2 self-contained).
"""
from __future__ import annotations

import json
import statistics
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.learner_state import _classify_thread, assess_learner_state  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "assess_learner_state_bench.json"
MEMBER_MISSION = REPO_ROOT / "missions" / "spring-roomescape-member"
STATE_ROOT = REPO_ROOT / "state"

TARGETS = {
    "p95_ms_max": 45000,           # 45s (legacy 60s)
    "classification_correct_min": 3,  # synthetic fixture: 3 fixed cases
}


def _classification_fixture() -> int:
    """3 known classifications — count correct."""
    correct = 0
    if _classify_thread("body", "X.java", 1, True, True) == "already-fixed":
        correct += 1
    if _classify_thread("body", "Gone.java", 1, False, False) == "likely-fixed":
        correct += 1
    if _classify_thread("body", "X.java", 10, False, True) == "still-applies":
        correct += 1
    return correct


def measure() -> dict:
    if not MEMBER_MISSION.exists():
        return {"pass": False, "error": f"mission missing: {MEMBER_MISSION}"}

    # Wall-clock on real paradigm-v2 self-contained state
    times = []
    threads_n = 0
    payload_sample = None
    for _ in range(3):
        t0 = time.perf_counter()
        payload = assess_learner_state(
            repo="spring-roomescape-member",
            mission_path=MEMBER_MISSION,
            state_root=STATE_ROOT,
            learner_login="DongKey777",
        )
        times.append((time.perf_counter() - t0) * 1000)
        if payload.get("target_pr"):
            threads_n = payload["target_pr"].get("threads_total", 0)
        payload_sample = payload

    p50 = statistics.median(times)
    p95 = max(times)

    classification_correct = _classification_fixture()

    # Budget timeout test (set to 0 → forces partial path early)
    t0 = time.perf_counter()
    partial = assess_learner_state(
        repo="spring-roomescape-member", mission_path=MEMBER_MISSION,
        state_root=STATE_ROOT, learner_login="DongKey777",
        budget_secs=0.001,  # immediate budget exhaustion after first step
    )
    budget_test_ms = (time.perf_counter() - t0) * 1000

    current = {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "real_threads_n": threads_n,
        "real_target_pr": (payload_sample or {}).get("target_pr", {}) and
                          payload_sample["target_pr"].get("number"),
        "classification_correct_n": classification_correct,
        "budget_test_ms": round(budget_test_ms, 1),
        "budget_test_partial": partial.get("coverage") == "partial",
    }
    axes = {
        "latency_under_target": (p95 <= TARGETS["p95_ms_max"],
                                  f"p95 {current['p95_ms']}ms ≤ {TARGETS['p95_ms_max']}ms"),
        "classification_correct": (classification_correct >= TARGETS["classification_correct_min"],
                                    f"{classification_correct}/3 cases"),
        "budget_partial_graceful": (current["budget_test_partial"] and budget_test_ms < 5000,
                                     f"timeout fallback in {budget_test_ms:.0f}ms with coverage=partial"),
    }
    return {
        "baseline_legacy_ms": 60000,
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
        "threads_on_member": report["current"]["real_threads_n"],
        "classify_correct": f"{report['current']['classification_correct_n']}/3",
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()},
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
