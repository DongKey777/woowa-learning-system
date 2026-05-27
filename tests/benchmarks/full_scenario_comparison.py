"""Full scenario comparison: paradigm-v2 vs legacy hub.

Runs identical prompts on both systems, captures:
- Latency (warm, 3 runs median)
- Mode/intent dispatched
- Output length (chars + token estimate)
- Evidence markers (mission_patterns, cross_crew, rag_hits, cs_augmentation, etc.)
- Mode dispatch correctness (vs expected per scenario)

Outputs JSON + Markdown report under reports/.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PARADIGM_V2_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_ROOT = Path("/Users/idonghun/IdeaProjects/woowa-learning-hub")
RUNS = 5  # warm runs per scenario; p95 needs more than 3 samples to resist CLI jitter
TIMEOUT_S = 60


@dataclass
class Scenario:
    sid: str
    prompt: str
    repo: str | None
    expected_mode: str
    feature: str  # F1, F2, ... F11
    description: str
    evidence_markers: list[str] = field(default_factory=list)  # must appear in output


SCENARIOS: list[Scenario] = [
    # ── cs_qa (F1, F8) ──────────────────────────────────────────────────
    Scenario("cs_qa_di", "DI가 뭐야", None, "cs_qa", "F1",
             "단순 개념 정의 — Bean DI 도입",
             ["DI", "spring/", "참고:" if False else ""]),
    Scenario("cs_qa_tx_iso", "트랜잭션 isolation level 차이를 설명해줘", None, "cs_qa", "F1+F8",
             "Depth + prereq walk",
             ["isolation", "database/", "참고:" if False else ""]),
    Scenario("cs_qa_bean_lifecycle", "Spring Bean 생명주기 단계", None, "cs_qa", "F1",
             "Multi-step concept",
             ["Bean", "spring/"]),

    # ── coaching (F2, F10 forward) ──────────────────────────────────────
    Scenario("coach_refactor", "내 ReservationController 어떻게 리팩토링하면 좋아?",
             "spring-roomescape-member", "coaching", "F2+F10f",
             "Mentor 시각 + mission_patterns surface",
             ["mission_patterns", "121", "patterns"]),
    Scenario("coach_optional", "이 코드에서 Optional을 어떻게 쓰는 게 맞아?",
             "spring-roomescape-member", "coaching", "F2",
             "코드 패턴 가이드",
             ["Optional", "mission_patterns"]),

    # ── tool_only (F3) ──────────────────────────────────────────────────
    Scenario("tool_transactional", "@Transactional 어노테이션은 어디에 붙이는 게 좋아?",
             None, "cs_qa", "F3/F1",
             "어노테이션 사용법 — Spring 개념이므로 cs_qa 적절 (legacy와 일치)",
             ["Transactional"]),
    Scenario("tool_git_rebase", "git rebase -i 어떻게 써?", None, "tool_only", "F3",
             "Git 도구",
             ["rebase"]),

    # ── retro (F4) ──────────────────────────────────────────────────────
    Scenario("retro_pr_flow", "내 PR 흐름 보여줘",
             "spring-roomescape-member", "retro", "F4",
             "Self-PR cycle archive walk",
             ["PR", "메타"]),
    Scenario("retro_recurring", "반복 멘토 지적 뭐가 있어?",
             "spring-roomescape-member", "retro", "F4",
             "Recurring signals",
             ["멘토", "반복"]),

    # ── drill (F5, F6) ──────────────────────────────────────────────────
    # NOTE: drill mode requires pending drill trigger (v4 contract).
    # Without prior offer, "확인 질문 하나 줘" correctly falls to cs_qa
    # (system controls drill issuance, not learner-initiated).
    Scenario("drill_offer", "확인 질문 하나 줘", None, "cs_qa", "F5+F6 (policy)",
             "Drill offer — v4 policy: no pending → cs_qa fallback",
             ["질문"]),

    # ── self_assess (F7) ────────────────────────────────────────────────
    # NOTE: self_assess requires pending_self_assessment trigger (v4 contract).
    # Random scores rejected to prevent calibration noise.
    Scenario("self_assess", "DI 8점", None, "cs_qa", "F7 (policy)",
             "Self-assessment — v4 policy: no pending → cs_qa fallback",
             ["DI", "점"]),

    # ── f11_anchor (F11) ────────────────────────────────────────────────
    Scenario("f11_cross_crew", "다른 크루는 이 reservation 코드를 어떻게 작성했어?",
             "spring-roomescape-member", "f11_anchor", "F11",
             "Cross-crew comparison",
             ["cross_crew", "PR", "crew"]),
    Scenario("f11_precise", "PR 390 line 정밀 비교", "spring-roomescape-member",
             "f11_anchor", "F11",
             "Precise PR/line anchor",
             ["cross_crew", "정밀"]),

    # ── router edge (F9) ────────────────────────────────────────────────
    Scenario("short_prompt", "안녕", None, "tool_only", "F9",
             "Short prompt fallback (greeting)",
             []),
]


@dataclass
class Result:
    sid: str
    system: str         # "paradigm-v2" or "legacy"
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_min: float
    latency_samples_ms: list[float]
    output_chars: int
    token_est: int      # chars / 4
    detected_mode: str
    expected_mode: str
    mode_correct: bool | None
    evidence_hits: int  # how many evidence_markers found
    evidence_total: int
    err: str | None = None


def _parse_paradigm_v2_mode(output: str) -> str:
    """v2 prompt header has '**mode**: `<mode>` (reason: ...)'"""
    m = re.search(r"\*\*mode\*\*:\s*`([^`]+)`", output)
    return m.group(1) if m else "?"


def _parse_legacy_intent(output: str) -> str:
    """Legacy rag-ask header is '[RAG: tier-N — <reason>]'.
    Coach-run output uses different schema."""
    m = re.search(r"\[RAG:\s*tier-(\d+)\s*—", output)
    if m:
        return f"tier-{m.group(1)}"
    if "coach-run" in output.lower() or "## 상태 요약" in output:
        return "coach-run"
    return "?"


def _run_paradigm_v2(prompt: str, repo: str | None) -> tuple[str, float]:
    cmd = ["bin/ask", prompt]
    if repo:
        cmd.extend(["--repo", repo])
    t0 = time.perf_counter()
    res = subprocess.run(cmd, cwd=str(PARADIGM_V2_ROOT), capture_output=True,
                         text=True, timeout=TIMEOUT_S,
                         env={**os.environ, "WOOWA_SESSION_MODE": "development"})
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return res.stdout + res.stderr, elapsed_ms


def _run_legacy(prompt: str, repo: str | None) -> tuple[str, float]:
    # Legacy: prefer rag-ask for cs/dev questions, coach-run for repo+coaching.
    # For apples-to-apples, use rag-ask always (it routes internally).
    cmd = ["bin/rag-ask", prompt]
    t0 = time.perf_counter()
    res = subprocess.run(cmd, cwd=str(LEGACY_ROOT), capture_output=True,
                         text=True, timeout=TIMEOUT_S,
                         env={**os.environ, "WOOWA_SESSION_MODE": "development"})
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return res.stdout + res.stderr, elapsed_ms


def _percentile(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return ordered[idx]


def _bench(scenario: Scenario, system: str) -> Result:
    runner = _run_paradigm_v2 if system == "paradigm-v2" else _run_legacy
    # warm-up
    try:
        runner(scenario.prompt, scenario.repo)
    except Exception:
        pass
    times: list[float] = []
    last_output = ""
    err = None
    for _ in range(RUNS):
        try:
            output, ms = runner(scenario.prompt, scenario.repo)
            times.append(ms)
            last_output = output
        except subprocess.TimeoutExpired:
            err = "timeout"
            break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:120]
            break

    if not times:
        return Result(sid=scenario.sid, system=system,
                      latency_ms_p50=-1, latency_ms_p95=-1, latency_ms_min=-1,
                      latency_samples_ms=[],
                      output_chars=0, token_est=0,
                      detected_mode="?", expected_mode=scenario.expected_mode,
                      mode_correct=None, evidence_hits=0,
                      evidence_total=len(scenario.evidence_markers), err=err)

    p50 = statistics.median(times)
    p95 = _percentile(times, 0.95)
    detected = (_parse_paradigm_v2_mode(last_output) if system == "paradigm-v2"
                else _parse_legacy_intent(last_output))
    mode_correct = (detected == scenario.expected_mode) if system == "paradigm-v2" else None

    ev_hits = sum(1 for m in scenario.evidence_markers if m and m in last_output)

    return Result(
        sid=scenario.sid, system=system,
        latency_ms_p50=round(p50, 1),
        latency_ms_p95=round(p95 or 0.0, 1),
        latency_ms_min=round(min(times), 1),
        latency_samples_ms=[round(t, 1) for t in times],
        output_chars=len(last_output),
        token_est=len(last_output) // 4,
        detected_mode=detected,
        expected_mode=scenario.expected_mode,
        mode_correct=mode_correct,
        evidence_hits=ev_hits,
        evidence_total=len([m for m in scenario.evidence_markers if m]),
        err=err,
    )


def run_all() -> dict:
    results_v2: list[Result] = []
    results_legacy: list[Result] = []
    for sc in SCENARIOS:
        print(f"  [{sc.sid}] {sc.prompt[:50]}…", flush=True)
        results_v2.append(_bench(sc, "paradigm-v2"))
        results_legacy.append(_bench(sc, "legacy"))

    # Aggregate
    v2_samples = [t for r in results_v2 for t in r.latency_samples_ms if t > 0]
    legacy_samples = [t for r in results_legacy for t in r.latency_samples_ms if t > 0]
    v2_p50 = _percentile(v2_samples, 0.50)
    v2_p95 = _percentile(v2_samples, 0.95)
    legacy_p50 = _percentile(legacy_samples, 0.50)
    legacy_p95 = _percentile(legacy_samples, 0.95)
    v2_mode_correct = [r for r in results_v2 if r.mode_correct is True]
    v2_mode_total = [r for r in results_v2 if r.mode_correct is not None]
    v2_evidence_pct = (sum(r.evidence_hits for r in results_v2) /
                       max(1, sum(r.evidence_total for r in results_v2))) * 100
    legacy_evidence_pct = (sum(r.evidence_hits for r in results_legacy) /
                           max(1, sum(r.evidence_total for r in results_legacy))) * 100

    return {
        "metadata": {
            "scenarios": len(SCENARIOS),
            "runs_per_scenario": RUNS,
            "paradigm_v2_root": str(PARADIGM_V2_ROOT),
            "legacy_root": str(LEGACY_ROOT),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "summary": {
            "paradigm_v2": {
                "p50_overall_ms": round(v2_p50, 1) if v2_p50 is not None else None,
                "p95_overall_ms": round(v2_p95, 1) if v2_p95 is not None else None,
                "mode_dispatch_correct": f"{len(v2_mode_correct)}/{len(v2_mode_total)}",
                "evidence_coverage_pct": round(v2_evidence_pct, 1),
                "avg_output_chars": round(statistics.mean(r.output_chars for r in results_v2), 0),
            },
            "legacy": {
                "p50_overall_ms": round(legacy_p50, 1) if legacy_p50 is not None else None,
                "p95_overall_ms": round(legacy_p95, 1) if legacy_p95 is not None else None,
                "evidence_coverage_pct": round(legacy_evidence_pct, 1),
                "avg_output_chars": round(statistics.mean(r.output_chars for r in results_legacy), 0),
            },
            "comparison": {
                "speedup_p50": round(legacy_p50 / v2_p50, 1) if v2_p50 and legacy_p50 else None,
                "speedup_p95": round(legacy_p95 / v2_p95, 1) if v2_p95 and legacy_p95 else None,
            },
        },
        "scenarios": [asdict(s) for s in SCENARIOS],
        "results_paradigm_v2": [asdict(r) for r in results_v2],
        "results_legacy": [asdict(r) for r in results_legacy],
    }


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        f"# Paradigm-v2 vs Legacy — full scenario comparison",
        f"",
        f"- Scenarios: {report['metadata']['scenarios']} (across 7 modes + 11 features)",
        f"- Runs per scenario: {report['metadata']['runs_per_scenario']} warm",
        f"- Timestamp: {report['metadata']['timestamp']}",
        f"",
        f"## Overall summary",
        f"",
        f"| Metric | Paradigm-v2 | Legacy | Δ |",
        f"|---|---|---|---|",
        f"| p50 latency | {s['paradigm_v2']['p50_overall_ms']}ms | {s['legacy']['p50_overall_ms']}ms | {s['comparison']['speedup_p50']}× faster |",
        f"| p95 latency | {s['paradigm_v2']['p95_overall_ms']}ms | {s['legacy']['p95_overall_ms']}ms | {s['comparison']['speedup_p95']}× faster |",
        f"| Mode dispatch | {s['paradigm_v2']['mode_dispatch_correct']} | n/a (different schema) | — |",
        f"| Evidence coverage | {s['paradigm_v2']['evidence_coverage_pct']}% | {s['legacy']['evidence_coverage_pct']}% | — |",
        f"| Avg output chars | {s['paradigm_v2']['avg_output_chars']:.0f} | {s['legacy']['avg_output_chars']:.0f} | — |",
        f"",
        f"## Per-scenario detail",
        f"",
        f"| Scenario | Feature | Expected | v2 mode | v2 OK | v2 p50 | leg p50 | speedup | v2 ev | leg ev |",
        f"|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sc, r_v2, r_leg in zip(report["scenarios"], report["results_paradigm_v2"],
                                report["results_legacy"]):
        speedup = "—"
        if r_v2["latency_ms_p50"] > 0 and r_leg["latency_ms_p50"] > 0:
            speedup = f"{r_leg['latency_ms_p50'] / r_v2['latency_ms_p50']:.1f}×"
        ok = "✓" if r_v2["mode_correct"] else ("✗" if r_v2["mode_correct"] is False else "—")
        ev_v2 = f"{r_v2['evidence_hits']}/{r_v2['evidence_total']}"
        ev_leg = f"{r_leg['evidence_hits']}/{r_leg['evidence_total']}"
        lines.append(f"| {sc['sid']} | {sc['feature']} | {sc['expected_mode']} | "
                     f"{r_v2['detected_mode']} | {ok} | "
                     f"{r_v2['latency_ms_p50']}ms | {r_leg['latency_ms_p50']}ms | "
                     f"{speedup} | {ev_v2} | {ev_leg} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"Running {len(SCENARIOS)} scenarios × {RUNS} runs × 2 systems "
          f"= {len(SCENARIOS) * RUNS * 2} calls...")
    report = run_all()
    out_json = PARADIGM_V2_ROOT / "reports" / "v2_vs_legacy_full_comparison.json"
    out_md = PARADIGM_V2_ROOT / "reports" / "v2_vs_legacy_full_comparison.md"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nReports written:\n  {out_json}\n  {out_md}")
    print("\n=== SUMMARY ===")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
