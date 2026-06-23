"""Streaming JSONL iterator with field filters + sampling (Phase W prelude)."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_RELATIVE_SINCE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)([smhdw])\s*$", re.IGNORECASE)
_SECONDS_BY_UNIT = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
}


def parse_since_ts(value: str | None, *, now: float | None = None) -> float | None:
    """Parse --since values into epoch seconds.

    Supported values:
    - relative durations: 30m, 24h, 7d, 2w
    - epoch seconds: 1779762582.7
    - ISO-8601 timestamps: 2026-05-27T00:00:00Z
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    ref = time.time() if now is None else now
    relative = _RELATIVE_SINCE_RE.match(text)
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2).lower()
        return ref - amount * _SECONDS_BY_UNIT[unit]

    try:
        return float(text)
    except ValueError:
        pass

    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_text)
    except ValueError as exc:
        raise ValueError(
            "--since must be a duration like 7d, epoch seconds, or ISO-8601 timestamp"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _row_timestamp(row: dict) -> float | None:
    raw = row.get("ts") or row.get("logged_at")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        try:
            return parse_since_ts(str(raw))
        except ValueError:
            return None


def iter_jsonl(path: Path, since_ts: float | None = None,
                event_type: str | None = None,
                mode: str | None = None,
                limit: int | None = None,
                exclude_modes: tuple[str, ...] = ()) -> Iterator[dict]:
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
            if exclude_modes and ev.get("mode") in exclude_modes:
                continue
            if since_ts is not None:
                row_ts = _row_timestamp(ev)
                if row_ts is None or row_ts < since_ts:
                    continue
            yield ev
            yielded += 1
            if limit and yielded >= limit:
                break


def is_fixture_or_demo_row(row: dict) -> bool:
    """Return True for rows that should not drive learner-quality mining."""
    learner_id = row.get("learner_id")
    if learner_id in {None, "multi-turn-test", "fixture", "demo"}:
        return True
    if row.get("source_event_id") == "demo-event":
        return True
    return False


def is_backfilled(row: dict) -> bool:
    """Rows with inferred provenance should be visible in audit outputs."""
    return bool(row.get("_backfilled_from") or row.get("_canonical_id_source"))
