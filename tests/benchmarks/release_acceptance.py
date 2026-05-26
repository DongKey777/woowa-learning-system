"""tests/benchmarks/release_acceptance.py — final release gate.

Runs every measurable verification in paradigm-v2 sequentially and
aggregates pass/fail into a single acceptance report.

Categories:
  A. Unit test suite (319+ tests)
  B. Phase J/K/L/M/N/P (foundation benches)
  C. Phase T learner automation (7 wrappers + e2e integration)
  D. Phase U onboarding/collection (10 wrappers + collection layer)
  E. Phase V coaching context (12 wrappers)
  F. Phase W mining/analytics (12 wrappers)
  G. Phase X maintenance + sub-commands (10 wrappers)
  H. Phase Y final integration + 5 hidden gaps + concurrent append
  I. Learner cycle e2e (real-world flow)
  J. Architectural assumption checks (LOC budget / wrapper count / docs)

Acceptance criteria for v1.0.1: ALL categories pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH = REPO_ROOT / "tests" / "benchmarks"
REPORT_PATH = REPO_ROOT / "reports" / "release_acceptance_v1.0.1.json"

# (category, name, bench_path, timeout_s)
SUITE = [
    # B. Foundation benches
    ("B_foundation", "rag_quality_regression", BENCH / "rag_quality_regression.py", 120),
    ("B_foundation", "gate_measurements", BENCH / "gate_measurements.py", 60),

    # C. Phase T learner automation
    ("C_T_learner", "learn_pr_retro_bench", BENCH / "learn_pr_retro_bench.py", 30),
    ("C_T_learner", "learn_record_code_bench", BENCH / "learn_record_code_bench.py", 30),
    ("C_T_learner", "learn_test_bench", BENCH / "learn_test_bench.py", 30),
    ("C_T_learner", "learn_response_quality_bench", BENCH / "learn_response_quality_bench.py", 30),
    ("C_T_learner", "assess_learner_state_bench", BENCH / "assess_learner_state_bench.py", 60),
    ("C_T_learner", "profile_recompute_bench", BENCH / "profile_recompute_bench.py", 30),
    ("C_T_learner", "session_start_bench", BENCH / "session_start_bench.py", 60),
    ("C_T_learner", "phase_t_e2e_integration", BENCH / "phase_t_e2e_integration.py", 60),

    # D. Phase U onboarding
    ("D_U_onboarding", "phase_u_wrappers_bench", BENCH / "phase_u_wrappers_bench.py", 60),

    # E. Phase V coaching context
    ("E_V_coaching", "phase_v_wrappers_bench", BENCH / "phase_v_wrappers_bench.py", 120),

    # F. Phase W mining
    ("F_W_mining", "phase_w_wrappers_bench", BENCH / "phase_w_wrappers_bench.py", 180),

    # G. Phase X maintenance
    ("G_X_maintenance", "phase_x_wrappers_bench", BENCH / "phase_x_wrappers_bench.py", 60),

    # H. Phase Y integration + gaps
    ("H_Y_integration", "phase_y7_sync_prs_chain", BENCH / "phase_y7_sync_prs_chain.py", 60),
    ("H_Y_integration", "phase_y8_fresh_clone_sim", BENCH / "phase_y8_fresh_clone_sim.py", 60),
    ("H_Y_integration", "phase_y8_concurrent_append", BENCH / "phase_y8_concurrent_append.py", 60),

    # I. Learner cycle e2e
    ("I_learner_cycle", "phase_y_learner_cycle", BENCH / "phase_y_learner_cycle.py", 60),

    # B. Legacy comparison (slow last)
    ("B_foundation", "full_scenario_comparison", BENCH / "full_scenario_comparison.py", 300),

    # M/N/P uncovered scenarios
    ("B_foundation", "uncovered_scenarios", BENCH / "uncovered_scenarios.py", 120),
    ("B_foundation", "uncovered_scenarios_phase_n", BENCH / "uncovered_scenarios_phase_n.py", 300),
    ("B_foundation", "deep_scenarios_phase_p", BENCH / "deep_scenarios_phase_p.py", 60),
]


def _run_unit_suite() -> dict:
    """A. Run full pytest suite."""
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    elapsed = (time.perf_counter() - t0) * 1000
    # Parse "N passed in X" or "N failed, M passed"
    last = r.stdout.strip().split("\n")[-1] if r.stdout else ""
    return {
        "category": "A_unit_suite",
        "name": "pytest_all",
        "rc": r.returncode,
        "ms": round(elapsed, 1),
        "summary": last[:120],
        "pass": r.returncode == 0,
    }


def _run_bench(category: str, name: str, path: Path, timeout: int) -> dict:
    if not path.exists():
        return {"category": category, "name": name, "rc": -1, "ms": 0,
                "missing": True, "pass": False}
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            [sys.executable, str(path)],
            cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=timeout,
            env={**__import__("os").environ,
                 "WOOWA_SESSION_MODE": "development"},
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "category": category, "name": name,
            "rc": r.returncode, "ms": round(elapsed, 1),
            "stdout_tail": (r.stdout or "")[-200:],
            "stderr_tail": (r.stderr or "")[-200:],
            "pass": r.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"category": category, "name": name, "rc": 124, "ms": -1,
                "timeout": True, "pass": False}


def _architectural_checks() -> list[dict]:
    """J. Validate architectural assumptions remain true."""
    out: list[dict] = []

    # bin/* count
    bin_n = len([p for p in (REPO_ROOT / "bin").iterdir()
                  if p.is_file() and not p.name.startswith(".")])
    out.append({
        "category": "J_architecture", "name": "wrapper_count",
        "observed": bin_n, "expected_min": 63,
        "pass": bin_n >= 63,
    })

    # Runtime LOC under budget
    def _loc(d: str) -> int:
        n = 0
        for p in (REPO_ROOT / d).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            n += sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
        return n
    total_loc = sum(_loc(d) for d in
                     ("core", "rag", "mission", "anchors", "curation", "scripts"))
    out.append({
        "category": "J_architecture", "name": "runtime_loc",
        "observed": total_loc, "budget_max": 9500,
        "pass": total_loc <= 9500,
    })

    # Critical artifacts present
    artifacts = [
        ("corpus_concept_graph", REPO_ROOT / "corpus" / "concept_graph.json"),
        ("lance_index", REPO_ROOT / "state" / "index" / "concepts.lance"),
        ("learner_history", REPO_ROOT / "state" / "learner" / "history.jsonl"),
        ("learner_identity", REPO_ROOT / "state" / "learner" / "identity.json"),
        ("mastery_graph", REPO_ROOT / "state" / "learner" / "mastery_graph.sqlite"),
    ]
    for name, p in artifacts:
        out.append({
            "category": "J_architecture", "name": f"artifact_{name}",
            "path": str(p.relative_to(REPO_ROOT)),
            "pass": p.exists(),
        })

    # Docs sanity
    docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "CLAUDE.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "docs" / "architecture.md",
        REPO_ROOT / "docs" / "bin-reference.md",
        REPO_ROOT / "docs" / "onboarding.md",
        REPO_ROOT / "docs" / "verification-results.md",
    ]
    for d in docs:
        out.append({
            "category": "J_architecture", "name": f"doc_{d.name}",
            "path": str(d.relative_to(REPO_ROOT)),
            "size_bytes": d.stat().st_size if d.exists() else 0,
            "pass": d.exists() and d.stat().st_size > 500,
        })

    # No legacy hub dependency in runtime code
    import subprocess as sp
    r = sp.run(["grep", "-rln", "woowa-learning-hub",
                  str(REPO_ROOT / "core"),
                  str(REPO_ROOT / "scripts"),
                  str(REPO_ROOT / "anchors"),
                  str(REPO_ROOT / "mission"),
                  str(REPO_ROOT / "rag"),
                  str(REPO_ROOT / "curation")],
                capture_output=True, text=True)
    runtime_legacy_refs = [l for l in r.stdout.splitlines()
                           if "__pycache__" not in l]
    out.append({
        "category": "J_architecture", "name": "zero_legacy_hub_dep",
        "observed": runtime_legacy_refs,
        "pass": len(runtime_legacy_refs) == 0,
    })

    return out


def main():
    print("=== Release v1.0.1 acceptance bench ===\n", flush=True)
    results: list[dict] = []

    # A. unit suite
    print("[A] pytest...", flush=True)
    results.append(_run_unit_suite())

    # B-I. all phase benches
    for cat, name, path, to in SUITE:
        print(f"[{cat[0]}] {name}...", flush=True)
        results.append(_run_bench(cat, name, path, to))

    # J. architectural checks
    print("[J] architectural checks...", flush=True)
    results.extend(_architectural_checks())

    # Aggregate per category
    from collections import defaultdict
    cat_summary = defaultdict(lambda: {"pass": 0, "fail": 0, "items": []})
    for r in results:
        c = r["category"]
        if r.get("pass"):
            cat_summary[c]["pass"] += 1
        else:
            cat_summary[c]["fail"] += 1
        cat_summary[c]["items"].append({
            "name": r["name"], "pass": r.get("pass", False),
            "ms": r.get("ms"), "rc": r.get("rc"),
        })

    total_pass = sum(1 for r in results if r.get("pass"))
    total = len(results)
    overall_pass = total_pass == total

    report = {
        "version_target": "v1.0.1",
        "timestamp": time.time(),
        "results": results,
        "per_category": dict(cat_summary),
        "totals": {"pass": total_pass, "total": total},
        "release_ready": overall_pass,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # Compact stdout summary
    print("\n=== ACCEPTANCE REPORT ===")
    for cat, s in sorted(cat_summary.items()):
        marker = "✅" if s["fail"] == 0 else "❌"
        print(f"  {marker} {cat:<25} {s['pass']}/{s['pass'] + s['fail']}")
        for item in s["items"]:
            mk = "✓" if item["pass"] else "✗"
            ms = f"{item['ms']}ms" if item.get('ms', 0) and item['ms'] > 0 else ""
            print(f"      {mk} {item['name']:<40} {ms}")
    print(f"\n  TOTAL: {total_pass}/{total}  ({'RELEASE READY ✅' if overall_pass else 'BLOCKED ❌'})")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
