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
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"

EPHEMERAL_PENDING_KEYS = frozenset({"review_drill"})
DRILL_PENDING_TTL_SECS = 24 * 3600  # W8: stale drill offer expiry (epoch created_at)
BEGINNER_FLAGS_FILE = "beginner_flags.json"  # W7: learner self-declared beginner concepts
VALID_LEARNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,38}$")

def _validate_learner_id(learner_id: str) -> None:
    if learner_id in {None, "", "default"} or "@" in str(learner_id):
        raise ValueError(f"invalid learner_id: {learner_id!r}; use resolve_learner_login")
    if not VALID_LEARNER_ID_RE.match(str(learner_id)):
        raise ValueError(f"invalid learner_id: {learner_id!r}")


@dataclass
class LearnerProfile:
    learner_id: str
    mastered_concepts: list[str] = field(default_factory=list)
    uncertain_concepts: list[str] = field(default_factory=list)
    drill_due: list[dict] = field(default_factory=list)
    pending_triggers: dict = field(default_factory=dict)
    total_events: int = 0
    last_updated: float = 0.0
    proficient_concepts: list[str] = field(default_factory=list)
    self_declared_beginner_concepts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_learner_id(self.learner_id)

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


def _load_pending_triggers(state_root: Path, now: datetime | None = None) -> dict:
    """Merge learner-level pending_triggers.json + drill_pending.json.

    review_drill comes from drill_pending.json (ephemeral, not stored in
    profile). Other pending keys come from pending_triggers.json.

    pending_triggers.json entries that carry their own timestamps
    (expires_at / issued_at) are expired on load (W1) so a stale
    self_assessment never blocks new offers or hijacks a score-like reply.
    Timestamp-less entries, and review_drill, are never dropped.
    """
    triggers: dict = {}
    pt = state_root / "learner" / "pending_triggers.json"
    if pt.exists():
        try:
            raw = json.loads(pt.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            raw = {}
        if raw:
            from datetime import datetime, timezone

            from core.trigger import expire_stale_triggers  # lazy: avoid import cycle

            if now is None:
                now = datetime.now(timezone.utc)
            # Only timestamped entries are subject to expiry; preserve
            # timestamp-less entries untouched (expire_stale_triggers would
            # otherwise drop them). review_drill is merged separately below.
            timestamped = {
                k: v
                for k, v in raw.items()
                if isinstance(v, dict) and (v.get("expires_at") or v.get("issued_at"))
            }
            for key, value in raw.items():
                if key not in timestamped:
                    triggers[key] = value
            triggers.update(expire_stale_triggers(timestamped, now))
    dp = state_root / "learner" / "drill_pending.json"
    if dp.exists() and "review_drill" not in triggers:
        try:
            offer = json.loads(dp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            offer = None
        if isinstance(offer, dict):
            # W8: drill_pending TTL on epoch created_at (disjoint from W1's ISO
            # expiry above). A stale offer is dropped AND its file unlinked so it
            # stops hijacking cs_qa (intent.py DRILL_ANSWER_HINT) and unblocks
            # build_offer_if_due's one-open guard. A timestamp-less offer is kept
            # (real offers always carry created_at; honors the review_drill contract).
            now_epoch = now.timestamp() if now is not None else time.time()
            created_at = offer.get("created_at")
            if isinstance(created_at, (int, float)) and now_epoch - created_at > DRILL_PENDING_TTL_SECS:
                try:
                    dp.unlink()
                except OSError:
                    pass
            else:
                triggers["review_drill"] = offer
    return triggers


def _is_v3_profile(data: dict) -> bool:
    """v3 schema: `concepts.*` wrapper + `schema_version: v3*`."""
    return (
        str(data.get("schema_version", "")).startswith("v3")
        or "concepts" in data
    )


def load_beginner_flags(state_root: Path = DEFAULT_STATE_ROOT) -> list[str]:
    """W7: learner self-declared 'beginner' concepts. Subtracted from
    must_skip_explanations_of so a self-declared-beginner concept is always
    re-explained even if mastery over-promoted it. Written by the AI session via
    bin/learn-beginner-flag (AI judgment, not a keyword filter)."""
    p = state_root / "learner" / BEGINNER_FLAGS_FILE
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    raw = data.get("concepts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []  # malformed (non-list) → ignore, don't per-char/key-iterate
    return [str(c) for c in raw if c]


def save_beginner_flags(concepts: list[str], state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """Persist the self-declared-beginner concept list (deduped, order-preserving)."""
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    deduped = list(dict.fromkeys(str(c) for c in concepts if c))
    atomic_write_json(d / BEGINNER_FLAGS_FILE, {"concepts": deduped})


def load_profile(learner_id: str, state_root: Path = DEFAULT_STATE_ROOT) -> LearnerProfile:
    p = _profile_path(state_root)
    if not p.exists():
        return LearnerProfile.empty(learner_id)
    mtime = p.stat().st_mtime
    data = _profile_json_cached(str(p), mtime)
    beginner = load_beginner_flags(state_root)

    if _is_v3_profile(data):
        concepts = data.get("concepts", {}) or {}
        activity = data.get("activity", {}) or {}
        merged_pending = dict(data.get("pending_triggers", {}) or {})
        merged_pending.update(_load_pending_triggers(state_root))
        return LearnerProfile(
            learner_id=data.get("learner_id", learner_id),
            mastered_concepts=list(concepts.get("mastered", [])),
            uncertain_concepts=list(concepts.get("uncertain", [])),
            proficient_concepts=list(concepts.get("proficient", [])),
            self_declared_beginner_concepts=beginner,
            drill_due=list(data.get("drill_due", [])),
            pending_triggers=merged_pending,
            total_events=int(activity.get("events_total", 0) or 0),
            last_updated=float(
                data.get("last_updated",
                          data.get("computed_at", 0.0)) or 0.0
            ),
        )

    # Legacy flat schema
    return LearnerProfile(
        learner_id=data.get("learner_id", learner_id),
        mastered_concepts=list(data.get("mastered_concepts", [])),
        uncertain_concepts=list(data.get("uncertain_concepts", [])),
        proficient_concepts=list(data.get("proficient_concepts", [])),
        self_declared_beginner_concepts=beginner,
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

    existing: dict = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            existing = {}

    if _is_v3_profile(existing):
        # Preserve v3 wrapper. Persist only non-ephemeral pending keys —
        # review_drill is owned by drill_pending.json.
        persistent_pending = {
            k: v for k, v in profile.pending_triggers.items()
            if k not in EPHEMERAL_PENDING_KEYS
        }
        existing.setdefault("concepts", {})
        existing["concepts"]["mastered"] = list(profile.mastered_concepts)
        existing["concepts"]["uncertain"] = list(profile.uncertain_concepts)
        existing["concepts"]["proficient"] = list(profile.proficient_concepts)
        existing["learner_id"] = profile.learner_id
        existing["last_updated"] = profile.last_updated
        existing["drill_due"] = list(profile.drill_due)
        existing["pending_triggers"] = persistent_pending
        existing.setdefault("activity", {})
        existing["activity"]["events_total"] = profile.total_events
        atomic_write_json(p, existing)
        return

    # Legacy flat schema — preserve existing behaviour.
    atomic_write_json(p, profile.__dict__)


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
    state.last_updated = time.time()
    atomic_write_json(p, state.__dict__)


def atomic_write_json(path: Path, payload, *, indent: int = 2) -> None:
    """Atomically write a JSON file via tmp→replace (crash-safe).

    A mid-write crash (power loss / OOM / Ctrl-C) leaves the previous good file
    intact instead of a truncated one. Use for single-source-of-truth state
    files; concurrent appenders use append_jsonl_locked instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)


def append_jsonl_locked(path: Path, row: dict) -> None:
    """Append one dict as a JSON line, fcntl LOCK_EX-serialized on POSIX.

    Falls back to a plain append on Windows / unsupported filesystems (single
    write <4KB lines are atomic enough in practice). Used for every jsonl log
    in state/learner/ that may see concurrent writers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    try:
        import fcntl
        with path.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def rewrite_jsonl_locked(path: Path, keep) -> int:
    """In-place filter a jsonl file under the SAME fcntl LOCK_EX that
    ``append_jsonl_locked`` takes, so a concurrent append cannot be lost to a
    read-modify-write race. ``keep(parsed_or_None, raw_line) -> bool`` decides
    each line; unparseable lines are passed as ``parsed=None`` (conservatively
    kept by most callers). Returns the count of rows removed. Falls back to an
    unlocked rewrite on Windows / unsupported filesystems."""
    if not path.exists():
        return 0

    def _filter(text: str):
        kept: list[str] = []
        removed = 0
        for ln in text.splitlines():
            if not ln.strip():
                continue
            try:
                parsed = json.loads(ln)
            except json.JSONDecodeError:
                parsed = None
            if keep(parsed, ln):
                kept.append(ln)
            else:
                removed += 1
        return kept, removed

    try:
        import fcntl
        with path.open("r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                kept, removed = _filter(f.read())
                if removed:
                    f.seek(0)
                    f.truncate()
                    f.write("".join(s + "\n" for s in kept))
                    f.flush()
                return removed
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        kept, removed = _filter(path.read_text(encoding="utf-8"))
        if removed:
            path.write_text("".join(s + "\n" for s in kept), encoding="utf-8")
        return removed


def append_history_event(event: dict, state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """Append one event line to history.jsonl. Caller supplies event_id + ts."""
    event = {**event, "ts": event.get("ts", time.time())}
    append_jsonl_locked(_history_path(state_root), event)


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
