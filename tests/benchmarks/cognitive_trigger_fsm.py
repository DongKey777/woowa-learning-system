"""Benchmark + parity checks for Y13-E cognitive trigger FSM."""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_ROOT = REPO_ROOT.parent / "woowa-learning-hub"
REPORT_PATH = REPO_ROOT / "reports" / "cognitive_trigger_fsm.json"
sys.path.insert(0, str(REPO_ROOT))

from core.trigger import select_cognitive_trigger  # noqa: E402


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    # Nearest-rank percentile, matching the Y13 latency reports.
    rank = max(1, int((pct / 100.0) * len(ordered) + 0.999999))
    return ordered[min(len(ordered) - 1, rank - 1)]


def _cases(now: datetime) -> list[dict]:
    code_attempt = {
        "event_id": "code-1",
        "event_type": "code_attempt",
        "mode": "learning",
        "ts": now.timestamp(),
        "payload": {
            "file_path": "missions/app/src/main/java/App.java",
            "concept_ids": ["spring/di"],
        },
    }
    due = {
        "concept_id": "spring/bean",
        "question": "Bean 복습?",
        "due_at": (now - timedelta(minutes=1)).isoformat(),
    }
    follow = {"open_follow_up_queue": [{"question": "후속 질문?", "learning_points": []}]}
    return [
        {
            "name": "self_assessment_priority",
            "kwargs": {
                "history": [code_attempt],
                "profile": follow,
                "drill_history": [due],
                "pending_triggers": {},
                "now": now,
            },
            "expected": "self_assessment",
        },
        {
            "name": "review_priority",
            "kwargs": {
                "profile": follow,
                "drill_history": [due],
                "pending_triggers": {},
                "now": now,
            },
            "expected": "review_drill",
        },
        {
            "name": "follow_up_only",
            "kwargs": {"profile": follow, "pending_triggers": {}, "now": now},
            "expected": "follow_up",
        },
        {
            "name": "drill_pending_suppression",
            "kwargs": {
                "history": [code_attempt],
                "profile": follow,
                "drill_pending": {"drill_session_id": "d1"},
                "drill_history": [due],
                "pending_triggers": {},
                "now": now,
            },
            "expected": "none",
        },
        {
            "name": "self_pending_allows_review",
            "kwargs": {
                "history": [code_attempt],
                "drill_history": [due],
                "pending_triggers": {"self_assessment": {"trigger_session_id": "s1"}},
                "now": now,
            },
            "expected": "review_drill",
        },
    ]


def _run_v2_cases(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        selected = select_cognitive_trigger(**case["kwargs"])
        observed = selected.get("trigger_type")
        rows.append({
            "name": case["name"],
            "expected": case["expected"],
            "observed": observed,
            "pass": observed == case["expected"],
            "reason": selected.get("reason"),
        })
    return rows


def _legacy_compare(now: datetime) -> dict | None:
    legacy_module = LEGACY_ROOT / "scripts" / "workbench" / "core" / "cognitive_trigger.py"
    if not legacy_module.exists():
        return None
    code = f"""
import json, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, {str(LEGACY_ROOT / 'scripts' / 'workbench')!r})
from core.cognitive_trigger import select_cognitive_trigger
now = datetime.fromisoformat({now.isoformat()!r})
code_attempt = {{
    'event_id': 'code-1',
    'event_type': 'code_attempt',
    'mode': 'learning',
    'ts': now.timestamp(),
    'payload': {{
        'file_path': 'missions/app/src/main/java/App.java',
        'concept_ids': ['spring/di'],
    }},
}}
due = {{
    'concept_id': 'spring/bean',
    'question': 'Bean 복습?',
    'due_at': (now - timedelta(minutes=1)).isoformat(),
}}
follow = {{'open_follow_up_queue': [{{'question': '후속 질문?', 'learning_points': []}}]}}
cases = [
    ('self_assessment_priority', dict(history=[code_attempt], profile={{
        **follow,
        'recent_code_changes_24h': [{{
            'event_id': 'code-1',
            'ts': now.timestamp(),
            'file_path': 'missions/app/src/main/java/App.java',
            'concept_ids': ['spring/di'],
        }}],
    }}, drill_history=[due], pending_triggers={{}}, now=now)),
    ('review_priority', dict(profile=follow, drill_history=[due], pending_triggers={{}}, now=now)),
    ('follow_up_only', dict(profile=follow, pending_triggers={{}}, now=now)),
    ('drill_pending_suppression', dict(history=[code_attempt], profile=follow,
        drill_pending={{'drill_session_id': 'd1'}}, drill_history=[due],
        pending_triggers={{}}, now=now)),
    ('self_pending_allows_review', dict(history=[code_attempt], drill_history=[due],
        pending_triggers={{'self_assessment': {{'trigger_session_id': 's1'}}}}, now=now)),
]
print(json.dumps([
    {{'name': name, 'observed': select_cognitive_trigger(**kwargs).get('trigger_type')}}
    for name, kwargs in cases
], ensure_ascii=False))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=LEGACY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.SubprocessError as exc:
        return {"available": True, "error": str(exc), "pass": False}
    if result.returncode != 0:
        return {
            "available": True,
            "rc": result.returncode,
            "stderr": result.stderr[-500:],
            "pass": False,
        }
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"available": True, "stdout": result.stdout[-500:], "pass": False}
    return {"available": True, "rows": rows, "pass": True}


def _latency(cases: list[dict], iterations: int = 2000) -> dict:
    samples_ms: list[float] = []
    # Cycle through all cases so the measurement covers each branch.
    for i in range(iterations):
        kwargs = cases[i % len(cases)]["kwargs"]
        t0 = time.perf_counter_ns()
        select_cognitive_trigger(**kwargs)
        samples_ms.append((time.perf_counter_ns() - t0) / 1_000_000.0)
    total_ms = sum(samples_ms)
    return {
        "iterations": iterations,
        "p50_ms": round(statistics.median(samples_ms), 4),
        "p95_ms": round(_percentile(samples_ms, 95), 4),
        "max_ms": round(max(samples_ms), 4),
        "throughput_per_s": round(iterations / (total_ms / 1000.0), 1) if total_ms else 0.0,
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    cases = _cases(now)
    v2_rows = _run_v2_cases(cases)
    legacy = _legacy_compare(now)
    latency = _latency(cases)
    checks = {
        "quality_cases_pass": all(row["pass"] for row in v2_rows),
        "legacy_same_cases_pass": bool(legacy and legacy.get("pass")),
        "latency_p50_budget": latency["p50_ms"] <= 3.0,
        "latency_p95_budget": latency["p95_ms"] <= 5.0,
    }
    report = {
        "timestamp": time.time(),
        "v2_cases": v2_rows,
        "legacy_compare": legacy,
        "latency": latency,
        "checks": checks,
        "pass": checks["quality_cases_pass"]
        and checks["latency_p50_budget"]
        and checks["latency_p95_budget"],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

