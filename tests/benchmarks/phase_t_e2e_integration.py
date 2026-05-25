"""tests/benchmarks/phase_t_e2e_integration.py — Phase T end-to-end integration.

Simulates a complete learner session using all 7 Phase T wrappers in
real-world order. Verifies:

  S1. assess-learner-state — real mission repo snapshot
  S2. profile-recompute — derive v3 profile from real history
  S3. session-start (warm) — orchestrator should detect fresh caches
  S4. learn-pr-retro — recurring signals from real archive
  S5. learn-record-code — simulated code attempt → history append
  S6. learn-test — simulated JUnit XML → test_result events
  S7. learn-response-quality — simulated coach answer telemetry

Each step verifies its own output AND that downstream wrappers see the
side effects (history.jsonl grows, profile reflects new events, etc.).

Run requires: paradigm-v2 daemon running, paradigm-v2 state has the
member/auth archives copied (Phase S).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.code_event import record_code_attempt  # noqa: E402
from core.junit_ingest import ingest_junit_dir  # noqa: E402
from core.learner_state import assess_learner_state  # noqa: E402
from core.pr_retro import build_retrospective  # noqa: E402
from core.profile import recompute_profile  # noqa: E402
from core.response_quality import record_response_quality  # noqa: E402
from core.session import start_session  # noqa: E402
from core.state import DEFAULT_STATE_ROOT, read_history  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "phase_t_e2e_integration.json"
MEMBER_MISSION = REPO_ROOT / "missions" / "spring-roomescape-member"


def _step(name, fn):
    t0 = time.perf_counter()
    try:
        result = fn()
        ok = True
        err = None
    except Exception as e:
        result = None
        ok = False
        err = f"{type(e).__name__}: {e}"[:200]
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {"step": name, "ok": ok, "elapsed_ms": round(elapsed_ms, 1),
            "result": result, "error": err}


def _isolated_state(tmp: Path) -> Path:
    """Build an isolated state dir for steps that mutate state (S5-S7)."""
    sr = tmp / "state"
    (sr / "learner").mkdir(parents=True)
    return sr


def measure() -> dict:
    if not MEMBER_MISSION.exists():
        return {"pass": False, "error": f"mission missing: {MEMBER_MISSION}"}

    steps = []
    integration_checks: dict[str, bool] = {}

    # S1. assess-learner-state on REAL paradigm-v2 state
    def s1():
        payload = assess_learner_state(
            repo="spring-roomescape-member",
            mission_path=MEMBER_MISSION,
            state_root=DEFAULT_STATE_ROOT,
            learner_login="DongKey777",
        )
        out = DEFAULT_STATE_ROOT / "repos" / "spring-roomescape-member" / "contexts" / "learner-state.json"
        integration_checks["s1_output_written"] = out.exists()
        integration_checks["s1_head_sha_set"] = bool(payload.get("head_sha"))
        integration_checks["s1_target_pr_threads"] = (
            (payload.get("target_pr") or {}).get("threads_total", 0) > 0
        )
        return {
            "coverage": payload["coverage"],
            "head_branch": payload.get("head_branch"),
            "head_sha_short": (payload.get("head_sha") or "")[:8],
            "threads_n": (payload.get("target_pr") or {}).get("threads_total", 0),
            "classifications": (
                _count_classifications(payload.get("target_pr") or {})
                if payload.get("target_pr") else None
            ),
        }
    steps.append(_step("S1_assess_learner_state", s1))

    # S2. profile-recompute on REAL history
    def s2():
        hist_before = len(read_history(state_root=DEFAULT_STATE_ROOT))
        profile = recompute_profile(state_root=DEFAULT_STATE_ROOT)
        integration_checks["s2_profile_v3_schema"] = profile["schema_version"] == "v3"
        integration_checks["s2_experience_advanced"] = profile["experience_level"] == "advanced"
        integration_checks["s2_mastered_5_plus"] = len(profile["concepts"]["mastered"]) >= 5
        return {
            "experience": profile["experience_level"],
            "events_total": profile["activity"]["events_total"],
            "mastered_n": len(profile["concepts"]["mastered"]),
            "proficient_n": len(profile["concepts"]["proficient"]),
            "recommendations_n": len(profile["next_recommendations"]),
            "history_seen": hist_before,
        }
    steps.append(_step("S2_profile_recompute", s2))

    # S3. session-start (warm — caches from S1/S2)
    def s3():
        result = start_session(
            repo="spring-roomescape-member",
            prompt="Bean DI 더 자세히",
            mission_path=MEMBER_MISSION,
            force_refresh=False,  # WARM
        )
        integration_checks["s3_warm_path"] = (
            result["boot_type"] == "warm" and not result["assess_ran"]
            and not result["profile_ran"]
        )
        integration_checks["s3_ask_ok"] = result["ask_ok"]
        return {
            "boot_type": result["boot_type"],
            "assess_ran": result["assess_ran"],
            "profile_ran": result["profile_ran"],
            "ask_ok": result["ask_ok"],
            "ask_latency_ms": result["ask_latency_ms"],
            "mode": result["mode"],
            "event_id": result["event_id"],
        }
    steps.append(_step("S3_session_start_warm", s3))

    # S4. learn-pr-retro on REAL archive
    def s4():
        retro = build_retrospective(
            repo="spring-roomescape-member",
            learner_login="DongKey777",
            state_root=DEFAULT_STATE_ROOT,
        )
        integration_checks["s4_source_sqlite"] = retro.source == "sqlite"
        integration_checks["s4_unresolved_threads"] = len(retro.unresolved_threads) > 0
        return {
            "prs_total": retro.prs_total,
            "prs_merged": retro.prs_merged,
            "recurring_signals_n": len(retro.recurring_mentor_signals),
            "unresolved_threads_n": len(retro.unresolved_threads),
        }
    steps.append(_step("S4_learn_pr_retro", s4))

    # S5-S7 use ISOLATED state (so we don't pollute production history)
    with tempfile.TemporaryDirectory() as tmp:
        iso_root = _isolated_state(Path(tmp))

        # S5. learn-record-code — simulated code attempt on a REAL java file
        def s5():
            java = next(MEMBER_MISSION.rglob("*.java"))
            event = record_code_attempt(
                file_path=str(java), summary="Phase T e2e simulated edit",
                repo="spring-roomescape-member",
                lines_added=12, lines_removed=3,
                state_root=iso_root, mode="learning",
            )
            hist_lines = (iso_root / "learner" / "history.jsonl").read_text(encoding="utf-8").splitlines()
            integration_checks["s5_event_appended"] = len(hist_lines) >= 1
            integration_checks["s5_concept_inferred"] = (
                event["payload"]["concept_source"] == "auto_inferred"
                and len(event["payload"]["concept_ids"]) >= 0
            )
            return {
                "file_path": str(java.relative_to(MEMBER_MISSION)),
                "event_id": event["event_id"],
                "concepts": event["payload"]["concept_ids"][:3],
                "concept_source": event["payload"]["concept_source"],
            }
        steps.append(_step("S5_learn_record_code", s5))

        # S6. learn-test — simulate gradle test XML
        def s6():
            xml_dir = Path(tmp) / "test-results"
            xml_dir.mkdir(parents=True)
            (xml_dir / "TEST-com.roomescape.ReservationControllerTest.xml").write_text("""<?xml version='1.0' encoding='UTF-8'?>
