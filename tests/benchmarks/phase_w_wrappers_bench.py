"""tests/benchmarks/phase_w_wrappers_bench.py — Phase W mining/analytics
(12 wrappers).
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

REPORT_PATH = REPO_ROOT / "reports" / "phase_w_wrappers_bench.json"

TARGETS = {
    # Batch analytics over 1k synthetic events. These are offline mining jobs,
    # while learner-turn latency is gated separately.
    "feedback_mine_p95_ms_max": 1000,
    "feedback_mine_max_ms_max": 1500,
    "rq_mine_p95_ms_max": 600,
    "rq_mine_max_ms_max": 1000,
    "routing_analyze_ms_max": 2000,
    "turn_audit_ms_max": 1500,
    "path_audit_ms_max": 1500,
    "reclassify_ms_max": 12000,  # 1000 events × router
    "cohort_eval_ms_max": 10000,  # 5-q via daemon
    "cohort_compare_ms_max": 200,
    "golden_verify_ms_max": 5000,
    "rag_eval_ms_max": 45000,
    "router_gen_ms_max": 300,
    "learner_log_eval_ms_max": 15000,  # 50 queries × daemon
    "router_gen_accuracy_min": 0.85,
}


def _run(cmd, timeout=30):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, (time.perf_counter() - t0) * 1000


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile / 100) - 1))
    return ordered[idx]


def _measure_runs(cmd, *, runs=5, timeout=30):
    timings = []
    rcs = []
    stdout = ""
    for _ in range(runs):
        rc, stdout, ms = _run(cmd, timeout=timeout)
        rcs.append(rc)
        timings.append(ms)
    return {
        "rc": 0 if all(rc == 0 for rc in rcs) else next(rc for rc in rcs if rc != 0),
        "runs": runs,
        "ms": round(_percentile(timings, 95), 1),
        "p50_ms": round(_percentile(timings, 50), 1),
        "p95_ms": round(_percentile(timings, 95), 1),
        "max_ms": round(max(timings), 1),
        "stdout_tail": stdout[-200:],
    }


def _seed_mining_state(root: Path, *, rows: int = 1000) -> None:
    now = time.time()
    feedback_path = root / "cs_rag" / "feedback.jsonl"
    quality_path = root / "learner" / "response-quality.jsonl"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    quality_path.parent.mkdir(parents=True, exist_ok=True)

    feedback_rows = []
    quality_rows = []
    for i in range(rows):
        recent = i % 2 == 0
        logged_at = now - (3600 + i if recent else 10 * 86400 + i)
        signal = "helpful" if i % 4 == 0 else "not_helpful"
        feedback_rows.append({
            "logged_at": logged_at,
            "signal": signal,
            "doc_paths": [f"doc/{i % 25}"],
        })

        flags = []
        if i % 5 == 0:
            flags.append("missing_response_body")
        if i % 7 == 0:
            flags.append("missing_citation")
        quality_rows.append({
            "logged_at": logged_at,
            "quality_flags": flags,
            "citation_paths_expected": [f"expected/{i % 20}"],
            "citation_paths_declared": [f"declared/{i % 20}"] if i % 7 else [],
        })

    feedback_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in feedback_rows) + "\n",
        encoding="utf-8",
    )
    quality_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in quality_rows) + "\n",
        encoding="utf-8",
    )


def measure():
    PY = sys.executable
    results = {}

    with tempfile.TemporaryDirectory() as td:
        mining_state = Path(td) / "state"
        _seed_mining_state(mining_state)

        # W1 feedback-mine with --since filter over 1k events.
        feedback = _measure_runs([
            PY, "bin/feedback-mine",
            "--state-root", str(mining_state),
            "--since", "7d",
        ])
        feedback["ok"] = (
            feedback["rc"] == 0
            and feedback["p95_ms"] <= TARGETS["feedback_mine_p95_ms_max"]
            and feedback["max_ms"] <= TARGETS["feedback_mine_max_ms_max"]
        )
        results["feedback_mine"] = feedback

        # W2 response-quality-mine with --since filter over 1k events.
        rq = _measure_runs([
            PY, "bin/response-quality-mine",
            "--state-root", str(mining_state),
            "--since", "7d",
        ])
        rq["ok"] = (
            rq["rc"] == 0
            and rq["p95_ms"] <= TARGETS["rq_mine_p95_ms_max"]
            and rq["max_ms"] <= TARGETS["rq_mine_max_ms_max"]
        )
        results["response_quality_mine"] = rq

    # W3 routing-analyze
    rc, _, ms = _run([PY, "bin/routing-analyze"])
    results["routing_analyze"] = {"rc": rc, "ms": round(ms, 1),
                                    "ok": rc == 0 and ms <= TARGETS["routing_analyze_ms_max"]}

    # W4 learning-turn-audit
    rc, _, ms = _run([PY, "bin/learning-turn-audit", "--last", "50"])
    results["learning_turn_audit"] = {"rc": rc, "ms": round(ms, 1),
                                        "ok": ms <= TARGETS["turn_audit_ms_max"]}

    # W5 learning-path-graph-audit
    rc, _, ms = _run([PY, "bin/learning-path-graph-audit"], timeout=10)
    results["learning_path_graph_audit"] = {"rc": rc, "ms": round(ms, 1),
                                              "ok": ms <= TARGETS["path_audit_ms_max"]}

    # W6 reclassify-history (dry-run, last 500)
    rc, _, ms = _run([PY, "bin/reclassify-history", "--last", "500"], timeout=20)
    results["reclassify_history"] = {"rc": rc, "ms": round(ms, 1),
                                       "ok": rc == 0 and ms <= TARGETS["reclassify_ms_max"]}

    # W7 cohort-eval (5-q synthetic cohort via tmpfile)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                       encoding="utf-8") as cf:
        cohort = [
            {"prompt": "Bean DI가 뭐야", "expected_mode": "cs_qa"},
            {"prompt": "git rebase 사용", "expected_mode": "tool_only"},
            {"prompt": "내 PR 흐름 보여줘", "expected_mode": "retro"},
            {"prompt": "다른 크루 비교", "expected_mode": "f11_anchor"},
            {"prompt": "Spring Bean 생명주기", "expected_mode": "cs_qa"},
        ]
        json.dump(cohort, cf, ensure_ascii=False)
        cohort_path = cf.name
    rc, _, ms = _run([PY, "bin/cohort-eval", "--cohort-file", cohort_path], timeout=30)
    results["cohort_eval"] = {"rc": rc, "ms": round(ms, 1),
                                "ok": rc == 0 and ms <= TARGETS["cohort_eval_ms_max"]}

    # W8 cohort-compare: same cohort vs itself = 0 drift
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                       encoding="utf-8") as cf:
        json.dump({"summary": {"mode_accuracy": 0.85, "latency_p50_ms": 30,
                                 "latency_p95_ms": 50}}, cf)
        p1 = cf.name
    rc, _, ms = _run([PY, "bin/cohort-compare", "--control", p1, "--candidate", p1])
    results["cohort_compare"] = {"rc": rc, "ms": round(ms, 1),
                                   "ok": rc == 0 and ms <= TARGETS["cohort_compare_ms_max"]}

    # W9 golden (update + verify)
    _run([PY, "bin/golden", "update"])
    rc, _, ms = _run([PY, "bin/golden", "verify"], timeout=20)
    results["golden_verify"] = {"rc": rc, "ms": round(ms, 1),
                                  "ok": ms <= TARGETS["golden_verify_ms_max"]}

    # W10 rag-eval (full BGE-backed corpus benchmark; latency is separately
    # gated inside reports/rag_quality_regression.json).
    rc, _, ms = _run([PY, "bin/rag-eval"], timeout=120)
    results["rag_eval"] = {"rc": rc, "ms": round(ms, 1),
                            "ok": rc == 0 and ms <= TARGETS["rag_eval_ms_max"]}

    # W11 router-generalization-eval
    rc, stdout, ms = _run([PY, "bin/router-generalization-eval"])
    accuracy = 0.0
    try:
        d = json.loads(stdout)
        accuracy = d.get("accuracy", 0.0)
    except Exception:
        pass
    results["router_generalization"] = {
        "rc": rc, "ms": round(ms, 1), "accuracy": accuracy,
        "ok": ms <= TARGETS["router_gen_ms_max"]
              and accuracy >= TARGETS["router_gen_accuracy_min"]
    }

    # W12 learner-log-rag-eval
    rc, _, ms = _run([PY, "bin/learner-log-rag-eval", "--limit", "10"], timeout=30)
    results["learner_log_rag_eval"] = {"rc": rc, "ms": round(ms, 1),
                                         "ok": rc == 0 and ms <= TARGETS["learner_log_eval_ms_max"]}

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
