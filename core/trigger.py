"""Cognitive trigger priority FSM for learning turns.

The selector is intentionally side-effect free: it chooses one learner-facing
trigger, while callers decide whether to persist pending state.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from core.state import DEFAULT_STATE_ROOT

TriggerType = Literal["self_assessment", "review_drill", "follow_up", "none"]

RECENT_CODE_WINDOW_SECS = 86_400


def _empty_trigger(*, competed_against: list[str] | None = None) -> dict[str, Any]:
    return {
        "trigger_type": "none",
        "trigger_session_id": None,
        "markdown": None,
        "payload": {},
        "applicability_hint": "omit",
        "reason": "none",
        "evidence": {},
        "competed_against": competed_against or [],
    }


def _pending_triggers_path(state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    return state_root / "learner" / "pending_triggers.json"


def load_pending_triggers(state_root: Path = DEFAULT_STATE_ROOT) -> dict[str, Any]:
    """Read learner pending cognitive triggers, returning {} on cold start."""
    path = _pending_triggers_path(state_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_pending_triggers_atomic(
    triggers: dict[str, Any],
    state_root: Path = DEFAULT_STATE_ROOT,
) -> None:
    """Atomically persist pending cognitive triggers."""
    path = _pending_triggers_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    closed = False
    try:
        os.write(
            fd,
            (json.dumps(triggers, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        os.fsync(fd)
        os.close(fd)
        closed = True
        os.replace(tmp, path)
    except BaseException:
        if not closed:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_epoch(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _event_ts(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed is not None else 0.0


def expire_stale_triggers(
    pending: dict[str, Any],
    now: datetime,
    *,
    ttl_hours: int = 24,
) -> dict[str, Any]:
    """Return pending triggers with stale entries removed."""
    out: dict[str, Any] = {}
    for trigger_type, payload in (pending or {}).items():
        if not isinstance(payload, dict):
            continue
        expires_at = _parse_iso(payload.get("expires_at"))
        issued_at = _parse_iso(payload.get("issued_at"))
        if expires_at is not None:
            if expires_at > now:
                out[trigger_type] = payload
            continue
        if issued_at is not None and issued_at + timedelta(hours=ttl_hours) > now:
            out[trigger_type] = payload
    return out


def match_pending_trigger(
    pending: dict[str, Any],
    trigger_type: str,
    response_payload: dict[str, Any] | None,
) -> str | None:
    """Return matching trigger_session_id for a response payload."""
    candidate = (pending or {}).get(trigger_type)
    if not isinstance(candidate, dict):
        return None
    trigger_session_id = candidate.get("trigger_session_id")
    if not trigger_session_id:
        return None
    response_id = (response_payload or {}).get("trigger_session_id")
    if response_id and response_id != trigger_session_id:
        return None
    return str(trigger_session_id)


def load_profile_payload(
    state_root: Path = DEFAULT_STATE_ROOT,
    repo: str | None = None,
) -> dict[str, Any]:
    """Load raw learner profile plus repo memory follow-up queue if available."""
    profile: dict[str, Any] = {}
    learner_path = state_root / "learner" / "profile.json"
    if learner_path.exists():
        try:
            loaded = json.loads(learner_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                profile.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    if repo:
        repo_profile = state_root / "repos" / repo / "memory" / "profile.json"
        if repo_profile.exists():
            try:
                loaded = json.loads(repo_profile.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("open_follow_up_queue"):
                    profile["open_follow_up_queue"] = loaded.get("open_follow_up_queue")
            except (OSError, json.JSONDecodeError):
                pass
    return profile


def load_drill_history(state_root: Path = DEFAULT_STATE_ROOT) -> list[dict[str, Any]]:
    """Load v2 spaced-repetition due list in selector-friendly shape."""
    path = state_root / "learner" / "drill_due.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _recent_code_changes(
    profile: dict[str, Any] | None,
    history: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    raw = (profile or {}).get("recent_code_changes_24h")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]

    cutoff = now.timestamp() - RECENT_CODE_WINDOW_SECS
    out: list[dict[str, Any]] = []
    for event in history or []:
        if not isinstance(event, dict) or event.get("event_type") != "code_attempt":
            continue
        ts = _event_ts(event.get("ts"))
        if ts < cutoff:
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        concept_ids = payload.get("concept_ids") or []
        if payload.get("concept_id"):
            concept_ids = [payload["concept_id"], *concept_ids]
        out.append({
            "event_id": event.get("event_id"),
            "ts": ts,
            "file_path": payload.get("file_path"),
            "concept_ids": [cid for cid in concept_ids if cid],
            "summary": payload.get("summary"),
        })
    return out


def _assessed_concepts(
    profile: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> set[str]:
    assessed: set[str] = set()
    for item in ((profile or {}).get("calibration_status") or {}).get(
        "recent_self_assessments",
        [],
    ):
        if isinstance(item, dict):
            assessed.update(cid for cid in item.get("concept_ids", []) if cid)
    for event in history or []:
        if not isinstance(event, dict) or event.get("event_type") != "self_assessment":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        assessed.update(cid for cid in payload.get("concept_ids", []) if cid)
        if payload.get("concept_id"):
            assessed.add(payload["concept_id"])
    return assessed


def _follow_up_candidate(
    *,
    profile: dict[str, Any] | None,
    **_: Any,
) -> dict[str, Any] | None:
    queue = (profile or {}).get("open_follow_up_queue") or []
    items = [
        item for item in queue
        if isinstance(item, dict) and (item.get("question") or "").strip()
    ]
    if not items:
        return None
    first = items[0]
    question = str(first.get("question") or "").strip()
    markdown = "## 이어서 보면 좋은 질문\n" f"- {question}"
    learning_points = list(first.get("learning_points") or [])
    if learning_points:
        markdown += "\n" f"  - 관련 학습 포인트: {', '.join(str(p) for p in learning_points)}"
    return {
        "trigger_type": "follow_up",
        "trigger_session_id": None,
        "markdown": markdown,
        "payload": {"item": first},
        "applicability_hint": "supporting",
        "reason": "open_follow_up",
        "evidence": {
            "source": "learning_profile.open_follow_up_queue",
            "queue_size": len(items),
        },
        "competed_against": [],
    }


def _self_assessment_due_candidate(
    *,
    history: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    pending_triggers: dict[str, Any] | None,
    now: datetime,
    **_: Any,
) -> dict[str, Any] | None:
    if (pending_triggers or {}).get("self_assessment"):
        return None
    recent_changes = _recent_code_changes(profile, history, now)
    if not recent_changes:
        return None
    assessed = _assessed_concepts(profile, history)
    for change in reversed(recent_changes):
        concept_ids = [
            concept_id
            for concept_id in (change.get("concept_ids") or [])
            if concept_id not in assessed
        ]
        if concept_ids:
            file_path = change.get("file_path") or "최근 변경"
            return {
                "trigger_type": "self_assessment",
                "trigger_session_id": str(uuid.uuid4()),
                "markdown": (
                    "## 자기 점검\n"
                    f"- 방금 다룬 `{file_path}` 변경 기준으로 이해도를 1-10점으로 적어줘."
                ),
                "payload": {
                    "concept_ids": concept_ids,
                    "source_event_id": change.get("event_id"),
                    "file_path": file_path,
                },
                "applicability_hint": "supporting",
                "reason": "recent_code_attempt_without_self_assessment",
                "evidence": {
                    "source": "history.code_attempt",
                    "code_attempt_ts": change.get("ts"),
                },
                "competed_against": [],
            }
    return None


def _entry_due_at(entry: dict[str, Any]) -> datetime | None:
    for key in ("due_at", "next_due_at"):
        due_at = _parse_iso(entry.get(key))
        if due_at is not None:
            return due_at
    for key in ("next_due_ts", "due_ts"):
        due_at = _parse_epoch(entry.get(key))
        if due_at is not None:
            return due_at
    return None


def _review_question(entry: dict[str, Any]) -> str:
    question = (entry.get("question") or "").strip()
    if question:
        return question
    concept_id = entry.get("concept_id") or entry.get("linked_learning_point")
    if concept_id:
        return f"`{concept_id}` 핵심을 다시 설명해줘."
    return "이전 드릴의 핵심을 다시 설명해줘."


def _review_due_candidate(
    *,
    drill_history: list[dict[str, Any]] | None,
    drill_pending: dict[str, Any] | None,
    now: datetime,
    **_: Any,
) -> dict[str, Any] | None:
    if drill_pending is not None:
        return None
    reference = now
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    earliest_due: tuple[datetime, int, dict[str, Any]] | None = None
    for index, entry in enumerate(drill_history or []):
        if not isinstance(entry, dict):
            continue
        due_at = _entry_due_at(entry)
        if due_at is None or due_at > reference:
            continue
        candidate = (due_at, index, entry)
        if earliest_due is None or (
            candidate[0],
            candidate[1],
        ) < (earliest_due[0], earliest_due[1]):
            earliest_due = candidate
    if earliest_due is None:
        return None

    due_at, _, entry = earliest_due
    question = _review_question(entry)
    return {
        "trigger_type": "review_drill",
        "trigger_session_id": str(uuid.uuid4()),
        "markdown": "## 복습 드릴\n" f"- 이전 드릴을 다시 풀어볼 차례야: {question}",
        "payload": {
            "review_of_session_id": entry.get("review_of_session_id")
            or entry.get("drill_session_id"),
            "linked_learning_point": entry.get("linked_learning_point")
            or entry.get("concept_id"),
            "due_at": due_at.isoformat(),
        },
        "applicability_hint": "supporting",
        "reason": "spaced_repetition_due",
        "evidence": {
            "source": "drill_due",
            "due_at": due_at.isoformat(),
            "drill_session_id": entry.get("drill_session_id"),
        },
        "competed_against": [],
    }


N_PROACTIVE_UNCERTAIN = 3                   # W8: min accumulated uncertain concepts
PROACTIVE_DRILL_COOLDOWN_SECS = 24 * 3600   # W8: no re-offer within this window


def _last_drill_answer_ts(history: list[dict[str, Any]] | None) -> float:
    latest = 0.0
    for ev in history or []:
        if ev.get("event_type") == "drill_answer":
            ts = ev.get("ts")
            if isinstance(ts, (int, float)) and ts > latest:
                latest = float(ts)
    return latest


def _proactive_drill_candidate(
    *,
    uncertain_concepts: list[str] | None = None,
    drill_pending: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    last_drill_offer_at: float | None = None,
    now: datetime,
    **_: Any,
) -> dict[str, Any] | None:
    """W8: proactively offer a forward drill when uncertain concepts accumulate and
    no drill activity is within the cooldown. Side-effect free (the daemon branch
    writes drill_pending.json + the offer marker). Sits LAST in the priority FSM."""
    if drill_pending is not None:
        return None
    uncertain = [c for c in (uncertain_concepts or []) if c]
    if len(uncertain) < N_PROACTIVE_UNCERTAIN:
        return None
    last_activity = max(last_drill_offer_at or 0.0, _last_drill_answer_ts(history))
    if now.timestamp() - last_activity < PROACTIVE_DRILL_COOLDOWN_SECS:
        return None
    return {
        "trigger_type": "proactive_drill",
        "trigger_session_id": str(uuid.uuid4()),
        "markdown": (
            "## 확인 드릴 제안\n"
            "- 헷갈리는 개념이 좀 쌓였어. 짧은 확인 드릴 하나 풀어볼까?"
        ),
        "payload": {"concept_ids": uncertain},
        "applicability_hint": "supporting",
        "reason": "uncertain_concepts_accumulated",
        "evidence": {"source": "profile.uncertain_concepts", "n_uncertain": len(uncertain)},
        "competed_against": [],
    }


_CANDIDATE_REGISTRY: dict[str, dict[str, Any]] = {
    "self_assessment_due": {"enabled": True, "fn": _self_assessment_due_candidate},
    "review_due": {"enabled": True, "fn": _review_due_candidate},
    "follow_up": {"enabled": True, "fn": _follow_up_candidate},
    "proactive_drill": {"enabled": True, "fn": _proactive_drill_candidate},
}


def select_cognitive_trigger(
    *,
    history: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
    drill_pending: dict[str, Any] | None = None,
    drill_history: list[dict[str, Any]] | None = None,
    intent: dict[str, Any] | None = None,
    learner_context: dict[str, Any] | None = None,
    pending_triggers: dict[str, Any] | None = None,
    input_signals: dict[str, Any] | None = None,
    uncertain_concepts: list[str] | None = None,
    last_drill_offer_at: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Select at most one cognitive trigger for the current turn."""
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    competed_against: list[str] = []
    detected_intent = (
        (intent or {}).get("pre_intent")
        or (intent or {}).get("detected_intent")
    )
    if drill_pending is not None or detected_intent in {
        "drill",
        "drill_answer",
        "mixed_with_drill_answer",
        "self_assess",
        "self_assessment",
    }:
        return _empty_trigger(competed_against=competed_against)

    context = {
        "history": history or [],
        "profile": profile,
        "drill_pending": drill_pending,
        "drill_history": drill_history or [],
        "intent": intent or {},
        "learner_context": learner_context,
        "pending_triggers": pending_triggers or {},
        "input_signals": input_signals or {},
        "uncertain_concepts": uncertain_concepts or [],
        "last_drill_offer_at": last_drill_offer_at,
        "now": reference,
    }
    for name in ("self_assessment_due", "review_due", "follow_up", "proactive_drill"):
        spec = _CANDIDATE_REGISTRY.get(name) or {}
        if not spec.get("enabled"):
            continue
        competed_against.append(name)
        fn: Callable[..., dict[str, Any] | None] = spec["fn"]
        candidate = fn(**context)
        if candidate:
            candidate["competed_against"] = list(competed_against)
            if (
                not candidate.get("trigger_session_id")
                and candidate.get("trigger_type") in {"self_assessment", "review_drill"}
            ):
                candidate["trigger_session_id"] = str(uuid.uuid4())
            return candidate
    return _empty_trigger(competed_against=competed_against)

