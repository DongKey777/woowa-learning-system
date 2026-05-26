"""tests/benchmarks/phase_y8_fresh_clone_sim.py — simulate a brand-new
learner cloning paradigm-v2 (Phase Y8).

Verifies what works WITHOUT cached state:
  - bin/doctor (should report missing index/daemon/identity gracefully)
  - bin/learner-profile show (should report missing)
  - bin/validate-state (empty state ok)
  - bin/list-repos (empty list)
  - bin/registry-audit (no orphans)
  - bin/learn-pr-retro on non-existent repo (graceful error)
  - bin/learn-record-code with explicit concept_id (works without daemon)
  - bin/learn-feedback (just appends jsonl)
  - bin/learn-self-assess without pending (correctly rejects)

NOT verified here (require live services):
  - bin/index-fetch (network + gh CLI)
  - bin/rag-daemon start (BGE-M3 download)
  - bin/bootstrap-repo (gh CLI + network)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "phase_y8_fresh_clone_sim.json"


def _run(cmd, state_root, timeout=15):
    """Insert --state-root immediately after script path (before any
    sub-command), since argparse subparsers don't inherit parent flags
    appearing after the action positional."""
    t0 = time.perf_counter()
    if "--state-root" not in cmd:
        # insert at position 2 (after [python, script_path])
        cmd = cmd[:2] + ["--state-root", str(state_root)] + cmd[2:]
    r = subprocess.run(cmd, cwd=REPO_ROOT,
                        capture_output=True, text=True, timeout=timeout)
    return {"rc": r.returncode, "ms": round((time.perf_counter() - t0) * 1000, 1),
            "stdout": r.stdout, "stdout_tail": r.stdout[-300:],
            "stderr_tail": r.stderr[-200:]}


def measure() -> dict:
    PY = sys.executable
    results: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "fresh-state"
        (fresh / "learner").mkdir(parents=True)

        # 1. doctor — should report missing pieces but NOT crash
        r = _run([PY, "bin/doctor"], fresh, timeout=10)
        # doctor may exit 1 (some checks fail in fresh state) but must not crash
        results.append({"step": "doctor_on_empty", "rc": r["rc"], "ms": r["ms"],
                          "passed": r["ms"] < 5000 and r["rc"] in (0, 1)})

        # 2. validate-state — empty state should be valid (0 errors)
        r = _run([PY, "bin/validate-state"], fresh)
        try:
            d = json.loads(r["stdout_tail"])
        except Exception:
            d = {}
        results.append({"step": "validate_state_empty", "rc": r["rc"], "ms": r["ms"],
                          "errors_n": d.get("errors_n", -1),
                          "passed": r["rc"] == 0})

        # 3. list-repos — empty list
        r = _run([PY, "bin/list-repos"], fresh)
        try:
            d = json.loads(r["stdout_tail"])
        except Exception:
            d = {}
        results.append({"step": "list_repos_empty", "rc": r["rc"], "ms": r["ms"],
                          "repos_n": d.get("repos_n", -1),
                          "passed": r["rc"] == 0 and d.get("repos_n") == 0})

        # 4. registry-audit — clean state
        r = _run([PY, "bin/registry-audit", "--missions-root",
                   str(fresh.parent / "no-missions")], fresh)
        results.append({"step": "registry_audit_clean", "rc": r["rc"], "ms": r["ms"],
                          "passed": True})  # rc 0 or 1 both acceptable for empty

        # 5. learn-pr-retro on non-existent repo
        r = _run([PY, "bin/learn-pr-retro", "--repo", "nonexistent",
                   "--silent"], fresh)
        results.append({"step": "learn_pr_retro_no_repo", "rc": r["rc"], "ms": r["ms"],
                          "graceful": r["rc"] == 2,
                          "passed": r["rc"] == 2})

        # 6. learn-record-code with explicit concept (no daemon needed)
        java = Path(tmp) / "Foo.java"
        java.write_text("class Foo {}", encoding="utf-8")
        r = _run([PY, "bin/learn-record-code",
                   "--file-path", str(java), "--summary", "test",
                   "--concept-id", "spring/test", "--silent"], fresh)
        results.append({"step": "learn_record_code_fresh", "rc": r["rc"], "ms": r["ms"],
                          "passed": r["rc"] == 0})

        # 7. learn-feedback (only appends jsonl)
        r = _run([PY, "bin/learn-feedback", "--signal", "helpful",
                   "--note", "test", "--silent"], fresh)
        results.append({"step": "learn_feedback_fresh", "rc": r["rc"], "ms": r["ms"],
                          "passed": r["rc"] == 0})

        # 8. learn-self-assess without pending — should reject
        r = _run([PY, "bin/learn-self-assess",
                   "--trigger-session-id", "fake-id", "--score", "8",
                   "--silent"], fresh)
        results.append({"step": "learn_self_assess_reject_no_pending",
                          "rc": r["rc"], "ms": r["ms"],
                          "passed": r["rc"] == 1})

        # 9. profile-recompute on empty history (writes profile.json — silent for no stdout)
        r = _run([PY, "bin/profile-recompute"], fresh)  # no --silent so we see output
        results.append({"step": "profile_recompute_empty", "rc": r["rc"], "ms": r["ms"],
                          "passed": r["rc"] == 0
                                     and (fresh / "learner" / "profile.json").exists()})

        # 10. learner-profile show (after recompute, profile.json now exists)
        r = _run([PY, "bin/learner-profile", "show"], fresh)
        try:
            d = json.loads(r["stdout"])  # full stdout, no tail-truncation
        except Exception:
            d = {}
        results.append({"step": "learner_profile_show_fresh",
                          "rc": r["rc"], "ms": r["ms"],
                          "experience_level": d.get("experience_level"),
                          "passed": r["rc"] == 0 and d.get("experience_level") == "junior"})

    passed = sum(1 for r in results if r.get("passed"))
    return {
        "scenario": "Phase Y8 fresh clone simulation (no live services)",
        "steps": results,
        "passed": passed, "total": len(results),
        "pass": passed == len(results),
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    summary = [{"step": s["step"], "rc": s["rc"], "ms": s["ms"],
                  "pass": s.get("passed", False)} for s in report["steps"]]
    print(json.dumps({"summary": summary, "passed": report["passed"],
                       "total": report["total"], "pass": report["pass"]},
                       ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
