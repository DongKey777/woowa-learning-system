"""Tests for state pending-trigger expiry on load (W1).

A stale self_assessment in pending_triggers.json must be expired on load so it
stops blocking new offers / hijacking score-like replies; timestamp-less entries
and review_drill (drill_pending.json, no timestamps) must never be dropped.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.state import _load_pending_triggers  # noqa: E402

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _write(state_root: Path, name: str, payload: dict) -> None:
    learner = state_root / "learner"
    learner.mkdir(parents=True, exist_ok=True)
    (learner / name).write_text(json.dumps(payload), encoding="utf-8")


def test_expires_stale_self_assessment(tmp_path: Path) -> None:
    # Mirrors the live zombie: issued 06-02, expired 06-03, still present 13d later.
    _write(tmp_path, "pending_triggers.json", {
        "self_assessment": {
            "trigger_session_id": "s1",
            "issued_at": "2026-06-02T07:42:16+00:00",
            "expires_at": "2026-06-03T07:42:16+00:00",
        }
    })
    pending = _load_pending_triggers(tmp_path, now=NOW)
    assert "self_assessment" not in pending


def test_keeps_fresh_self_assessment(tmp_path: Path) -> None:
    _write(tmp_path, "pending_triggers.json", {
        "self_assessment": {
            "trigger_session_id": "s2",
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(hours=12)).isoformat(),
        }
    })
    pending = _load_pending_triggers(tmp_path, now=NOW)
    assert "self_assessment" in pending


def test_expires_via_issued_at_ttl_when_no_expires_at(tmp_path: Path) -> None:
    _write(tmp_path, "pending_triggers.json", {
        "self_assessment": {
            "trigger_session_id": "s3",
            "issued_at": (NOW - timedelta(hours=48)).isoformat(),
        }
    })
    pending = _load_pending_triggers(tmp_path, now=NOW)
    assert "self_assessment" not in pending


def test_keeps_timestampless_review_drill(tmp_path: Path) -> None:
    # review_drill comes from drill_pending.json and has NO timestamps — never drop.
    _write(tmp_path, "drill_pending.json", {"trigger_session_id": "d1", "concept_id": "spring/di"})
    pending = _load_pending_triggers(tmp_path, now=NOW)
    assert "review_drill" in pending


def test_keeps_timestampless_pending_entry(tmp_path: Path) -> None:
    # Only timestamped+expired entries are dropped; a timestamp-less entry is kept.
    _write(tmp_path, "pending_triggers.json", {"some_trigger": {"trigger_session_id": "x1"}})
    pending = _load_pending_triggers(tmp_path, now=NOW)
    assert "some_trigger" in pending