<testsuite name="com.roomescape.ReservationControllerTest" tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="com.roomescape.ReservationControllerTest" name="shouldCreate" time="0.05"/>
  <testcase classname="com.roomescape.ReservationControllerTest" name="shouldReject" time="0.04">
    <failure message="expected 400">stack</failure>
  </testcase>
</testsuite>""", encoding="utf-8")
            report = ingest_junit_dir(
                xml_dir, repo="spring-roomescape-member",
                state_root=iso_root,
            )
            hist_after = read_history(state_root=iso_root)
            test_events = [e for e in hist_after if e.get("event_type") == "test_result"]
            integration_checks["s6_2_test_events"] = len(test_events) == 2
            integration_checks["s6_uncertain_inferred"] = (
                "spring/mvc-controller-basics" in report.inferred_uncertain_concepts
            )
            return {
                "files_scanned": report.files_scanned,
                "cases_total": report.cases_total,
                "cases_passed": report.cases_passed,
                "cases_failed": report.cases_failed,
                "events_appended": report.events_appended,
                "uncertain_concepts": report.inferred_uncertain_concepts,
            }
        steps.append(_step("S6_learn_test", s6))

        # S7. learn-response-quality — simulate coach answer telemetry
        def s7():
            source_id = (steps[2]["result"] or {}).get("event_id") or "e2e-test"
            row = record_response_quality(
                source_event_id=source_id,
                response_summary="Bean DI는 IoC 컨테이너가 객체 생명주기를 관리하는 패턴",
                response_body=(
                    "Bean DI 핵심은 의존성 주입을 통해 객체 결합도를 낮추는 거야. "
                    "전화 010-1234-5678 같은 PII는 자동 redact됨. " * 5
                ),
                expected_citation_paths=["spring/bean-di-basics", "spring/ioc-di-basics"],
                declared_citation_paths=["spring/bean-di-basics"],
                state_root=iso_root,
            )
            log = (iso_root / "learner" / "response-quality.jsonl").read_text(encoding="utf-8")
            integration_checks["s7_jsonl_written"] = len(log) > 100
            integration_checks["s7_pii_redacted"] = "[PHONE]" in row["response_excerpt"]
            integration_checks["s7_drift_detected"] = "citation_mismatch" in row["quality_flags"]
            return {
                "schema_id": row["schema_id"],
                "source_event_id": row["source_event_id"],
                "quality_flags": row["quality_flags"],
                "redaction_applied": "[PHONE]" in row["response_excerpt"],
                "excerpt_len": len(row["response_excerpt"]),
            }
        steps.append(_step("S7_learn_response_quality", s7))

    all_steps_ok = all(s["ok"] for s in steps)
    integration_pass_n = sum(1 for v in integration_checks.values() if v)
    integration_total = len(integration_checks)

    axes = {
        "all_steps_succeed": (all_steps_ok, f"{sum(1 for s in steps if s['ok'])}/{len(steps)} steps"),
        "integration_checks_pass": (
            integration_pass_n == integration_total,
            f"{integration_pass_n}/{integration_total} checks"
        ),
        "total_e2e_latency": (
            sum(s["elapsed_ms"] for s in steps) < 5000,
            f"sum {sum(s['elapsed_ms'] for s in steps):.0f}ms < 5000ms"
        ),
    }

    return {
        "scenario": "Phase T 7-wrapper end-to-end integration",
        "steps": steps,
        "integration_checks": integration_checks,
        "axes": {k: {"pass": v[0], "detail": v[1]} for k, v in axes.items()},
        "pass": all(p for p, _ in axes.values()),
    }


def _count_classifications(target_pr: dict) -> dict:
    threads = target_pr.get("threads", [])
    out = {}
    for t in threads:
        c = t.get("classification", "?")
        out[c] = out.get(c, 0) + 1
    return out


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "steps_status": [{"name": s["step"], "ok": s["ok"], "ms": s["elapsed_ms"],
                            "error": s.get("error")} for s in report.get("steps", [])],
        "integration_checks": report.get("integration_checks"),
        "pass": report.get("pass"),
        "axes": {k: v["pass"] for k, v in report.get("axes", {}).items()},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
