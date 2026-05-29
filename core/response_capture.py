"""Response capture pending-state and hook helpers.

This module keeps the learning UX non-blocking: hooks try to capture the
final assistant answer, but failures are written to a repair queue instead of
stopping the answer flow.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from core.response import parse_response
from core.response_quality import record_response_quality, redact_pii
from core.state import DEFAULT_STATE_ROOT, append_jsonl_locked, atomic_write_json

PENDING_DIR = "pending-captures"
REPAIR_QUEUE = "capture-repair-queue.jsonl"
REPAIR_BODY_DIR = "capture-repair-bodies"


def is_internal_capture_meta_prompt(prompt: str | None) -> bool:
    """Return True for prompts whose only purpose is finding a source id."""
    text = (prompt or "").strip().lower()
    if not text:
        return False
    patterns = (
        "source id",
        "source_event_id",
        "ask source",
        "이 답변에 대한 ask",
        "답변에 대한 ask",
        "source id 받",
        "소스 id",
        "이벤트 id 받",
    )
    return any(p in text for p in patterns)


def pending_capture_dir(state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    return state_root / "learner" / PENDING_DIR


def pending_capture_path(
    source_event_id: str,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> Path:
    return pending_capture_dir(state_root) / f"{source_event_id}.json"


def repair_queue_path(state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    return state_root / "learner" / REPAIR_QUEUE


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _source_event_metadata(ev: dict[str, Any] | None) -> tuple[list[str], str | None]:
    payload = (ev or {}).get("payload") or {}
    cids = payload.get("top_concept_ids") or (ev or {}).get("top_paths") or []
    repo = payload.get("repo") or (ev or {}).get("repo_context") or (ev or {}).get("repo")
    return [c for c in cids if isinstance(c, str)], repo


def create_pending_capture(
    event: dict[str, Any],
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    expected_citation_paths: list[str] | None = None,
) -> dict[str, Any] | None:
    """Create a pending capture row for one learning rag_ask event."""
    if event.get("mode") != "learning" or event.get("event_type") != "rag_ask":
        return None
    payload = event.get("payload") or {}
    if is_internal_capture_meta_prompt(payload.get("prompt")):
        return None
    source_event_id = event.get("event_id")
    if not source_event_id:
        return None
    cids, repo = _source_event_metadata(event)
    pending = {
        "schema_id": "response-capture-pending-v1",
        "source_event_id": source_event_id,
        "repo": payload.get("repo") or repo,
        "prompt": payload.get("prompt"),
        "expected_citation_paths": list(expected_citation_paths or cids),
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "pending",
        "attempts": 0,
    }
    _atomic_write_json(pending_capture_path(source_event_id, state_root), pending)
    return pending


def load_pending_capture(
    source_event_id: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict[str, Any] | None:
    path = pending_capture_path(source_event_id, state_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def latest_pending_capture(
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    statuses = statuses or {"pending", "failed_pending_repair"}
    root = pending_capture_dir(state_root)
    if not root.exists():
        return None
    rows: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if row.get("status") in statuses:
            row["_path"] = str(path)
            rows.append(row)
    if not rows:
        return None
    rows.sort(key=lambda r: (float(r.get("created_at") or 0.0), str(r.get("source_event_id"))))
    return rows[-1]


def update_pending_capture(
    source_event_id: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    status: str | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    row = load_pending_capture(source_event_id, state_root=state_root)
    if row is None:
        return None
    if status:
        row["status"] = status
    row["updated_at"] = time.time()
    if updates:
        row.update(updates)
    _atomic_write_json(pending_capture_path(source_event_id, state_root), row)
    return row


def supersede_older_pending_captures(
    captured_source_event_id: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    window_s: float = 900.0,
) -> int:
    """Close stale pending rows from the same short session window.

    AI clients sometimes call ``bin/ask`` more than once before producing one
    learner-facing answer. The hook can only capture the final answer once, so
    older pending rows in that burst are marked superseded instead of staying
    open forever.
    """
    captured = load_pending_capture(captured_source_event_id, state_root=state_root)
    if not captured:
        return 0
    captured_created = float(captured.get("created_at") or 0.0)
    root = pending_capture_dir(state_root)
    if not root.exists():
        return 0
    count = 0
    for path in root.glob("*.json"):
        if path.stem == captured_source_event_id:
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if row.get("status") != "pending":
            continue
        created = float(row.get("created_at") or 0.0)
        if not created or created > captured_created:
            continue
        if captured_created - created > window_s:
            continue
        row["status"] = "superseded_by_later_capture"
        row["updated_at"] = time.time()
        row["superseded_by"] = captured_source_event_id
        _atomic_write_json(path, row)
        count += 1
    return count


def load_source_event(
    source_event_id: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict[str, Any] | None:
    history = state_root / "learner" / "history.jsonl"
    if not history.exists():
        return None
    target = str(source_event_id).strip()
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip() or f'"{target}"' not in line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event_id") == target:
            return ev
    return None


def append_repair_queue(
    reason: str,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    status: str = "pending_repair",
    source_event_id: str | None = None,
    client: str | None = None,
    details: dict[str, Any] | None = None,
    response_body: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_id": "response-capture-repair-v1",
        "queued_at": time.time(),
        "status": status,
        "reason": reason,
        "source_event_id": source_event_id,
        "client": client,
        "details": details or {},
    }
    if response_body:
        body_path, body_hash = _store_repair_body(state_root, response_body)
        row["repair_body_path"] = body_path
        row["repair_body_hash"] = body_hash
    append_jsonl_locked(repair_queue_path(state_root), row)
    return row


def _store_repair_body(state_root: Path, body: str) -> tuple[str, str]:
    redacted = redact_pii(body)
    body_hash = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    body_path = (
        state_root / "learner" / REPAIR_BODY_DIR / "sha256"
        / body_hash[:2] / f"{body_hash}.md"
    )
    body_path.parent.mkdir(parents=True, exist_ok=True)
    if not body_path.exists():
        body_path.write_text(redacted, encoding="utf-8")
    return str(body_path.relative_to(state_root)), body_hash


def extract_hook_response_body(payload: dict[str, Any], client: str = "auto") -> str | None:
    """Extract final assistant text from common hook payload shapes."""
    preferred = {
        "claude": ("last_assistant_message", "assistant_message", "response"),
        "codex": ("last_assistant_message", "assistant_message", "response"),
        "gemini": ("prompt_response", "response", "final_response"),
        "auto": (
            "last_assistant_message",
            "prompt_response",
            "assistant_message",
            "final_response",
            "response",
            "message",
        ),
    }.get(client, ())
    for key in preferred:
        body = _text_from_value(payload.get(key))
        if body:
            return body
    for key in (
        "last_assistant_message",
        "prompt_response",
        "assistant_message",
        "final_response",
        "response",
        "message",
    ):
        body = _text_from_value(payload.get(key))
        if body:
            return body
    return None


def _text_from_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts = [_text_from_value(item) for item in value]
        text = "\n".join(p for p in parts if p).strip()
        return text or None
    if isinstance(value, dict):
        if value.get("role") == "user":
            return None
        for key in ("text", "content", "body", "message", "response", "output"):
            text = _text_from_value(value.get(key))
            if text:
                return text
    return None


def looks_like_learning_answer_body(body: str | None) -> bool:
    """Return True when a hook body looks like a learner-facing answer."""
    text = (body or "").lstrip()
    return text.startswith("[Mode:")


def capture_from_hook_payload(
    payload: dict[str, Any],
    *,
    client: str = "auto",
    state_root: Path = DEFAULT_STATE_ROOT,
    learner_id: str = "default",
) -> dict[str, Any]:
    """Capture a hook response or enqueue a non-blocking repair row."""
    body = extract_hook_response_body(payload, client=client)
    source_event_id = _source_event_id_from_payload(payload)
    pending = (
        load_pending_capture(source_event_id, state_root=state_root)
        if source_event_id else latest_pending_capture(state_root=state_root)
    )
    if pending and not source_event_id:
        source_event_id = pending.get("source_event_id")

    if not body:
        status = "pending_repair" if pending or source_event_id else "ignored_non_learning"
        append_repair_queue(
            "body_missing",
            state_root=state_root,
            status=status,
            source_event_id=source_event_id,
            client=client,
            details={"payload_keys": sorted(str(k) for k in payload.keys())},
        )
        if source_event_id:
            update_pending_capture(
                source_event_id,
                state_root=state_root,
                status="failed_pending_repair",
                updates={"last_error": "body_missing"},
            )
        return {
            "ok": False,
            "reason": "body_missing",
            "source_event_id": source_event_id,
            "ignored": status != "pending_repair",
        }

    if not pending or not source_event_id:
        if not looks_like_learning_answer_body(body):
            append_repair_queue(
                "non_learning_hook_message",
                state_root=state_root,
                status="ignored_non_learning",
                source_event_id=source_event_id,
                client=client,
                details={"payload_keys": sorted(str(k) for k in payload.keys())},
            )
            return {
                "ok": True,
                "ignored": True,
                "reason": "non_learning_hook_message",
                "source_event_id": source_event_id,
            }
        append_repair_queue(
            "pending_missing",
            state_root=state_root,
            source_event_id=source_event_id,
            client=client,
            details={"payload_keys": sorted(str(k) for k in payload.keys())},
            response_body=body,
        )
        return {"ok": False, "reason": "pending_missing", "source_event_id": source_event_id}

    ev = load_source_event(source_event_id, state_root=state_root)
    if ev is None:
        append_repair_queue(
            "orphan_source_event",
            state_root=state_root,
            source_event_id=source_event_id,
            client=client,
            response_body=body,
        )
        update_pending_capture(
            source_event_id,
            state_root=state_root,
            status="failed_pending_repair",
            updates={"last_error": "orphan_source_event"},
        )
        return {"ok": False, "reason": "orphan_source_event", "source_event_id": source_event_id}

    expected, repo = _source_event_metadata(ev)
    expected = list(pending.get("expected_citation_paths") or expected)
    declared = parse_response(body).citations
    row = record_response_quality(
        source_event_id=source_event_id,
        response_summary=_derive_summary(body),
        response_body=body,
        expected_citation_paths=expected,
        declared_citation_paths=declared,
        repo=pending.get("repo") or repo,
        learner_id=learner_id,
        state_root=state_root,
        capture_method=f"hook_{client}",
        capture_input_chars=0,
    )
    update_pending_capture(
        source_event_id,
        state_root=state_root,
        status="captured",
        updates={
            "captured_at": row["logged_at"],
            "response_body_path": row["response_body_path"],
            "quality_flags": row["quality_flags"],
        },
    )
    superseded_n = supersede_older_pending_captures(
        source_event_id,
        state_root=state_root,
    )
    drain_summary: dict[str, int] | None = None
    try:
        drain_summary = drain_repair_queue(
            state_root=state_root,
            learner_id=learner_id,
            limit=20,
            apply=True,
        )
    except Exception:  # noqa: BLE001
        drain_summary = None  # opportunistic — never break the response path
    return {
        "ok": True,
        "source_event_id": source_event_id,
        "response_body_path": row["response_body_path"],
        "quality_flags": row["quality_flags"],
        "superseded_pending_n": superseded_n,
        "drained_repaired_n": (drain_summary or {}).get("applied", 0),
        "drain_errors": (drain_summary or {}).get("drain_errors", 0),
    }


def drain_repair_queue(
    state_root: Path = DEFAULT_STATE_ROOT,
    *,
    learner_id: str = "default",
    limit: int = 50,
    apply: bool = True,
) -> dict[str, int]:
    """Replay repairable failures from capture-repair-queue.jsonl.

    Idempotent — skips rows whose source_event_id already has a quality entry.
    Used by both bin/capture-repair (explicit) and capture_from_hook_payload's
    success path (opportunistic), so successful hooks gradually drain backlog
    without a separate cron/daemon job.
    """
    queue_path = repair_queue_path(state_root)
    if not queue_path.exists():
        return {"scanned": 0, "repairable": 0, "applied": 0, "unrecoverable": 0, "drain_errors": 0}

    rows: list[dict[str, Any]] = []
    for line in queue_path.read_text(encoding="utf-8").splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)

    quality_path = state_root / "learner" / "response-quality.jsonl"
    existing: set[str] = set()
    if quality_path.exists():
        for line in quality_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = d.get("source_event_id") if isinstance(d, dict) else None
            if sid:
                existing.add(sid)

    repairable: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    unrecoverable = 0
    for row in rows:
        if row.get("status") != "pending_repair":
            continue
        sid = row.get("source_event_id")
        body_rel = row.get("repair_body_path")
        ev = load_source_event(sid, state_root=state_root) if sid else None
        if not sid or not body_rel or ev is None:
            unrecoverable += 1
            continue
        if sid in existing:
            continue
        body_path = state_root / body_rel
        if not body_path.exists():
            unrecoverable += 1
            continue
        repairable.append((row, ev, body_path))

    applied = 0
    drain_errors = 0
    if apply and repairable:
        from core.response import parse_response
        from core.response_quality import record_response_quality

        for row, ev, body_path in repairable:
            try:
                body = body_path.read_text(encoding="utf-8")
                expected, repo = _source_event_metadata(ev)
                quality = record_response_quality(
                    source_event_id=row["source_event_id"],
                    response_summary="auto-repaired full response capture",
                    response_body=body,
                    expected_citation_paths=expected,
                    declared_citation_paths=parse_response(body).citations,
                    repo=repo,
                    learner_id=learner_id,
                    state_root=state_root,
                    capture_method="repair_queue",
                    capture_input_chars=0,
                )
                update_pending_capture(
                    row["source_event_id"],
                    state_root=state_root,
                    status="captured",
                    updates={
                        "captured_at": quality["logged_at"],
                        "response_body_path": quality["response_body_path"],
                        "quality_flags": quality["quality_flags"],
                        "repaired": True,
                    },
                )
                append_repair_queue(
                    "drain_repaired",
                    state_root=state_root,
                    status="repaired",
                    source_event_id=row["source_event_id"],
                    details={"repair_body_path": row.get("repair_body_path")},
                )
                existing.add(row["source_event_id"])
                applied += 1
            except Exception:  # noqa: BLE001
                # Drain failures must never propagate — keep response flow alive.
                # Counted (not silent): the row stays queued for the next drain.
                drain_errors += 1
                continue

    return {
        "scanned": len(rows),
        "repairable": len(repairable),
        "applied": applied,
        "unrecoverable": unrecoverable,
        "drain_errors": drain_errors,
    }


def _source_event_id_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("source_event_id", "event_id", "turn_source_event_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    telemetry = payload.get("telemetry")
    if isinstance(telemetry, dict):
        value = telemetry.get("source_event_id") or telemetry.get("event_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _derive_summary(body: str) -> str:
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("[Mode:") or line.startswith("# response_"):
            continue
        if line == "참고:" or line.startswith("- "):
            continue
        lines.append(line)
        if len(" ".join(lines)) >= 120:
            break
    return (" ".join(lines) or "auto-captured full response")[:300]
