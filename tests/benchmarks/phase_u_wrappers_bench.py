"""tests/benchmarks/phase_u_wrappers_bench.py — Phase U onboarding/collection
suite bench (covers 10 new wrappers + collection layer).

Each wrapper has a target axis. Real-archive smoke + mocked collection.
Reports → reports/phase_u_wrappers_bench.json
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.collection import collect_prs  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "phase_u_wrappers_bench.json"

TARGETS = {
    "list_repos_ms_max": 80,
    "archive_status_ms_max": 200,
    "validate_state_ms_max": 600,
    "registry_audit_ms_max": 300,
    "doctor_ms_max": 2000,           # Y8 added ps subprocess for daemon RSS check
    "repo_readiness_ms_max": 200,
    "bootstrap_skip_ms_max": 10000,  # includes sentence-transformers import probe
    "onboard_clone_skip_ms_max": 500,
    "sync_prs_missing_run_exit_code": 2,
}


def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str, float]:
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                        timeout=timeout)
    return r.returncode, r.stdout, (time.perf_counter() - t0) * 1000


def measure() -> dict:
    PY = sys.executable
    results: dict[str, dict] = {}

    # 1. list-repos
    rc, _, ms = _run([PY, "bin/list-repos"])
    results["list_repos"] = {"rc": rc, "ms": round(ms, 1),
                              "ok": rc == 0 and ms <= TARGETS["list_repos_ms_max"]}

    # 2. archive-status all
    rc, _, ms = _run([PY, "bin/archive-status"])
    results["archive_status"] = {"rc": rc, "ms": round(ms, 1),
                                  "ok": rc == 0 and ms <= TARGETS["archive_status_ms_max"]}

    # 3. validate-state
    rc, _, ms = _run([PY, "bin/validate-state"])
    results["validate_state"] = {"rc": rc, "ms": round(ms, 1),
                                  "ok": rc == 0 and ms <= TARGETS["validate_state_ms_max"]}

    # 4. registry-audit (may exit 1 if orphans — measure latency only)
    rc, _, ms = _run([PY, "bin/registry-audit"])
    results["registry_audit"] = {"rc": rc, "ms": round(ms, 1),
                                  "ok": ms <= TARGETS["registry_audit_ms_max"]}

    # 5. doctor (rc 0 if healthy; latency check only)
    rc, _, ms = _run([PY, "bin/doctor"], timeout=20)
    results["doctor"] = {"rc": rc, "ms": round(ms, 1),
                          "ok": ms <= TARGETS["doctor_ms_max"]}

    # 6. repo-readiness (member should be 4/4 ready post Phase T)
    rc, _, ms = _run([PY, "bin/repo-readiness", "--repo", "spring-roomescape-member"])
    results["repo_readiness_member"] = {
        "rc": rc, "ms": round(ms, 1),
        "ok": rc == 0 and ms <= TARGETS["repo_readiness_ms_max"]
    }

    # 7. bootstrap (idempotent — skip all = fast)
    rc, _, ms = _run([PY, "bin/bootstrap"], timeout=10)
    results["bootstrap_idempotent"] = {
        "rc": rc, "ms": round(ms, 1),
        "ok": rc == 0 and ms <= TARGETS["bootstrap_skip_ms_max"]
    }

    # 8. onboard-repo --help (full onboarding invokes BGE-M3 which is
    # expensive; here gate on wrapper startup only — full flow is verified
    # in tests/test_collection.py)
    rc, _, ms = _run([PY, "bin/onboard-repo", "--help"], timeout=5)
    results["onboard_help"] = {
        "rc": rc, "ms": round(ms, 1),
        "ok": rc == 0 and ms <= 2000
    }

    # 9. sync-prs without prior run → expected exit 2
    rc, _, ms = _run([PY, "bin/sync-prs", "--repo", "no-such-repo",
                        "--owner", "no"], timeout=5)
    results["sync_prs_no_run"] = {
        "rc": rc, "ms": round(ms, 1),
        "ok": rc == TARGETS["sync_prs_missing_run_exit_code"]
    }

    # 10. mocked collect_prs orchestrator E2E (faster than real network)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "p.sqlite"

        # Reuse the mocked-subprocess pattern from test_collection
        from unittest.mock import patch

        # Synthetic 5-PR collection
        responses = {
            "user": json.dumps({"login": "tester"}),
            "repos/o/r/pulls?state=all&per_page=100": json.dumps([[
                {"id": i, "number": i, "updated_at": "2026-05-25T00:00:00Z"}
                for i in range(1, 6)
            ]]),
        }
        for n in range(1, 6):
            responses[f"repos/o/r/pulls/{n}"] = json.dumps({
                "id": n, "number": n, "title": f"PR {n}", "state": "open",
                "user": {"login": "u"}, "base": {}, "head": {}, "body": "x",
            })
            responses[f"repos/o/r/pulls/{n}/files?per_page=100"] = json.dumps([[]])
            responses[f"repos/o/r/pulls/{n}/reviews?per_page=100"] = json.dumps([[]])
            responses[f"repos/o/r/pulls/{n}/comments?per_page=100"] = json.dumps([[]])
            responses[f"repos/o/r/issues/{n}/comments?per_page=100"] = json.dumps([[]])

        class _R:
            def __init__(s, stdout, rc=0): s.stdout = stdout; s.returncode = rc; s.stderr = ""

        def _mock_run(cmd, **kw):
            if cmd[0] == "gh" and cmd[1] == "api":
                return _R(responses.get(cmd[2], ""))
            return _R("", 1)

        t0 = time.perf_counter()
        with patch("subprocess.run", _mock_run):
            report = collect_prs.collect(owner="o", repo="r", db_path=db,
                                          max_calls=500)
        coll_ms = (time.perf_counter() - t0) * 1000
        results["collect_prs_mocked_5"] = {
            "rc": 0 if report.finished_status == "succeeded" else 1,
            "ms": round(coll_ms, 1),
            "prs_processed": report.prs_processed,
            "ok": (report.finished_status == "succeeded"
                   and report.prs_processed == 5 and coll_ms < 500),
        }

    passed = sum(1 for v in results.values() if v["ok"])
    return {
        "targets": TARGETS,
        "results": results,
        "passed": passed,
        "total": len(results),
        "pass": passed == len(results),
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {name: {"ok": r["ok"], "ms": r["ms"], "rc": r.get("rc")}
                 for name, r in report["results"].items()}
    print(json.dumps({"summary": summary, "passed": report["passed"],
                       "total": report["total"], "pass": report["pass"]},
                       ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
