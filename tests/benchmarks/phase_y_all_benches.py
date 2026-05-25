"""tests/benchmarks/phase_y_all_benches.py — meta runner that executes every
phase bench sequentially and aggregates pass/fail. Final acceptance gate.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH_DIR = REPO_ROOT / "tests" / "benchmarks"
REPORT_PATH = REPO_ROOT / "reports" / "phase_y_master_bench.json"

# Order matters — earlier benches don't depend on later state changes,
# but we leave the daemon-restart bench (Phase M S5) for last.
BENCHES = [
    # Foundation benches (Phase J/K/L from earlier)
    "rag_quality_regression.py",
    "gate_measurements.py",
    # Phase T (learner automation, 7)
    "learn_pr_retro_bench.py",
    "learn_record_code_bench.py",
    "learn_test_bench.py",
    "learn_response_quality_bench.py",
    "assess_learner_state_bench.py",
    "profile_recompute_bench.py",
    "session_start_bench.py",
    "phase_t_e2e_integration.py",
    # Phase U (onboarding/collection, 10)
    "phase_u_wrappers_bench.py",
    # Phase V (coaching context, 12)
    "phase_v_wrappers_bench.py",
    # Phase W (mining/analytics, 12)
    "phase_w_wrappers_bench.py",
    # Phase X (maintenance, 11)
    "phase_x_wrappers_bench.py",
]


def _run(name: str, timeout: float = 300) -> dict:
    path = BENCH_DIR / name
    if not path.exists():
        return {"name": name, "rc": -1, "ms": 0, "missing": True}
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            [sys.executable, str(path)],
            cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=timeout,
            env={**__import__("os").environ, "WOOWA_SESSION_MODE": "development"},
        )
        ms = (time.perf_counter() - t0) * 1000
        # parse last JSON-ish "pass" boolean from stdout if present
        passed_from_output = None
        try:
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            for l in reversed(lines):
                if '"pass":' in l:
                    passed_from_output = "true" in l.lower()
                    break
        except Exception:
            pass
        return {
            "name": name, "rc": r.returncode, "ms": round(ms, 1),
            "stdout_tail": r.stdout[-300:] if r.stdout else "",
            "stderr_tail": r.stderr[-300:] if r.stderr else "",
            "passed": (r.returncode == 0) and (passed_from_output is not False),
        }
    except subprocess.TimeoutExpired:
        return {"name": name, "rc": 124, "ms": -1, "timeout": True, "passed": False}


def main() -> int:
    results = []
    for b in BENCHES:
        print(f"Running {b}...", flush=True)
        results.append(_run(b))
    passed = sum(1 for r in results if r.get("passed"))
    report = {
        "total": len(results),
        "passed": passed,
        "failed": [r for r in results if not r.get("passed")],
        "all_results": [{"name": r["name"], "rc": r["rc"], "ms": r["ms"],
                          "passed": r.get("passed", False)} for r in results],
        "pass": passed == len(results),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    summary = [{"name": r["name"], "rc": r["rc"], "ms": r["ms"],
                  "pass": r.get("passed", False)} for r in results]
    print(json.dumps({"summary": summary, "passed": passed,
                       "total": len(results), "pass": report["pass"]},
                       ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
