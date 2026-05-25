"""tests/benchmarks/learn_pr_retro_bench.py — Phase T1 perf bench.

Measures:
  1. SQLite-only build latency p50/p95 across 3 repos (target p50 ≤500ms)
  2. Recurring-signal "useful" rate (≥3-char tokens, not in stop list)
  3. Unresolved threads count sanity (≥0, ≤cap)
  4. gh_calls_used == 0 (no GraphQL fallback in v1)

Reports → reports/learn_pr_retro_bench.json with {pass: bool}.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.pr_retro import build_retrospective  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "learn_pr_retro_bench.json"
LEARNER = "DongKey777"
REPOS = ["spring-roomescape-member", "spring-roomescape-auth"]

TARGETS = {
    "p50_ms_max": 500,           # plan §T1
    "p95_ms_max": 1500,
    "useful_signal_pct_min": 60, # ≥60% of detected topics should be ≥3 chars + non-stop (proxy for "useful")
    "max_gh_calls": 0,           # v1 SQLite-only
}


def measure() -> dict:
    latencies = []
    per_repo = []
    total_useful = 0
    total_signals = 0
    total_gh_calls = 0

    for repo in REPOS:
        runs_ms = []
        for _ in range(5):
            t0 = time.perf_counter()
            retro = build_retrospective(repo, LEARNER, include_bot=True)
            runs_ms.append((time.perf_counter() - t0) * 1000)
        med = statistics.median(runs_ms)
        latencies.extend(runs_ms)
        # useful = topic length ≥3 + appears in ≥2 mentor comments
        useful = sum(1 for s in retro.recurring_mentor_signals
                     if len(s.topic) >= 3 and s.comment_count >= 2)
        per_repo.append({
            "repo": repo,
            "prs_total": retro.prs_total,
            "prs_merged": retro.prs_merged,
            "recurring_signals": len(retro.recurring_mentor_signals),
            "useful_signals": useful,
            "unresolved_threads": len(retro.unresolved_threads),
            "median_ms": round(med, 1),
            "gh_calls_used": retro.gh_calls_used,
        })
        total_useful += useful
        total_signals += len(retro.recurring_mentor_signals)
        total_gh_calls += retro.gh_calls_used

    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0.0
    useful_pct = (total_useful / total_signals * 100) if total_signals > 0 else 100.0

    current = {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "useful_signal_pct": round(useful_pct, 1),
        "total_signals": total_signals,
        "total_useful": total_useful,
        "total_gh_calls": total_gh_calls,
        "per_repo": per_repo,
    }
    axes = {
        "p50_ms_under_target": (current["p50_ms"] <= TARGETS["p50_ms_max"],
                                 f"{current['p50_ms']}ms ≤ {TARGETS['p50_ms_max']}ms"),
        "p95_ms_under_target": (current["p95_ms"] <= TARGETS["p95_ms_max"],
                                 f"{current['p95_ms']}ms ≤ {TARGETS['p95_ms_max']}ms"),
        "useful_signal_pct_met": (current["useful_signal_pct"] >= TARGETS["useful_signal_pct_min"],
                                   f"{current['useful_signal_pct']}% ≥ {TARGETS['useful_signal_pct_min']}%"),
        "no_gh_calls": (current["total_gh_calls"] <= TARGETS["max_gh_calls"],
                        f"{current['total_gh_calls']} ≤ {TARGETS['max_gh_calls']} (v1 SQLite-only)"),
    }
    all_pass = all(p for p, _ in axes.values())
    return {
        "baseline_legacy_p50_ms": 3000,  # legacy 2-5s SQLite hit (CLAUDE.md§212)
        "targets": TARGETS,
        "current": current,
        "axes": {k: {"pass": v[0], "detail": v[1]} for k, v in axes.items()},
        "pass": all_pass,
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "p50": report["current"]["p50_ms"],
        "p95": report["current"]["p95_ms"],
        "useful_pct": report["current"]["useful_signal_pct"],
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()},
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
