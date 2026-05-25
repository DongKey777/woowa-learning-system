"""tests/benchmarks/phase_x_wrappers_bench.py — Phase X maintenance + sub-commands."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

REPORT_PATH = REPO_ROOT / "reports" / "phase_x_wrappers_bench.json"

TARGETS = {"max_ms": 500}


def _run(cmd, timeout=30):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, (time.perf_counter() - t0) * 1000


def measure():
    PY = sys.executable
    results = {}

    # X1 sync-index-metadata (dry-run)
    rc, _, ms = _run([PY, "bin/sync-index-metadata", "--tag",
                       "paradigm-v2-index-v9.9.9", "--dry-run"])
    results["sync_index_metadata"] = {"rc": rc, "ms": round(ms, 1),
                                        "ok": rc == 0 and ms <= 500}

    # X2 drill-grade-prepare (synthesize pending)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                       encoding="utf-8") as cf:
        json.dump({"concept_id": "spring/bean-di-basics",
                    "question": "Bean이 뭐야?",
                    "expected_terms": ["Bean", "Spring", "IoC"]}, cf)
        pending_p = cf.name
    rc, _, ms = _run([PY, "bin/drill-grade-prepare",
                       "--pending-file", pending_p, "--answer", "Bean은 객체"])
    results["drill_grade_prepare"] = {"rc": rc, "ms": round(ms, 1),
                                        "ok": rc == 0 and ms <= 200}

    # X3 learn-feedback (helpful signal, isolated tmpstate)
    with tempfile.TemporaryDirectory() as tmp:
        rc, _, ms = _run([PY, "bin/learn-feedback", "--signal", "helpful",
                           "--hit", "spring/bean-di.md", "--note", "good",
                           "--state-root", tmp, "--silent"])
        results["learn_feedback"] = {"rc": rc, "ms": round(ms, 1),
                                       "ok": rc == 0 and ms <= 200}

    # X4 learn-self-assess (no pending → reject expected, rc=1)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "learner").mkdir(parents=True)
        rc, _, ms = _run([PY, "bin/learn-self-assess", "--trigger-session-id",
                           "nope", "--score", "8", "--state-root", tmp, "--silent"])
        results["learn_self_assess_reject"] = {"rc": rc, "ms": round(ms, 1),
                                                  "ok": rc == 1 and ms <= 200}

    # X5 learn-drill status
    rc, _, ms = _run([PY, "bin/learn-drill", "status"])
    results["learn_drill_status"] = {"rc": rc, "ms": round(ms, 1),
                                       "ok": rc == 0 and ms <= 200}

    # X6 learner-profile show
    rc, _, ms = _run([PY, "bin/learner-profile", "show"])
    results["learner_profile_show"] = {"rc": rc, "ms": round(ms, 1),
                                         "ok": rc == 0 and ms <= 200}

    # X6 learner-profile recompute
    rc, _, ms = _run([PY, "bin/learner-profile", "recompute"], timeout=10)
    results["learner_profile_recompute"] = {"rc": rc, "ms": round(ms, 1),
                                              "ok": rc == 0 and ms <= 2000}

    # X7 set-profile (tmp)
    with tempfile.TemporaryDirectory() as tmp:
        rc, _, ms = _run([PY, "bin/set-profile", "--repo", "demo",
                           "--field", "experience_level", "--value", "advanced",
                           "--state-root", tmp])
        results["set_profile"] = {"rc": rc, "ms": round(ms, 1),
                                    "ok": rc == 0 and ms <= 200}

    # X8 show-profile (real state)
    rc, _, ms = _run([PY, "bin/show-profile"])
    results["show_profile"] = {"rc": rc, "ms": round(ms, 1),
                                 "ok": rc == 0 and ms <= 200}

    # X9 reviewer-profile (alias)
    rc, _, ms = _run([PY, "bin/reviewer-profile", "--repo", "spring-roomescape-member",
                       "--reviewer-login", "hyeonic", "--top-n", "3"])
    results["reviewer_profile_alias"] = {"rc": rc, "ms": round(ms, 1),
                                           "ok": rc == 0 and ms <= 1000}

    # X10 rag-remote-build dry-run (no module → exit 0 with note)
    rc, _, ms = _run([PY, "bin/rag-remote-build", "--dry-run"])
    results["rag_remote_build_dry"] = {"rc": rc, "ms": round(ms, 1),
                                         "ok": ms <= 500}

    passed = sum(1 for r in results.values() if r["ok"])
    return {
        "targets": TARGETS, "results": results,
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
