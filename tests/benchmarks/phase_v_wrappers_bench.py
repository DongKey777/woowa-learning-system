"""tests/benchmarks/phase_v_wrappers_bench.py — Phase V coaching context
wrappers (12 entries).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

REPORT_PATH = REPO_ROOT / "reports" / "phase_v_wrappers_bench.json"

TARGETS = {
    "coach_run_ms_max": 250,        # daemon warm
    "next_action_ms_max": 200,
    "topic_ms_max": 200,
    "reviewer_ms_max": 1000,        # SQLite scan 100+ comments
    "mission_map_ms_max": 15000,    # walks ~30 java + member 463MB + auth 91MB archive patches
    "rewrite_prepare_ms_max": 80,   # pure templates
    "route_fallback_ms_max": 150,
    "chunk_context_ms_max": 150,    # corpus_graph 5MB load + 4 file write
}


def _run(cmd, timeout=30):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, (time.perf_counter() - t0) * 1000


def measure():
    PY = sys.executable
    results = {}
    _run([PY, "bin/ask", "warmup", "--json"], timeout=30)

    # V1 coach-run (warm via daemon)
    rc, _, ms = _run([PY, "bin/coach-run", "--repo", "spring-roomescape-member",
                       "--prompt", "Bean DI 더 설명", "--silent"])
    results["coach_run"] = {"rc": rc, "ms": round(ms, 1),
                              "ok": rc == 0 and ms <= TARGETS["coach_run_ms_max"]}

    # V4 next-action
    rc, _, ms = _run([PY, "bin/next-action"])
    results["next_action"] = {"rc": rc, "ms": round(ms, 1),
                                "ok": rc == 0 and ms <= TARGETS["next_action_ms_max"]}

    # V5 topic
    rc, _, ms = _run([PY, "bin/topic", "--concept", "spring/bean-di-basics", "--silent"])
    results["topic"] = {"rc": rc, "ms": round(ms, 1),
                          "ok": rc == 0 and ms <= 2000}  # daemon ask path

    # V6 reviewer
    rc, _, ms = _run([PY, "bin/reviewer", "--repo", "spring-roomescape-member",
                       "--reviewer-login", "hyeonic", "--top-n", "5"])
    results["reviewer"] = {"rc": rc, "ms": round(ms, 1),
                            "ok": rc == 0 and ms <= TARGETS["reviewer_ms_max"]}

    # V7 compare (warm via daemon)
    rc, _, ms = _run([PY, "bin/compare", "--repo", "spring-roomescape-member",
                       "--pr-a", "390", "--pr-b", "200", "--silent"])
    results["compare"] = {"rc": rc, "ms": round(ms, 1),
                           "ok": rc == 0 and ms <= 2000}

    # V8 compose-response (with --thread-id from a known mentor root comment in member)
    rc, stdout, ms = _run([PY, "bin/compose-response", "--repo", "spring-roomescape-member",
                            "--thread-id", "3202357560", "--silent"])
    results["compose_response"] = {"rc": rc, "ms": round(ms, 1),
                                     "ok": rc == 0 and ms <= 3000}

    # V9 mission-map summary
    rc, _, ms = _run([PY, "bin/mission-map", "--repo", "spring-roomescape-member",
                       "--summary"], timeout=15)
    results["mission_map"] = {"rc": rc, "ms": round(ms, 1),
                                "ok": rc == 0 and ms <= TARGETS["mission_map_ms_max"]}

    # V10 rag-rewrite-prepare (3 modes)
    rewrite_passes = []
    for mode in ("hyde", "decompose", "normalize"):
        rc, _, ms = _run([PY, "bin/rag-rewrite-prepare", "--mode", mode,
                           "--query", "Bean DI 깊게"])
        rewrite_passes.append((rc, ms))
    rewrite_max_ms = max(m for _, m in rewrite_passes)
    results["rag_rewrite_prepare"] = {
        "rc": 0 if all(rc == 0 for rc, _ in rewrite_passes) else 1,
        "ms": round(rewrite_max_ms, 1),
        "ok": all(rc == 0 for rc, _ in rewrite_passes)
              and rewrite_max_ms <= TARGETS["rewrite_prepare_ms_max"],
    }

    # V11 rag-route-fallback
    rc, _, ms = _run([PY, "bin/rag-route-fallback", "--prompt", "Bean DI가 뭐야"])
    results["rag_route_fallback"] = {"rc": rc, "ms": round(ms, 1),
                                       "ok": rc == 0 and ms <= TARGETS["route_fallback_ms_max"]}

    # V12 chunk-context-prepare
    rc, _, ms = _run([PY, "bin/chunk-context-prepare",
                       "--concept-id", "spring/bean-di-basics"])
    results["chunk_context_prepare"] = {"rc": rc, "ms": round(ms, 1),
                                          "ok": rc == 0 and ms <= TARGETS["chunk_context_ms_max"]}

    passed = sum(1 for r in results.values() if r["ok"])
    return {
        "targets": TARGETS,
        "results": results,
        "passed": passed, "total": len(results),
        "pass": passed == len(results),
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    summary = {n: {"ok": r["ok"], "ms": r["ms"], "rc": r["rc"]}
                 for n, r in report["results"].items()}
    print(json.dumps({"summary": summary, "passed": report["passed"],
                       "total": report["total"], "pass": report["pass"]},
                       ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
