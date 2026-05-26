"""tests/benchmarks/phase_y8_concurrent_append.py — verify Y8 fcntl lock
on history.jsonl works under concurrent multi-process append.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.state import append_history_event  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "phase_y8_concurrent_append.json"
N_WORKERS = 8
N_PER_WORKER = 100


def _writer(args):
    state_root_str, worker_id = args
    state_root = Path(state_root_str)
    for i in range(N_PER_WORKER):
        append_history_event({
            "event_id": f"w{worker_id}-e{i}",
            "event_type": "rag_ask",
            "ts": time.time(),
            "payload": {"worker": worker_id, "seq": i},
        }, state_root=state_root)
    return worker_id


def measure():
    with tempfile.TemporaryDirectory() as tmp:
        sr = Path(tmp)
        (sr / "learner").mkdir(parents=True)
        t0 = time.perf_counter()
        with mp.Pool(N_WORKERS) as pool:
            pool.map(_writer, [(str(sr), i) for i in range(N_WORKERS)])
        elapsed_ms = (time.perf_counter() - t0) * 1000

        log = sr / "learner" / "history.jsonl"
        lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        valid = 0
        invalid = 0
        seen_ids = set()
        for l in lines:
            try:
                ev = json.loads(l)
                valid += 1
                seen_ids.add(ev.get("event_id"))
            except json.JSONDecodeError:
                invalid += 1

        expected = N_WORKERS * N_PER_WORKER
        unique_ids = len(seen_ids)
        return {
            "n_workers": N_WORKERS,
            "events_per_worker": N_PER_WORKER,
            "expected_total": expected,
            "lines_written": len(lines),
            "valid_json": valid,
            "invalid_json": invalid,
            "unique_event_ids": unique_ids,
            "elapsed_ms": round(elapsed_ms, 1),
            "throughput_events_per_sec": round(expected / (elapsed_ms / 1000), 1),
            "pass": (valid == expected and invalid == 0
                     and unique_ids == expected),
        }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
