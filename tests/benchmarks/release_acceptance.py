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
import os
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
    ("B_foundation", "corpus_rebuild_readiness", BENCH / "corpus_rebuild_readiness.py", 30),
    ("B_foundation", "rag_quality_regression", BENCH / "rag_quality_regression.py", 120),
    ("B_foundation", "gate_measurements", BENCH / "gate_measurements.py", 60),
    ("B_foundation", "cohort_qrels_eval", BENCH / "cohort_qrels_eval.py", 120),

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
    ("H_Y_integration", "daemon_latency", BENCH / "daemon_latency.py", 300),
    ("H_Y_integration", "concurrent_ask", BENCH / "concurrent_ask.py", 60),
    ("H_Y_integration", "reformulated_query_path", BENCH / "reformulated_query_path.py", 60),
    ("H_Y_integration", "auto_reformulation_path", BENCH / "auto_reformulation_path.py", 60),
    ("H_Y_integration", "exact_query_shortcut", BENCH / "exact_query_shortcut.py", 30),
    ("H_Y_integration", "profile_rebuild_projection", BENCH / "profile_rebuild_projection.py", 60),
    ("H_Y_integration", "cognitive_trigger_fsm", BENCH / "cognitive_trigger_fsm.py", 30),

    # I. Learner cycle e2e
    ("I_learner_cycle", "phase_y_learner_cycle", BENCH / "phase_y_learner_cycle.py", 60),

    # B. Legacy comparison (slow last)
    ("B_foundation", "full_scenario_comparison", BENCH / "full_scenario_comparison.py", 300),

    # M/N/P uncovered scenarios
    ("B_foundation", "uncovered_scenarios", BENCH / "uncovered_scenarios.py", 120),
    ("B_foundation", "uncovered_scenarios_phase_n", BENCH / "uncovered_scenarios_phase_n.py", 480),
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
    extra_args = []
    if name == "daemon_latency":
        # Keep L1/L2 visible in the canonical report. They are final-superiority
        # audit metrics, not v1.0.1 release-ready gates yet.
        extra_args = [
            "--warm-socket", "5",
            "--warm-cli", "5",
            "--cold-start", "3",
            "--first-ask", "3",
            "--cold-timeout-s", "45",
        ]
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            [sys.executable, str(path), *extra_args],
            cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "WOOWA_SESSION_MODE": "development"},
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

    non_executable_wrappers = []
    for p in sorted((REPO_ROOT / "bin").iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            first_line = p.open(encoding="utf-8", errors="replace").readline()
        except OSError:
            first_line = ""
        if first_line.startswith("#!") and not os.access(p, os.X_OK):
            non_executable_wrappers.append(str(p.relative_to(REPO_ROOT)))
    out.append({
        "category": "J_architecture", "name": "bin_wrappers_executable",
        "observed": non_executable_wrappers,
        "pass": len(non_executable_wrappers) == 0,
    })

    # Runtime LOC under budget
    def _loc(d: str) -> int:
        n = 0
        for p in (REPO_ROOT / d).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            n += sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
        return n
    # Same runtime dir set as the primary pytest gate (test_phase8_code_metrics):
    # scripts/ is Mode B maintainer tooling, not learner runtime, so it is not
    # part of the runtime LOC budget. Budget is a soft generous ceiling, not a
    # hard trip-wire.
    total_loc = sum(_loc(d) for d in
                     ("core", "rag", "mission", "anchors", "curation"))
    out.append({
        "category": "J_architecture", "name": "runtime_loc",
        "observed": total_loc, "budget_max": 9700,
        "pass": total_loc <= 9700,
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

    archive = REPO_ROOT / "state" / "index.tar.zst"
    archive_report: dict = {}
    archive_ok = False
    if archive.exists():
        r = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "bin" / "index-pack"),
                "--verify-only",
                "--archive",
                str(archive),
                "--json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        try:
            archive_report = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            archive_report = {"stdout_tail": (r.stdout or "")[-200:]}
        archive_ok = r.returncode == 0 and archive_report.get("pass") is True
    out.append({
        "category": "J_architecture",
        "name": "artifact_index_release_archive",
        "path": str(archive.relative_to(REPO_ROOT)),
        "observed": archive_report,
        "pass": archive.exists() and archive_ok,
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


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _y13_gate_checks() -> list[dict]:
    """Y13 quality/performance/latency gates over benchmark reports."""
    out: list[dict] = []

    rag_report = _read_json(REPO_ROOT / "reports" / "rag_quality_regression.json")
    rag = (rag_report or {}).get("current_measurement", {})
    rag_gates = [
        ("rag_top1_strict", rag.get("top1_strict_match_rate"), 0.80, ">="),
        ("rag_ndcg_at_5", rag.get("ndcg_at_5"), 0.78, ">="),
        ("rag_latency_p95_ms", rag.get("latency_ms_p95"), 500.0, "<="),
        ("rag_errors", rag.get("errors_n"), 0, "=="),
    ]
    for name, observed, threshold, op in rag_gates:
        if op == ">=":
            passed = observed is not None and observed >= threshold
        elif op == "<=":
            passed = observed is not None and observed <= threshold
        else:
            passed = observed == threshold
        out.append({
            "category": "K_Y13_gates",
            "name": name,
            "observed": observed,
            "threshold": threshold,
            "op": op,
            "pass": passed,
        })

    concurrent = _read_json(REPO_ROOT / "reports" / "concurrent_ask.json")
    if concurrent is not None:
        out.extend([
            {
                "category": "K_Y13_gates",
                "name": "concurrent_ask_lost_events",
                "observed": concurrent.get("lost_events"),
                "threshold": 0,
                "op": "==",
                "pass": concurrent.get("lost_events") == 0,
            },
            {
                "category": "K_Y13_gates",
                "name": "concurrent_ask_p95_ms",
                "observed": (concurrent.get("latency_ms") or {}).get("p95"),
                "threshold": 1500.0,
                "op": "<=",
                "pass": ((concurrent.get("latency_ms") or {}).get("p95") or 10**9) <= 1500.0,
            },
        ])
    else:
        out.append({
            "category": "K_Y13_gates",
            "name": "concurrent_ask_report_present",
            "path": "reports/concurrent_ask.json",
            "pass": False,
        })

    latency = _read_json(REPO_ROOT / "reports" / "y13_latency_baseline.json")
    layers = (latency or {}).get("layers", {})
    latency_gates = [
        ("first_ask_p95_ms", ("first_ask", "p95_ms"), 1500.0),
        ("first_answer_total_p95_ms", ("first_answer_total", "p95_ms"), 30000.0),
        ("warm_socket_p95_ms", ("warm_socket", "p95_ms"), 200.0),
        ("warm_cli_p95_ms", ("warm_cli", "p95_ms"), 400.0),
    ]
    for name, (layer_name, key), threshold in latency_gates:
        observed = (layers.get(layer_name) or {}).get(key)
        out.append({
            "category": "K_Y13_gates",
            "name": name,
            "observed": observed,
            "threshold": threshold,
            "op": "<=",
            "pass": observed is not None and observed <= threshold,
        })

    cohort_report = _read_json(REPO_ROOT / "reports" / "cohort_y13_baseline.json")
    cohort = (cohort_report or {}).get("summary", {})
    cohort_gates = [
        ("cohort_qrels_top1", cohort.get("top1_match_rate"), 0.83, ">="),
        ("cohort_qrels_ndcg_at_5", cohort.get("ndcg_at_5"), 0.91, ">="),
        ("cohort_qrels_mrr", cohort.get("mrr"), 0.90, ">="),
        (
            "cohort_qrels_learner_top1",
            cohort.get("learner_top1_match_rate"),
            0.86,
            ">=",
        ),
        ("cohort_qrels_p95_ms", cohort.get("latency_p95_ms"), 500.0, "<="),
        ("cohort_qrels_errors", cohort.get("errors_n"), 0, "=="),
    ]
    for name, observed, threshold, op in cohort_gates:
        if op == ">=":
            passed = observed is not None and observed >= threshold
        elif op == "<=":
            passed = observed is not None and observed <= threshold
        else:
            passed = observed == threshold
        out.append({
            "category": "K_Y13_gates",
            "name": name,
            "observed": observed,
            "threshold": threshold,
            "op": op,
            "pass": passed,
        })

    cohort_legacy_report = _read_json(REPO_ROOT / "reports" / "cohort_y13_vs_legacy.json")
    cohort_legacy = (cohort_legacy_report or {}).get("legacy", {})
    cohort_legacy_sha = (cohort_legacy_report or {}).get("qrel_sha256")
    cohort_sha = cohort.get("qrel_sha256")
    legacy_qrels_p50 = cohort_legacy.get("latency_p50_ms")
    legacy_qrels_p95 = cohort_legacy.get("latency_p95_ms")
    current_qrels_p50 = cohort.get("latency_p50_ms")
    current_qrels_p95 = cohort.get("latency_p95_ms")
    current_qrels_mrr = cohort.get("mrr")
    legacy_qrels_mrr = cohort_legacy.get("mrr_active")
    out.extend([
        {
            "category": "K_Y13_gates",
            "name": "cohort_qrels_legacy_snapshot_same_fixture",
            "observed": {"current": cohort_sha, "legacy": cohort_legacy_sha},
            "threshold": "same qrel sha",
            "op": "==",
            "pass": bool(cohort_sha and cohort_sha == cohort_legacy_sha),
        },
        {
            "category": "K_Y13_gates",
            "name": "cohort_qrels_mrr_vs_legacy",
            "observed": {"v2": current_qrels_mrr, "legacy": legacy_qrels_mrr},
            "threshold": "v2>=legacy",
            "op": ">=",
            "pass": (
                current_qrels_mrr is not None
                and legacy_qrels_mrr is not None
                and current_qrels_mrr >= legacy_qrels_mrr
            ),
        },
        {
            "category": "K_Y13_gates",
            "name": "cohort_qrels_p50_vs_legacy",
            "observed": {"v2": current_qrels_p50, "legacy": legacy_qrels_p50},
            "threshold": "v2<=legacy",
            "op": "<=",
            "pass": (
                current_qrels_p50 is not None
                and legacy_qrels_p50 is not None
                and current_qrels_p50 <= legacy_qrels_p50
            ),
        },
        {
            "category": "K_Y13_gates",
            "name": "cohort_qrels_p95_vs_legacy",
            "observed": {"v2": current_qrels_p95, "legacy": legacy_qrels_p95},
            "threshold": "v2<=legacy",
            "op": "<=",
            "pass": (
                current_qrels_p95 is not None
                and legacy_qrels_p95 is not None
                and current_qrels_p95 <= legacy_qrels_p95
            ),
        },
    ])

    full_compare = _read_json(REPO_ROOT / "reports" / "v2_vs_legacy_full_comparison.json")
    full_v2 = ((full_compare or {}).get("summary") or {}).get("paradigm_v2", {})
    full_legacy = ((full_compare or {}).get("summary") or {}).get("legacy", {})
    full_comparison = ((full_compare or {}).get("summary") or {}).get("comparison", {})
    full_gates = [
        # Absolute guardrails catch pathological regressions; superiority is
        # enforced by the speedup and v2<=legacy gates below.
        ("full_scenario_p50_ms", full_v2.get("p50_overall_ms"), 100.0, "<="),
        ("full_scenario_p95_ms", full_v2.get("p95_overall_ms"), 150.0, "<="),
        ("full_scenario_speedup_p50", full_comparison.get("speedup_p50"), 1.2, ">="),
        ("full_scenario_speedup_p95", full_comparison.get("speedup_p95"), 1.2, ">="),
        (
            "full_scenario_evidence_coverage_pct",
            full_v2.get("evidence_coverage_pct"),
            90.0,
            ">=",
        ),
        ("full_scenario_mode_dispatch", full_v2.get("mode_dispatch_correct"), "14/14", "=="),
    ]
    for name, observed, threshold, op in full_gates:
        if op == ">=":
            passed = observed is not None and observed >= threshold
        elif op == "<=":
            passed = observed is not None and observed <= threshold
        else:
            passed = observed == threshold
        out.append({
            "category": "K_Y13_gates",
            "name": name,
            "observed": observed,
            "threshold": threshold,
            "op": op,
            "pass": passed,
        })
    v2_p95 = full_v2.get("p95_overall_ms")
    legacy_p95 = full_legacy.get("p95_overall_ms")
    out.append({
        "category": "K_Y13_gates",
        "name": "full_scenario_p95_vs_legacy",
        "observed": {"v2": v2_p95, "legacy": legacy_p95},
        "threshold": "v2<=legacy",
        "op": "<=",
        "pass": v2_p95 is not None and legacy_p95 is not None and v2_p95 <= legacy_p95,
    })

    reform_report = _read_json(REPO_ROOT / "reports" / "reformulated_query_path.json")
    reform = (reform_report or {}).get("reformulated", {})
    raw = (reform_report or {}).get("raw", {})
    reform_checks = (reform_report or {}).get("checks", {})
    out.extend([
        {
            "category": "K_Y13_gates",
            "name": "reformulated_query_raw_fallback",
            "observed": raw.get("modes"),
            "threshold": ["tier_0_fallback"],
            "op": "==",
            "pass": reform_checks.get("raw_falls_back_without_citations") is True,
        },
        {
            "category": "K_Y13_gates",
            "name": "reformulated_query_routes_cs_qa",
            "observed": reform.get("modes"),
            "threshold": ["cs_qa"],
            "op": "==",
            "pass": reform_checks.get("reformulated_routes_to_cs_qa") is True,
        },
        {
            "category": "K_Y13_gates",
            "name": "reformulated_query_citations",
            "observed": reform.get("citation_counts"),
            "threshold": "min>=1",
            "op": ">=",
            "pass": reform_checks.get("reformulated_has_citations") is True,
        },
        {
            "category": "K_Y13_gates",
            "name": "reformulated_query_p95_ms",
            "observed": reform.get("p95_ms"),
            "threshold": 500.0,
            "op": "<=",
            "pass": reform_checks.get("reformulated_latency_p95_budget") is True,
        },
    ])

    auto_reform_report = _read_json(REPO_ROOT / "reports" / "auto_reformulation_path.json")
    auto_checks = (auto_reform_report or {}).get("checks", {})
    auto_decision_latency = (
        (auto_reform_report or {}).get("decision_latency_ms") or {}
    )
    auto_search_latency = (
        (auto_reform_report or {}).get("search_latency_ms") or {}
    )
    out.extend([
        {
            "category": "K_Y13_gates",
            "name": "auto_reformulation_quality",
            "observed": {
                "routes_cs_qa": auto_checks.get("auto_query_routes_cs_qa"),
                "top1": auto_checks.get("auto_query_recovers_prior_top1"),
                "legacy_shape": auto_checks.get("legacy_same_follow_up_shape"),
            },
            "threshold": True,
            "op": "==",
            "pass": (
                auto_checks.get("auto_query_routes_cs_qa") is True
                and auto_checks.get("auto_query_recovers_prior_top1") is True
                and auto_checks.get("legacy_same_follow_up_shape") is True
            ),
        },
        {
            "category": "K_Y13_gates",
            "name": "auto_reformulation_decision_p95_ms",
            "observed": auto_decision_latency.get("p95_ms"),
            "threshold": 5.0,
            "op": "<=",
            "pass": (auto_decision_latency.get("p95_ms") or 10**9) <= 5.0,
        },
        {
            "category": "K_Y13_gates",
            "name": "auto_reformulation_search_p95_ms",
            "observed": auto_search_latency.get("p95_ms"),
            "threshold": 500.0,
            "op": "<=",
            "pass": (auto_search_latency.get("p95_ms") or 10**9) <= 500.0,
        },
    ])

    exact_shortcut_report = _read_json(REPO_ROOT / "reports" / "exact_query_shortcut.json")
    exact_shortcut_checks = (exact_shortcut_report or {}).get("checks", {})
    exact_shortcut_latency = (exact_shortcut_report or {}).get("latency_ms", {})
    out.extend([
        {
            "category": "K_Y13_gates",
            "name": "exact_query_shortcut_quality",
            "observed": {
                "top1": exact_shortcut_checks.get("all_cases_top1"),
                "ambiguous": exact_shortcut_checks.get("ambiguous_alias_falls_through"),
            },
            "threshold": True,
            "op": "==",
            "pass": (
                exact_shortcut_checks.get("all_cases_top1") is True
                and exact_shortcut_checks.get("ambiguous_alias_falls_through") is True
            ),
        },
        {
            "category": "K_Y13_gates",
            "name": "exact_query_shortcut_encoder_not_called",
            "observed": exact_shortcut_checks.get("encoder_not_called_for_exact"),
            "threshold": True,
            "op": "==",
            "pass": exact_shortcut_checks.get("encoder_not_called_for_exact") is True,
        },
        {
            "category": "K_Y13_gates",
            "name": "exact_query_shortcut_p95_ms",
            "observed": exact_shortcut_latency.get("p95_ms"),
            "threshold": 30.0,
            "op": "<=",
            "pass": (exact_shortcut_latency.get("p95_ms") or 10**9) <= 30.0,
        },
    ])

    profile_rebuild_report = _read_json(
        REPO_ROOT / "reports" / "profile_rebuild_projection.json"
    )
    profile_rebuild_checks = (profile_rebuild_report or {}).get("checks", {})
    direct_rebuild_latency = (
        (profile_rebuild_report or {}).get("direct_latency_ms") or {}
    )
    cli_rebuild_latency = (
        (profile_rebuild_report or {}).get("cli_latency_ms") or {}
    )
    out.extend([
        {
            "category": "K_Y13_gates",
            "name": "profile_rebuild_quality",
            "observed": {
                "schema": profile_rebuild_checks.get("profile_schema_preserved"),
                "projection": profile_rebuild_checks.get("projection_written"),
                "legacy": profile_rebuild_checks.get("legacy_cs_view_same"),
            },
            "threshold": True,
            "op": "==",
            "pass": (
                profile_rebuild_checks.get("profile_schema_preserved") is True
                and profile_rebuild_checks.get("projection_written") is True
                and profile_rebuild_checks.get("legacy_cs_view_same") is True
            ),
        },
        {
            "category": "K_Y13_gates",
            "name": "profile_rebuild_direct_p95_ms",
            "observed": direct_rebuild_latency.get("p95_ms"),
            "threshold": 10_000.0,
            "op": "<=",
            "pass": (direct_rebuild_latency.get("p95_ms") or 10**9) <= 10_000.0,
        },
        {
            "category": "K_Y13_gates",
            "name": "profile_rebuild_cli_p95_ms",
            "observed": cli_rebuild_latency.get("p95_ms"),
            "threshold": 30_000.0,
            "op": "<=",
            "pass": (cli_rebuild_latency.get("p95_ms") or 10**9) <= 30_000.0,
        },
    ])

    phase_w_report = _read_json(REPO_ROOT / "reports" / "phase_w_wrappers_bench.json")
    phase_w_results = (phase_w_report or {}).get("results", {})
    mining_gates = [
        (
            "feedback_mine_since_p95_ms",
            ("feedback_mine", "p95_ms"),
            1000.0,
        ),
        (
            "feedback_mine_since_max_ms",
            ("feedback_mine", "max_ms"),
            1500.0,
        ),
        (
            "response_quality_mine_since_p95_ms",
            ("response_quality_mine", "p95_ms"),
            600.0,
        ),
        (
            "response_quality_mine_since_max_ms",
            ("response_quality_mine", "max_ms"),
            1000.0,
        ),
    ]
    for name, (result_name, key), threshold in mining_gates:
        observed = (phase_w_results.get(result_name) or {}).get(key)
        out.append({
            "category": "K_Y13_gates",
            "name": name,
            "observed": observed,
            "threshold": threshold,
            "op": "<=",
            "pass": observed is not None and observed <= threshold,
        })

    trigger_report = _read_json(REPO_ROOT / "reports" / "cognitive_trigger_fsm.json")
    trigger_latency = (trigger_report or {}).get("latency", {})
    trigger_checks = (trigger_report or {}).get("checks", {})
    out.extend([
        {
            "category": "K_Y13_gates",
            "name": "cognitive_trigger_quality",
            "observed": trigger_checks.get("quality_cases_pass"),
            "threshold": True,
            "op": "==",
            "pass": trigger_checks.get("quality_cases_pass") is True,
        },
        {
            "category": "K_Y13_gates",
            "name": "cognitive_trigger_p50_ms",
            "observed": trigger_latency.get("p50_ms"),
            "threshold": 3.0,
            "op": "<=",
            "pass": (trigger_latency.get("p50_ms") or 10**9) <= 3.0,
        },
        {
            "category": "K_Y13_gates",
            "name": "cognitive_trigger_p95_ms",
            "observed": trigger_latency.get("p95_ms"),
            "threshold": 5.0,
            "op": "<=",
            "pass": (trigger_latency.get("p95_ms") or 10**9) <= 5.0,
        },
    ])
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

    # K. Y13 gates
    print("[K] Y13 gates...", flush=True)
    results.extend(_y13_gate_checks())

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
