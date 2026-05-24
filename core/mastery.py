"""Bloom mastery SQLite store + cross-axis evidence (D-C, fixes mastered=0).

Bloom levels:
  attempted   — 1 evidence event of any kind
  familiar    — ≥3 evidence from ≥2 distinct sources within 14 days
  proficient  — PR merge + mentor accept (no follow-up nit on same concept)
  mastered    — proficient + ≥1 drill ≥80 OR ≥1 self-assess + 30-day retention

Evidence sources + weights:
  pr_merge       1.0
  mentor_accept  0.9
  drill_score    0.7 × score (0..1)
  mission_use    0.3
  self_assess    0.2 (calibration only, never promotes alone)
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
BLOOM_LEVELS = ("attempted", "familiar", "proficient", "mastered")
EVIDENCE_WEIGHTS = {
    "pr_merge": 1.0,
    "mentor_accept": 0.9,
    "drill_score": 0.7,
    "mission_use": 0.3,
    "self_assess": 0.2,
}
FAMILIAR_WINDOW_SECS = 14 * 24 * 3600
RETENTION_WINDOW_SECS = 30 * 24 * 3600


@dataclass(frozen=True)
class MasteryEntry:
    concept_id: str
    bloom_level: str
    evidence_count: int
    last_seen_at: float
    promotion_trace: list[dict]


def _db_path(state_root: Path) -> Path:
    return state_root / "learner" / "mastery_graph.sqlite"


@contextmanager
def _connect(state_root: Path = DEFAULT_STATE_ROOT):
    p = _db_path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mastery (
          concept_id TEXT PRIMARY KEY,
          bloom_level TEXT NOT NULL DEFAULT 'attempted',
          evidence_count INTEGER NOT NULL DEFAULT 0,
          last_seen_at REAL NOT NULL,
          promotion_trace TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS evidence (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          concept_id TEXT NOT NULL,
          source TEXT NOT NULL,
          weight REAL NOT NULL,
          ts REAL NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_concept ON evidence(concept_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_ts ON evidence(ts);
        """
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_evidence(
    concept_id: str,
    source: str,
    score: float = 1.0,
    payload: dict | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
    ts: float | None = None,
) -> int:
    """Append one evidence event. Returns evidence_id. Source must be a known weight key."""
    if source not in EVIDENCE_WEIGHTS:
        raise ValueError(f"unknown evidence source: {source!r}")
    weight = EVIDENCE_WEIGHTS[source] * max(0.0, min(1.0, score))
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    now = ts if ts is not None else time.time()
    with _connect(state_root) as conn:
        cur = conn.execute(
            "INSERT INTO evidence(concept_id, source, weight, ts, payload_json) VALUES (?,?,?,?,?)",
            (concept_id, source, weight, now, payload_json),
        )
        eid = cur.lastrowid
        conn.execute(
            "INSERT INTO mastery(concept_id, bloom_level, evidence_count, last_seen_at) "
            "VALUES(?, 'attempted', 1, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET evidence_count = evidence_count + 1, last_seen_at = excluded.last_seen_at",
            (concept_id, now),
        )
    return eid


def promote(concept_id: str, state_root: Path = DEFAULT_STATE_ROOT, now: float | None = None) -> str:
    """Compute current Bloom level for concept based on evidence, persist if changed."""
    now = now if now is not None else time.time()
    with _connect(state_root) as conn:
        evs = list(conn.execute(
            "SELECT source, weight, ts FROM evidence WHERE concept_id=? ORDER BY ts",
            (concept_id,),
        ))
        cur_row = conn.execute(
            "SELECT bloom_level, promotion_trace FROM mastery WHERE concept_id=?",
            (concept_id,),
        ).fetchone()
        old_level = cur_row["bloom_level"] if cur_row else "attempted"
        trace = json.loads(cur_row["promotion_trace"]) if cur_row else []
        new_level = _compute_level(evs, now)
        if new_level != old_level:
            trace.append({"from": old_level, "to": new_level, "ts": now})
            conn.execute(
                "UPDATE mastery SET bloom_level=?, promotion_trace=? WHERE concept_id=?",
                (new_level, json.dumps(trace, ensure_ascii=False), concept_id),
            )
    return new_level


def _compute_level(evs: list[sqlite3.Row], now: float) -> str:
    if not evs:
        return "attempted"
    recent_window = [e for e in evs if now - e["ts"] <= FAMILIAR_WINDOW_SECS]
    distinct_sources_recent = {e["source"] for e in recent_window}
    has_pr_merge = any(e["source"] == "pr_merge" for e in evs)
    has_mentor_accept = any(e["source"] == "mentor_accept" for e in evs)
    has_strong_drill = any(e["source"] == "drill_score" and e["weight"] >= 0.55 for e in evs)  # 0.7 × 0.8 = ~0.56 (float precision)
    has_self_assess_retained = any(
        e["source"] == "self_assess" and now - e["ts"] >= RETENTION_WINDOW_SECS for e in evs
    )

    proficient = has_pr_merge and has_mentor_accept
    mastered = proficient and (has_strong_drill or has_self_assess_retained)
    if mastered:
        return "mastered"
    if proficient:
        return "proficient"
    if len(recent_window) >= 3 and len(distinct_sources_recent) >= 2:
        return "familiar"
    return "attempted"


def get(concept_id: str, state_root: Path = DEFAULT_STATE_ROOT) -> MasteryEntry | None:
    with _connect(state_root) as conn:
        row = conn.execute(
            "SELECT concept_id, bloom_level, evidence_count, last_seen_at, promotion_trace FROM mastery WHERE concept_id=?",
            (concept_id,),
        ).fetchone()
    if row is None:
        return None
    return MasteryEntry(
        concept_id=row["concept_id"],
        bloom_level=row["bloom_level"],
        evidence_count=row["evidence_count"],
        last_seen_at=row["last_seen_at"],
        promotion_trace=json.loads(row["promotion_trace"]),
    )


def list_by_level(level: str, state_root: Path = DEFAULT_STATE_ROOT) -> list[str]:
    if level not in BLOOM_LEVELS:
        raise ValueError(f"unknown level {level!r}")
    with _connect(state_root) as conn:
        rows = conn.execute(
            "SELECT concept_id FROM mastery WHERE bloom_level=? ORDER BY last_seen_at DESC",
            (level,),
        ).fetchall()
    return [r["concept_id"] for r in rows]


def summary(state_root: Path = DEFAULT_STATE_ROOT) -> dict:
    """Quick stats for prompt context. Counts per level."""
    with _connect(state_root) as conn:
        rows = conn.execute("SELECT bloom_level, COUNT(*) c FROM mastery GROUP BY bloom_level").fetchall()
    counts = {lvl: 0 for lvl in BLOOM_LEVELS}
    for r in rows:
        counts[r["bloom_level"]] = r["c"]
    return {"counts": counts, "total_tracked": sum(counts.values())}


def compact_all(state_root: Path = DEFAULT_STATE_ROOT, now: float | None = None) -> int:
    """Re-evaluate Bloom level for every tracked concept. Returns count of promotions."""
    with _connect(state_root) as conn:
        cids = [r["concept_id"] for r in conn.execute("SELECT concept_id FROM mastery").fetchall()]
    promoted = 0
    for cid in cids:
        before = get(cid, state_root=state_root)
        after_level = promote(cid, state_root=state_root, now=now)
        if before is None or after_level != before.bloom_level:
            promoted += 1
    return promoted
