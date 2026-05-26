"""tests/benchmarks/phase_y_learner_cycle.py — Phase Y final demo: full
learner 1-cycle on spring-roomescape-waiting (fresh mission).

Scenario: 학습자가 새 미션 onboard 후 첫 학습 cycle 진행:
  S1. session-start (warm path - 캐시 있으면)
  S2. ask "Bean DI 더 자세히" (cs_qa mode)
  S3. assess-learner-state
  S4. next-action (profile-driven recommendations)
  S5. learn-record-code (학습자 첫 .java 작성 시뮬레이션 — 미션 default)
  S6. learn-test (시뮬레이션 — 첫 컴파일/테스트)
  S7. learn-response-quality (S2 답변 telemetry)
  S8. learn-feedback (helpful 시그널)
  S9. profile-recompute
  S10. learn-pr-retro (own PR 0이라 빈 결과 — 정상)

각 step 결과 분석.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "phase_y_learner_cycle.json"
MISSION = REPO_ROOT / "missions" / "spring-roomescape-waiting"


def _run(cmd, timeout=60, stdin=None):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                        timeout=timeout, input=stdin)
    return {"rc": r.returncode, "ms": round((time.perf_counter() - t0) * 1000, 1),
            "stdout": r.stdout, "stderr_tail": r.stderr[-200:] if r.stderr else ""}


def _parse_json_safe(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {}


def measure() -> dict:
    PY = sys.executable
    steps: list[dict] = []
    insights: list[str] = []
    REPO = "spring-roomescape-waiting"

    # S1. session-start (warm — caches from earlier benches)
    r1 = _run([PY, "bin/session-start", "--repo", REPO,
                "--prompt", "Bean DI 더 자세히",
                "--path", str(MISSION), "--silent"])
    j1 = _parse_json_safe(r1["stdout"])
    steps.append({"step": "S1_session_start", "rc": r1["rc"], "ms": r1["ms"],
                   "boot_type": j1.get("boot_type"),
                   "mode": j1.get("mode"),
                   "ask_latency_ms": j1.get("ask_latency_ms")})
    if j1.get("boot_type") == "warm":
        insights.append("S1: warm boot (assess + profile 캐시 hit)")

    # S2. ask basic cs_qa
    r2 = _run([PY, "bin/ask", "Spring Bean 생명주기"])
    has_concept = "spring/" in r2["stdout"]
    steps.append({"step": "S2_ask_cs_qa", "rc": r2["rc"], "ms": r2["ms"],
                   "spring_concept_surfaced": has_concept})
    if has_concept:
        insights.append("S2: cs_qa surfaced spring/* concept_id")

    # S3. assess-learner-state
    r3 = _run([PY, "bin/assess-learner-state", "--repo", REPO,
                "--path", str(MISSION), "--silent"])
    j3 = _parse_json_safe(r3["stdout"])
    steps.append({"step": "S3_assess_learner_state", "rc": r3["rc"], "ms": r3["ms"],
                   "head_branch": j3.get("head_branch"),
                   "prs_n": j3.get("prs_n"),
                   "threads_n": j3.get("threads_n")})

    # S4. next-action
    r4 = _run([PY, "bin/next-action", "--repo", REPO])
    j4 = _parse_json_safe(r4["stdout"])
    steps.append({"step": "S4_next_action", "rc": r4["rc"], "ms": r4["ms"],
                   "actions_n": j4.get("actions_n"),
                   "first_action_kind": (j4.get("actions") or [{}])[0].get("kind")})

    # S5. learn-record-code (simulate first edit on a real java file)
    java_files = list(MISSION.rglob("*.java"))[:1]
    if java_files:
        r5 = _run([PY, "bin/learn-record-code",
                    "--file-path", str(java_files[0]),
                    "--summary", "ReservationWaiting entity 초안",
                    "--repo", REPO, "--lines-added", "12", "--lines-removed", "0",
                    "--silent"])
        steps.append({"step": "S5_learn_record_code", "rc": r5["rc"], "ms": r5["ms"],
                       "file": java_files[0].name})
    else:
        steps.append({"step": "S5_learn_record_code", "skip": "no java files"})

    # S6. learn-test (simulate gradle output via synthetic xml — placeholder
    # since waiting mission has no test results yet)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "TEST-com.roomescape.WaitingTest.xml"
        xml.write_text("""<?xml version='1.0' encoding='UTF-8'?>
<testsuite tests="1">
  <testcase classname="com.roomescape.WaitingTest" name="shouldCreate" time="0.05"/>
</testsuite>""", encoding="utf-8")
        r6 = _run([PY, "bin/learn-test", "--path", tmp,
                    "--repo", REPO, "--silent"])
        j6 = _parse_json_safe(r6["stdout"])
    steps.append({"step": "S6_learn_test", "rc": r6["rc"], "ms": r6["ms"],
                   "cases_total": j6.get("cases_total"),
                   "events_appended": j6.get("events_appended")})

    # S7. learn-response-quality (use S1 event_id if available)
    event_id = j1.get("event_id") or "demo-event"
    r7 = _run([PY, "bin/learn-response-quality",
                "--source-event-id", event_id,
                "--response-summary", "Bean DI 답변",
                "--response-text", "Bean은 IoC 컨테이너가 관리하는 객체입니다",
                "--expected-citation", "spring/bean-di-basics",
                "--declared-citation", "spring/bean-di-basics",
                "--repo", REPO, "--silent"])
    steps.append({"step": "S7_learn_response_quality", "rc": r7["rc"], "ms": r7["ms"]})

    # S8. learn-feedback (helpful signal)
    r8 = _run([PY, "bin/learn-feedback", "--signal", "helpful",
                "--hit", "spring/bean-di-basics",
                "--source-event-id", event_id, "--silent"])
    steps.append({"step": "S8_learn_feedback", "rc": r8["rc"], "ms": r8["ms"]})

    # S9. profile-recompute (post-cycle)
    r9 = _run([PY, "bin/profile-recompute", "--silent"])
    j9 = _parse_json_safe(r9["stdout"])
    steps.append({"step": "S9_profile_recompute", "rc": r9["rc"], "ms": r9["ms"],
                   "experience": j9.get("experience_level"),
                   "events_total": j9.get("events_total"),
                   "mastered_n": j9.get("mastered_n")})

    # S10. learn-pr-retro (own PR 0 expected — fresh mission)
    r10 = _run([PY, "bin/learn-pr-retro", "--repo", REPO, "--silent"])
    j10 = _parse_json_safe(r10["stdout"])
    steps.append({"step": "S10_learn_pr_retro", "rc": r10["rc"], "ms": r10["ms"],
                    "prs_total": j10.get("prs_total"),
                    "note": "fresh mission, own PR 0 expected"})

    # All steps should rc=0
    all_ok = all(s.get("rc", 0) == 0 or s.get("skip") for s in steps)
    return {
        "scenario": "Phase Y learner 1-cycle on spring-roomescape-waiting",
        "steps": steps,
        "insights": insights,
        "total_steps": len(steps),
        "all_ok": all_ok,
        "total_e2e_ms": sum(s.get("ms", 0) for s in steps),
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    summary = [{"step": s["step"], "rc": s.get("rc", "skip"),
                  "ms": s.get("ms", 0)} for s in report["steps"]]
    print(json.dumps({
        "summary": summary,
        "insights": report["insights"],
        "all_ok": report["all_ok"],
        "total_e2e_ms": round(report["total_e2e_ms"], 1),
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["all_ok"] else 1)
