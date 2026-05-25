"""tests/benchmarks/session_start_bench.py — Phase T7 perf bench.

Measures (paradigm-v2 daemon must be running):
  1. Cold session p95 ≤ 50s (assess + profile + ask)
  2. Warm session p95 ≤ 800ms (caches fresh → skip assess/profile, just ask)
  3. Mode dispatch correctness on 5 sample prompts (4/5 expected = 80%+)
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.session import start_session  # noqa: E402
from core.state import DEFAULT_STATE_ROOT  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "session_start_bench.json"
MEMBER_MISSION = REPO_ROOT / "missions" / "spring-roomescape-member"

TARGETS = {
    "cold_p95_ms_max": 50000,
    "warm_p95_ms_max": 800,
    "mode_correct_min": 4,
}

SAMPLES = [
    ("Bean DI 더 설명해줘", "cs_qa"),
    ("내 ReservationController 어떻게 리팩토링", "coaching"),
    ("git rebase -i 어떻게 써", "tool_only"),
    ("다른 크루는 어떻게 작성했어", "f11_anchor"),
    ("내 PR 흐름 보여줘", "retro"),
]


def _ensure_daemon() -> bool:
    """Ping daemon; return True if alive."""
    sock = DEFAULT_STATE_ROOT / "rag-daemon.sock"
    if not sock.exists():
        return False
    try:
        r = subprocess.run(["bin/rag-daemon", "ping"],
                            cwd=REPO_ROOT, capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def measure() -> dict:
    if not _ensure_daemon():
        return {"pass": False, "error": "daemon not running — start with `nohup bin/rag-daemon start`"}

    # Force cold (force_refresh=True) for cold-path measurement
    cold_times = []
    for _ in range(2):
        t0 = time.perf_counter()
        result = start_session(
            repo="spring-roomescape-member",
            prompt="Bean DI 더 설명해줘",
            mission_path=MEMBER_MISSION,
            force_refresh=True,
        )
        cold_times.append((time.perf_counter() - t0) * 1000)
    cold_p50 = statistics.median(cold_times)
    cold_p95 = max(cold_times)

    # Warm: caches just refreshed → second calls should be ~ask-only
    warm_times = []
    for _ in range(3):
        t0 = time.perf_counter()
        result = start_session(
            repo="spring-roomescape-member",
            prompt="Bean DI 더 설명해줘",
            mission_path=MEMBER_MISSION,
            force_refresh=False,
        )
        warm_times.append((time.perf_counter() - t0) * 1000)
    warm_p50 = statistics.median(warm_times)
    warm_p95 = max(warm_times)

    # Mode dispatch correctness
    mode_correct = 0
    mode_results = []
    for prompt, expected in SAMPLES:
        r = start_session(repo="spring-roomescape-member", prompt=prompt,
                           mission_path=MEMBER_MISSION, force_refresh=False)
        got = r.get("mode")
        ok = got == expected
        if ok:
            mode_correct += 1
        mode_results.append({"prompt": prompt[:40], "expected": expected, "got": got, "ok": ok})

    current = {
        "cold_p50_ms": round(cold_p50, 1),
        "cold_p95_ms": round(cold_p95, 1),
        "warm_p50_ms": round(warm_p50, 1),
        "warm_p95_ms": round(warm_p95, 1),
        "mode_correct": mode_correct,
        "mode_samples": len(SAMPLES),
        "mode_results": mode_results,
    }
    axes = {
        "cold_under_target": (cold_p95 <= TARGETS["cold_p95_ms_max"],
                                f"cold p95 {current['cold_p95_ms']}ms ≤ {TARGETS['cold_p95_ms_max']}ms"),
        "warm_under_target": (warm_p95 <= TARGETS["warm_p95_ms_max"],
                                f"warm p95 {current['warm_p95_ms']}ms ≤ {TARGETS['warm_p95_ms_max']}ms"),
        "mode_dispatch_ok": (mode_correct >= TARGETS["mode_correct_min"],
                              f"{mode_correct}/{len(SAMPLES)} ≥ {TARGETS['mode_correct_min']}"),
    }
    return {
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
        "cold_p95": report["current"].get("cold_p95_ms") if "current" in report else None,
        "warm_p95": report["current"].get("warm_p95_ms") if "current" in report else None,
        "mode_correct": f"{report['current']['mode_correct']}/{report['current']['mode_samples']}"
                          if "current" in report else "n/a",
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()}
                  if "axes" in report else None,
        "error": report.get("error"),
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
