"""Minimal JSON state store — learner-wide + per-repo (D15).

Layout:
  state/learner/identity.json      # immutable login / display name
  state/learner/profile.json       # mastery + drill_due + pending_triggers
  state/learner/history.jsonl      # append-only event log
  state/repos/<repo>/state.json    # working_copy + target_pr + threads + active_session

All structures are intentionally dict-shaped so AI session prompt can pass
them verbatim without per-field marshalling.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"


@dataclass
class LearnerProfile:
    learner_id: str
    mastered_concepts: list[str] = field(default_factory=list)
    uncertain_concepts: list[str] = field(default_factory=list)
    drill_due: list[dict] = field(default_factory=list)
    pending_triggers: dict = field(default_factory=dict)
    total_events: int = 0
    last_updated: float = 0.0

    @classmethod
    def empty(cls, learner_id: str) -> "LearnerProfile":
        return cls(learner_id=learner_id, last_updated=time.time())


@dataclass
class RepoState:
    repo_name: str
    working_copy: dict = field(default_factory=dict)
    target_pr_number: int | None = None
    threads: list[dict] = field(default_factory=list)
    active_session_id: str | None = None
    last_updated: float = 0.0


def _profile_path(state_root: Path) -> Path:
    return state_root / "learner" / "profile.json"


def _repo_state_path(state_root: Path, repo: str) -> Path:
    return state_root / "repos" / repo / "state.json"


def _history_path(state_root: Path) -> Path:
    return state_root / "learner" / "history.jsonl"


def load_profile(learner_id: str, state_root: Path = DEFAULT_STATE_ROOT) -> LearnerProfile:
    p = _profile_path(state_root)
    if not p.exists():
        return LearnerProfile.empty(learner_id)
    mtime = p.stat().st_mtime
    data = _profile_json_cached(str(p), mtime)
    return LearnerProfile(
        learner_id=data.get("learner_id", learner_id),
        mastered_concepts=list(data.get("mastered_concepts", [])),
        uncertain_concepts=list(data.get("uncertain_concepts", [])),
        drill_due=list(data.get("drill_due", [])),
        pending_triggers=dict(data.get("pending_triggers", {})),
        total_events=data.get("total_events", 0),
        last_updated=data.get("last_updated", 0.0),
    )


@lru_cache(maxsize=4)
def _profile_json_cached(path_str: str, mtime: float) -> dict:
    """mtime-keyed cache — auto-invalidates on profile write."""
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def save_profile(profile: LearnerProfile, state_root: Path = DEFAULT_STATE_ROOT) -> None:
    p = _profile_path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    profile.last_updated = time.time()
    p.write_text(json.dumps(profile.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")


def load_repo_state(repo: str, state_root: Path = DEFAULT_STATE_ROOT) -> RepoState:
    p = _repo_state_path(state_root, repo)
    if not p.exists():
        return RepoState(repo_name=repo)
    data = json.loads(p.read_text(encoding="utf-8"))
    return RepoState(
        repo_name=data.get("repo_name", repo),
        working_copy=data.get("working_copy", {}),
        target_pr_number=data.get("target_pr_number"),
        threads=data.get("threads", []),
        active_session_id=data.get("active_session_id"),
        last_updated=data.get("last_updated", 0.0),
    )


def save_repo_state(state: RepoState, state_root: Path = DEFAULT_STATE_ROOT) -> None:
    p = _repo_state_path(state_root, state.repo_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    state.last_updated = time.time()
    p.write_text(json.dumps(state.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history_event(event: dict, state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """Append one event line to history.jsonl. Caller supplies event_id + ts."""
    p = _history_path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    event = {**event, "ts": event.get("ts", time.time())}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_history(
    state_root: Path = DEFAULT_STATE_ROOT,
    tail: int | None = None,
) -> list[dict]:
    """Read events from history.jsonl.

    When tail=N, seek from end and parse only the last N events
    (avoids parsing the full file — 9999 lines parse ~77ms → ~5ms).
    """
    p = _history_path(state_root)
    if not p.exists():
        return []
    if tail is None:
        # Full read (back-compat)
        out: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    # Tail mode — seek-from-end, retry with larger chunk if we don't get
    # `tail` complete events back (event sizes vary 277-2274 bytes empirically).
    size = p.stat().st_size
    bytes_per_event = 1500  # safe upper-band estimate
    for attempt in range(4):  # 1500 → 3000 → 6000 → 12000 bytes/event
        chunk = min(size, max(tail * bytes_per_event, 4096))
        with p.open("rb") as f:
            f.seek(max(0, size - chunk))
            data = f.read()
        raw_lines = data.decode("utf-8", errors="replace").splitlines()
        if size > chunk and raw_lines:
            # First line may be partial — drop it
            raw_lines = raw_lines[1:]
        valid_lines = [L for L in raw_lines if L.strip()]
        if len(valid_lines) >= tail or chunk >= size:
            break
        bytes_per_event *= 2
    out: list[dict] = []
    for line in valid_lines[-tail:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
