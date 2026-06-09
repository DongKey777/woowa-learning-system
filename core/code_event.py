"""Phase T2: learn-record-code — code attempt event recorder with auto
concept inference.

AI session calls this after Write/Edit on a learner mission file:
- Reads file content (if path exists) → mission.extract.extract_from_text
  → distinct concept_ids
- Appends `code_attempt` event to history.jsonl with file_path, summary,
  diff stats, linked_test, inferred concept_ids
- Routes through core/feedback.record_turn so Bloom autoloop picks up the
  mission_use evidence

Public API:
  record_code_attempt(file_path, summary, ...) -> dict (the event)
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from core.feedback import record_turn
from core.state import DEFAULT_STATE_ROOT, append_history_event


def infer_repo_from_path(file_path: str) -> str | None:
    """Infer mission repo name from missions/<repo>/... paths."""
    parts = Path(file_path).parts
    for index, part in enumerate(parts):
        if part == "missions" and index + 1 < len(parts):
            repo = parts[index + 1]
            if repo and repo not in {".", ".."}:
                return repo
    return None


def infer_concepts_from_file(file_path: str) -> list[str]:
    """Read file (if exists) and run mission.extract.extract_from_text.

    Returns distinct matched_concept_id list. Empty list on missing/unparseable.
    """
    from mission.extract import extract_from_text  # lazy import

    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    patterns = extract_from_text(file_path, text)
    seen = []
    for pat in patterns:
        cid = pat.matched_concept_id
        if cid and cid not in seen:
            seen.append(cid)
    return seen


def record_code_attempt(
    file_path: str,
    summary: str,
    *,
    repo: str | None = None,
    lines_added: int | None = None,
    lines_removed: int | None = None,
    linked_test: str | None = None,
    explicit_concept_ids: list[str] | None = None,
    learner_id: str = "default",
    mode: str = "learning",
    state_root: Path = DEFAULT_STATE_ROOT,
    now: float | None = None,
) -> dict:
    """Append code_attempt event with concept inference. Returns the event."""
    ts = now or time.time()
    concept_ids = explicit_concept_ids if explicit_concept_ids else \
        infer_concepts_from_file(file_path)
    resolved_repo = repo or infer_repo_from_path(file_path)

    event_id = f"code_attempt-{int(ts * 1000)}-{uuid.uuid4().hex[:6]}"
    event = {
        "event_id": event_id,
        "event_type": "code_attempt",
        "mode": mode,
        "ts": ts,
        "learner_id": learner_id,
        "repo": resolved_repo,
        "payload": {
            "file_path": file_path,
            "summary": summary,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "linked_test": linked_test,
            "concept_ids": concept_ids,
            "concept_source": "explicit" if explicit_concept_ids else "auto_inferred",
        },
    }
    append_history_event(event, state_root=state_root)
    # Bloom autoloop — only for learning mode
    if mode == "learning":
        record_turn(event, state_root=state_root)
    return event
