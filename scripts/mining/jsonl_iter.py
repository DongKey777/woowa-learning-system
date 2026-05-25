"""Streaming JSONL iterator with field filters + sampling (Phase W prelude)."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterator


def iter_jsonl(path: Path, since_ts: float | None = None,
                event_type: str | None = None,
                mode: str | None = None,
                limit: int | None = None) -> Iterator[dict]:
    """Stream JSONL with optional filters."""
    if not path.exists():
        return
    yielded = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and ev.get("event_type") != event_type:
                continue
            if mode is not None and ev.get("mode") != mode:
                continue
            if since_ts is not None:
                try:
                    if float(ev.get("ts") or ev.get("logged_at") or 0) < since_ts:
                        continue
                except (TypeError, ValueError):
                    continue
            yield ev
            yielded += 1
            if limit and yielded >= limit:
                break


def sample_jsonl(path: Path, n: int, seed: int = 0, **filters) -> list[dict]:
    """Reservoir sample n events."""
    pool = list(iter_jsonl(path, **filters))
    if n >= len(pool):
        return pool
    return random.Random(seed).sample(pool, n)
