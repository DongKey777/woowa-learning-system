"""Concurrent bin/ask latency and event-loss benchmark (Y13-0).

This benchmark covers AI-session burst behavior, not JSONL append locking.
It fails if daemon-backed response telemetry is missing, because missing
event_id/response_quality_hint means the client likely fell back to the cold
in-process path.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "concurrent_ask.json"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[idx]


def _ask_once(prompt: str, timeout_s: int, worker_id: int) -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["bin/ask", prompt, "--json"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    ms = (time.perf_counter() - t0) * 1000
    payload: dict = {}
    parse_error = None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        parse_error = str(exc)
    return {
        "worker_id": worker_id,
        "rc": proc.returncode,
        "ms": round(ms, 1),
        "event_id": payload.get("event_id"),
        "has_event_id": bool(payload.get("event_id")),
        "has_response_quality_hint": bool(payload.get("response_quality_hint")),
        "mode": payload.get("mode"),
        "parse_error": parse_error,
        "stderr_tail": proc.stderr[-200:],
    }


def measure(
    *,
    workers: int,
    prompt: str,
    timeout_s: int,
    max_p95_ms: float,
) -> dict:
    wall0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        rows = list(executor.map(
            lambda i: _ask_once(prompt, timeout_s, i),
            range(workers),
        ))
    wall_ms = (time.perf_counter() - wall0) * 1000
    latencies = [float(r["ms"]) for r in rows if r["rc"] == 0 and r["parse_error"] is None]
    event_ids = [r["event_id"] for r in rows if r.get("event_id")]
    duplicate_event_ids = sorted({
        event_id for event_id in event_ids if event_ids.count(event_id) > 1
    })
    lost_events = sum(1 for r in rows if not r["has_event_id"])
    missing_rq = sum(1 for r in rows if not r["has_response_quality_hint"])
    bad_rc = sum(1 for r in rows if r["rc"] != 0)
    parse_errors = sum(1 for r in rows if r["parse_error"])
    p50 = statistics.median(latencies) if latencies else None
    p95 = _percentile(latencies, 0.95)
    passed = (
        bad_rc == 0
        and parse_errors == 0
        and lost_events == 0
        and missing_rq == 0
        and not duplicate_event_ids
        and p95 is not None
        and p95 <= max_p95_ms
    )
    return {
        "benchmark": "concurrent_ask",
        "workers": workers,
        "prompt": prompt,
        "wall_ms": round(wall_ms, 1),
        "latency_ms": {
            "p50": round(p50, 1) if p50 is not None else None,
            "p95": round(p95, 1) if p95 is not None else None,
            "min": round(min(latencies), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "budgets": {
            "p95_ms_max": max_p95_ms,
            "lost_events_max": 0,
        },
        "bad_rc": bad_rc,
        "parse_errors": parse_errors,
        "lost_events": lost_events,
        "missing_response_quality_hint": missing_rq,
        "duplicate_event_ids": duplicate_event_ids,
        "rows": rows,
        "pass": passed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--prompt", default="DI")
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--max-p95-ms", type=float, default=1500.0)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    report = measure(
        workers=args.workers,
        prompt=args.prompt,
        timeout_s=args.timeout_s,
        max_p95_ms=args.max_p95_ms,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "workers": report["workers"],
        "latency_ms": report["latency_ms"],
        "lost_events": report["lost_events"],
        "missing_response_quality_hint": report["missing_response_quality_hint"],
        "duplicate_event_ids": report["duplicate_event_ids"],
        "pass": report["pass"],
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
