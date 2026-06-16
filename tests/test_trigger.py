"""Tests for cognitive trigger priority FSM."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.trigger import (  # noqa: E402
    expire_stale_triggers,
    load_pending_triggers,
    match_pending_trigger,
    select_cognitive_trigger,
    write_pending_triggers_atomic,
)


def _code_attempt(ts: float, concept_id: str = "spring/di") -> dict:
    return {
        "event_id": "code-1",
        "event_type": "code_attempt",
        "mode": "learning",
        "ts": ts,
        "payload": {
            "file_path": "missions/app/src/main/java/App.java",
            "concept_ids": [concept_id],
            "summary": "DI refactor",
        },
    }


def test_self_assessment_beats_review_and_follow_up() -> None:
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    trigger = select_cognitive_trigger(
        history=[_code_attempt(now.timestamp())],
        profile={
            "open_follow_up_queue": [{"question": "다음으로 볼 질문?", "learning_points": []}],
        },
        drill_history=[{
            "concept_id": "spring/bean",
            "question": "Bean 복습?",
            "due_at": (now - timedelta(minutes=1)).isoformat(),
        }],
        pending_triggers={},
        now=now,
    )

    assert trigger["trigger_type"] == "self_assessment"
    assert trigger["payload"]["concept_ids"] == ["spring/di"]
    assert trigger["competed_against"] == ["self_assessment_due"]


def test_review_drill_beats_follow_up_when_no_self_assessment() -> None:
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    trigger = select_cognitive_trigger(
        profile={
            "open_follow_up_queue": [{"question": "이어질 질문?", "learning_points": []}],
        },
        drill_history=[{
            "concept_id": "spring/bean",
            "next_due_ts": (now - timedelta(seconds=1)).timestamp(),
        }],
        pending_triggers={},
        now=now,
    )

    assert trigger["trigger_type"] == "review_drill"
    assert trigger["payload"]["linked_learning_point"] == "spring/bean"
    assert trigger["competed_against"] == ["self_assessment_due", "review_due"]


def test_drill_pending_suppresses_all_triggers() -> None:
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    trigger = select_cognitive_trigger(
        history=[_code_attempt(now.timestamp())],
        profile={
            "open_follow_up_queue": [{"question": "이어질 질문?", "learning_points": []}],
        },
        drill_pending={"drill_session_id": "d1"},
        drill_history=[{
            "concept_id": "spring/bean",
            "next_due_ts": now.timestamp() - 1,
        }],
        pending_triggers={},
        now=now,
    )

    assert trigger["trigger_type"] == "none"
    assert trigger["competed_against"] == []


def test_pending_self_assessment_skips_self_and_allows_review() -> None:
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    trigger = select_cognitive_trigger(
        history=[_code_attempt(now.timestamp())],
        drill_history=[{
            "concept_id": "spring/bean",
            "next_due_ts": now.timestamp() - 1,
        }],
        pending_triggers={"self_assessment": {"trigger_session_id": "s1"}},
        now=now,
    )

    assert trigger["trigger_type"] == "review_drill"
    assert trigger["competed_against"] == ["self_assessment_due", "review_due"]


def test_self_assessment_history_suppresses_duplicate_concept() -> None:
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    history = [
        _code_attempt(now.timestamp()),
        {
            "event_type": "self_assessment",
            "mode": "learning",
            "ts": now.timestamp(),
            "payload": {"concept_ids": ["spring/di"], "score": 0.8},
        },
    ]
    trigger = select_cognitive_trigger(
        history=history,
        profile={
            "open_follow_up_queue": [{"question": "후속 질문?", "learning_points": []}],
        },
        pending_triggers={},
        now=now,
    )

    assert trigger["trigger_type"] == "follow_up"


def test_expire_stale_and_match_pending_trigger() -> None:
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    pending = {
        "self_assessment": {
            "trigger_session_id": "fresh",
            "issued_at": (now - timedelta(hours=1)).isoformat(),
        },
        "old": {
            "trigger_session_id": "old",
            "issued_at": (now - timedelta(hours=30)).isoformat(),
        },
        "expired": {
            "trigger_session_id": "expired",
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
        },
    }

    kept = expire_stale_triggers(pending, now)

    assert set(kept) == {"self_assessment"}
    assert match_pending_trigger(
        kept,
        "self_assessment",
        {"trigger_session_id": "fresh"},
    ) == "fresh"
    assert match_pending_trigger(
        kept,
        "self_assessment",
        {"trigger_session_id": "wrong"},
    ) is None


def test_load_write_pending_triggers_atomic(tmp_path: Path) -> None:
    payload = {
        "self_assessment": {
            "trigger_session_id": "s1",
            "payload": {"concept_ids": ["spring/bean"]},
        }
    }

    write_pending_triggers_atomic(payload, tmp_path)

    path = tmp_path / "learner" / "pending_triggers.json"
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert load_pending_triggers(tmp_path) == payload


def test_selector_latency_budget_micro() -> None:
    now = datetime.now(timezone.utc)
    history = [_code_attempt(time.time(), "spring/di")]
    profile = {"open_follow_up_queue": [{"question": "후속?", "learning_points": []}]}

    t0 = time.perf_counter()
    for _ in range(1000):
        select_cognitive_trigger(
            history=history,
            profile=profile,
            pending_triggers={},
            now=now,
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 50.0


def test_bin_ask_persists_trigger_only_in_learning_mode(tmp_path: Path) -> None:
    ts = time.time()

    def seed(root: Path) -> None:
        learner = root / "learner"
        learner.mkdir(parents=True)
        (learner / "history.jsonl").write_text(
            json.dumps(_code_attempt(ts), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    dev_root = tmp_path / "dev"
    learn_root = tmp_path / "learn"
    seed(dev_root)
    seed(learn_root)

    base_cmd = [
        sys.executable,
        str(REPO_ROOT / "bin" / "ask"),
        "gradle 빌드",
        "--no-daemon",
        "--json",
        "--state-root",
    ]
    dev = subprocess.run(
        [*base_cmd, str(dev_root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "WOOWA_SESSION_MODE": "development"},
        timeout=5,
    )
    learn = subprocess.run(
        [*base_cmd, str(learn_root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "WOOWA_SESSION_MODE": "learning"},
        timeout=5,
    )

    assert dev.returncode == 0
    assert learn.returncode == 0
    assert not (dev_root / "learner" / "pending_triggers.json").exists()
    assert "cognitive_trigger" not in json.loads(dev.stdout)["markdown"]
    pending = json.loads(
        (learn_root / "learner" / "pending_triggers.json").read_text(encoding="utf-8")
    )
    assert pending["self_assessment"]["trigger_type"] == "self_assessment"
    assert "cognitive_trigger" in json.loads(learn.stdout)["markdown"]


# --- W8: proactive drill-offer trigger -------------------------------------

_W8_NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)
_W8_UNCERTAIN = ["spring/a", "spring/b", "spring/c"]  # >= N_PROACTIVE_UNCERTAIN


def test_proactive_drill_fires_for_accumulated_uncertain() -> None:
    t = select_cognitive_trigger(
        history=[], profile={}, drill_pending=None, drill_history=[],
        uncertain_concepts=_W8_UNCERTAIN, last_drill_offer_at=None, now=_W8_NOW,
    )
    assert t["trigger_type"] == "proactive_drill"
    assert t["payload"]["concept_ids"] == _W8_UNCERTAIN
    assert "proactive_drill" in t["competed_against"]


def test_proactive_drill_loses_to_self_assessment() -> None:
    # Lowest priority: a competing self_assessment (recent code attempt) wins.
    t = select_cognitive_trigger(
        history=[_code_attempt(_W8_NOW.timestamp())], profile={}, drill_pending=None,
        uncertain_concepts=_W8_UNCERTAIN, now=_W8_NOW,
    )
    assert t["trigger_type"] == "self_assessment"


def test_proactive_drill_suppressed_when_drill_pending() -> None:
    t = select_cognitive_trigger(
        history=[], profile={}, drill_pending={"drill_session_id": "d1"},
        uncertain_concepts=_W8_UNCERTAIN, now=_W8_NOW,
    )
    assert t["trigger_type"] == "none"  # FSM early-exit on existing drill_pending


def test_proactive_drill_needs_n_uncertain() -> None:
    t = select_cognitive_trigger(
        history=[], profile={}, drill_pending=None,
        uncertain_concepts=["spring/a", "spring/b"], now=_W8_NOW,  # only 2 < 3
    )
    assert t["trigger_type"] != "proactive_drill"


def test_proactive_drill_frequency_cap_offer_marker() -> None:
    within = select_cognitive_trigger(
        history=[], profile={}, drill_pending=None, uncertain_concepts=_W8_UNCERTAIN,
        last_drill_offer_at=_W8_NOW.timestamp() - 3600, now=_W8_NOW,  # 1h < 24h
    )
    assert within["trigger_type"] != "proactive_drill"
    outside = select_cognitive_trigger(
        history=[], profile={}, drill_pending=None, uncertain_concepts=_W8_UNCERTAIN,
        last_drill_offer_at=_W8_NOW.timestamp() - 25 * 3600, now=_W8_NOW,  # 25h
    )
    assert outside["trigger_type"] == "proactive_drill"


def test_proactive_drill_recent_drill_answer_suppresses() -> None:
    history = [{"event_type": "drill_answer", "ts": _W8_NOW.timestamp() - 3600}]  # 1h
    t = select_cognitive_trigger(
        history=history, profile={}, drill_pending=None,
        uncertain_concepts=_W8_UNCERTAIN, now=_W8_NOW,
    )
    assert t["trigger_type"] != "proactive_drill"
