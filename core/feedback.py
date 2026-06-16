"""Per-turn learner event evidence → Bloom mastery promotion."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.mastery import DEFAULT_STATE_ROOT, promote, record_evidence
from core.state import read_history

EVENT_TO_SOURCE: dict[str, str] = {
    "code_attempt": "mission_use",
    "rag_ask": "mission_use",
    "coach_run": "mission_use",
    "drill_answer": "drill_score",
    "self_assessment": "self_assess",
}


def record_turn(event: dict, state_root: Path = DEFAULT_STATE_ROOT) -> list[tuple[str, str]]:
    """Convert one event → evidence + auto-promote affected concepts.

    Returns list of (concept_id, new_bloom_level) for the concepts touched.
    """
    et = event.get("event_type")
    if et not in EVENT_TO_SOURCE:
        return []
    payload = dict(event.get("payload") or {})
    # Some emitters (code_event.py) carry repo at the event top level, not in
    # payload — copy it so evidence stays attributable per-repo (L use-case).
    if event.get("repo") and not payload.get("repo"):
        payload["repo"] = event["repo"]
    source = EVENT_TO_SOURCE[et]
    ts = event.get("ts")

    concepts = _extract_concepts(payload)
    score = _extract_score(payload, source)

    promoted: list[tuple[str, str]] = []
    for cid in concepts:
        if not cid:
            continue
        record_evidence(cid, source=source, score=score, payload=payload, state_root=state_root, ts=ts)
        new_level = promote(cid, state_root=state_root, now=ts)
        promoted.append((cid, new_level))
    return promoted


def ingest_pr_merge(
    concept_ids: Iterable[str],
    state_root: Path = DEFAULT_STATE_ROOT,
    ts: float | None = None,
    payload: dict | None = None,
    dedup_prefix: str | None = None,
) -> list[tuple[str, str]]:
    """Trigger pr_merge evidence (highest weight). Called after PR merged + mentor approved.

    dedup_prefix builds a per-concept dedup_key (prefix:cid) so batch syncs are
    idempotent; payload (e.g. {"repo": r, "pr_number": n}) is attached for audit.
    """
    out: list[tuple[str, str]] = []
    for cid in concept_ids:
        if not cid:
            continue
        key = f"{dedup_prefix}:{cid}" if dedup_prefix else None
        record_evidence(cid, source="pr_merge", state_root=state_root, ts=ts,
                        payload=payload, dedup_key=key)
        out.append((cid, promote(cid, state_root=state_root, now=ts)))
    return out


def ingest_mentor_accept(
    concept_ids: Iterable[str],
    state_root: Path = DEFAULT_STATE_ROOT,
    ts: float | None = None,
    payload: dict | None = None,
    dedup_prefix: str | None = None,
) -> list[tuple[str, str]]:
    """Trigger mentor_accept evidence (resolved review thread / APPROVED review)."""
    out: list[tuple[str, str]] = []
    for cid in concept_ids:
        if not cid:
            continue
        key = f"{dedup_prefix}:{cid}" if dedup_prefix else None
        record_evidence(cid, source="mentor_accept", state_root=state_root, ts=ts,
                        payload=payload, dedup_key=key)
        out.append((cid, promote(cid, state_root=state_root, now=ts)))
    return out


def replay_history(state_root: Path = DEFAULT_STATE_ROOT, mode_filter: str = "learning") -> int:
    """Backfill mastery_graph from existing history.jsonl events.

    Returns count of evidence records inserted. Fixes the broken mastered=0
    by replaying all past learning events through the new Bloom evidence model.
    """
    events = read_history(state_root)
    count = 0
    for ev in events:
        mode = ev.get("mode")
        if mode_filter and mode not in (mode_filter, None):
            continue
        if record_turn(ev, state_root=state_root):
            count += 1
    return count


def _extract_concepts(payload: dict) -> list[str]:
    """Pull concept_ids from various event payload shapes."""
    candidates: list = []
    for key in ("cited_concepts", "citations", "top_concept_ids",
                "citation_paths", "used_concepts", "concept_ids"):
        candidates.extend(payload.get(key) or [])
    if payload.get("concept_id"):
        candidates.append(payload["concept_id"])
    # normalize: strip concept: prefix, drop empties
    out: list[str] = []
    for c in candidates:
        if not c:
            continue
        c = str(c).strip()
        if c.startswith("concept:"):
            c = c[len("concept:") :]
        if c:
            out.append(c)
    return list(dict.fromkeys(out))  # dedupe, preserve order


def _extract_score(payload: dict, source: str) -> float:
    """Normalize score to 0..1 for record_evidence weight scaling.

    Both drill_score and self_assess carry a learner-reported score that must
    scale evidence weight: a "2/10" self-assessment is the learner saying they
    are NOT confident and must not earn the same mastery credit as "9/10". W4b:
    previously only drill_score honored the score; self_assess silently returned
    1.0, so every self-assessment promoted as full confidence regardless of the
    reported number. The live emitter stores self_assess score already in 0..1;
    the >1.0 legacy /10 path keeps history replay of 10-point events correct.
    """
    if source not in ("drill_score", "self_assess"):
        return 1.0
    raw = payload.get("score")
    if raw is None:
        return 1.0
    try:
        s = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if s > 1.0:  # legacy 10-point scale
        s = s / 10.0
    return max(0.0, min(1.0, s))
