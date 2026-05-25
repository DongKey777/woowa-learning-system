"""Phase T6: profile-recompute — derive learner profile v3 from history.jsonl
+ mastery_graph.sqlite.

Sources:
- state/learner/history.jsonl — all events (filtered by mode != development|test)
- state/learner/mastery_graph.sqlite — Bloom mastery state

Output: state/learner/profile.json (v3 schema)
  {
    schema_version: "v3",
    learner_id, computed_at,
    experience_level: junior|intermediate|advanced,
    concepts: { mastered:[], uncertain:[], underexplored:[] },
    activity: { events_total, days_active, last_event_ts },
    recent_code_changes_24h,
    calibration_status: { drill_avg_score, self_assess_count, delta },
    next_recommendations: [{ concept_id, reason }],
  }

experience_level rules:
- junior:        events_total < 100
- intermediate:  100 ≤ events < 1000 OR mastered ≥ 1
- advanced:      events ≥ 1000 OR mastered ≥ 5

Public API:
  recompute_profile(learner_id, state_root) -> dict (saved + returned)
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT, read_history

PROFILE_PATH = "profile.json"
SCHEMA_VERSION = "v3"
RECENT_WINDOW_SECS = 86400  # 24h


def _classify_experience(events_total: int, mastered_n: int) -> str:
    if events_total >= 1000 or mastered_n >= 5:
        return "advanced"
    if events_total >= 100 or mastered_n >= 1:
        return "intermediate"
    return "junior"


def _load_mastery(state_root: Path) -> dict:
    db = state_root / "learner" / "mastery_graph.sqlite"
    if not db.exists():
        return {"mastered": [], "proficient": [], "familiar": [], "attempted": []}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT concept_id, bloom_level FROM mastery"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"mastered": [], "proficient": [], "familiar": [], "attempted": []}
    finally:
        conn.close()
    out: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        out[r["bloom_level"]].append(r["concept_id"])
    for k in ("mastered", "proficient", "familiar", "attempted"):
        out.setdefault(k, [])
    return out


def _is_learning_event(ev: dict) -> bool:
    """Per CLAUDE.md §Session Mode: drop development/test mode events."""
    mode = ev.get("mode")
    return mode not in ("development", "test")


def recompute_profile(
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
    now: float | None = None,
) -> dict:
    """Scan history.jsonl + mastery_graph → write profile.json (v3)."""
    ts = now or time.time()
    events = read_history(state_root=state_root)
    learning = [e for e in events if _is_learning_event(e)]

    # Activity stats
    days = set()
    event_type_counts: Counter = Counter()
    last_ts = 0.0
    recent_code_n = 0
    concept_hit_counter: Counter = Counter()
    drill_scores: list[float] = []
    self_scores: list[float] = []
    for e in learning:
        et = e.get("event_type", "?")
        event_type_counts[et] += 1
        ets_raw = e.get("ts", 0.0)
        try:
            ets = float(ets_raw) if ets_raw is not None else 0.0
        except (TypeError, ValueError):
            ets = 0.0
        last_ts = max(last_ts, ets)
        if ets:
            import datetime as dt
            day = dt.datetime.fromtimestamp(ets, dt.timezone.utc).date().isoformat()
            days.add(day)
        payload = e.get("payload") or {}
        # Recent (24h) code attempts
        if et == "code_attempt" and ets >= ts - RECENT_WINDOW_SECS:
            recent_code_n += 1
        # Concept hit tally
        for cid in payload.get("concept_ids") or payload.get("top_concept_ids") or []:
            if cid:
                concept_hit_counter[cid] += 1
        if payload.get("concept_id"):
            concept_hit_counter[payload["concept_id"]] += 1
        # Drill / self_assess for calibration
        if et == "drill_answer":
            try:
                drill_scores.append(float(payload.get("score") or 0.0))
            except Exception:
                pass
        elif et == "self_assessment":
            try:
                self_scores.append(float(payload.get("score") or 0.0))
            except Exception:
                pass

    mastery = _load_mastery(state_root)
    mastered = sorted(mastery["mastered"])
    proficient = sorted(mastery["proficient"])
    # uncertain = concepts hit ≥3 but not in mastered/proficient/familiar
    progressed = set(mastered) | set(proficient) | set(mastery.get("familiar", []))
    uncertain = sorted([c for c, n in concept_hit_counter.most_common()
                         if n >= 3 and c not in progressed])[:30]
    # underexplored = hit 1-2 times, not progressed
    underexplored = sorted([c for c, n in concept_hit_counter.most_common()
                              if 1 <= n <= 2 and c not in progressed])[:30]

    experience = _classify_experience(len(learning), len(mastered))

    drill_avg = round(sum(drill_scores) / len(drill_scores), 3) if drill_scores else 0.0
    # self_assess often 0..10 → normalize
    self_norm = [s / 10.0 if s > 1.0 else s for s in self_scores]
    self_avg = round(sum(self_norm) / len(self_norm), 3) if self_norm else 0.0
    delta = round(self_avg - drill_avg, 3) if (drill_scores and self_norm) else None

    recommendations = _recommend_next(
        uncertain, underexplored, mastered, proficient, drill_avg,
    )[:5]

    profile = {
        "schema_version": SCHEMA_VERSION,
        "learner_id": learner_id,
        "computed_at": ts,
        "experience_level": experience,
        "concepts": {
            "mastered": mastered,
            "proficient": proficient,
            "uncertain": uncertain,
            "underexplored": underexplored,
        },
        "activity": {
            "events_total": len(learning),
            "days_active": len(days),
            "last_event_ts": last_ts,
            "event_type_counts": dict(event_type_counts),
        },
        "recent_code_changes_24h": recent_code_n,
        "calibration_status": {
            "drill_avg_score": drill_avg,
            "drill_count": len(drill_scores),
            "self_assess_avg_norm": self_avg,
            "self_assess_count": len(self_scores),
            "self_vs_drill_delta": delta,
        },
        "next_recommendations": recommendations,
    }
    out = state_root / "learner" / PROFILE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def _recommend_next(uncertain: list[str], underexplored: list[str],
                     mastered: list[str], proficient: list[str],
                     drill_avg: float) -> list[dict]:
    out = []
    # priority 1: uncertain concepts (≥3 hits, not progressed)
    for cid in uncertain[:3]:
        out.append({"concept_id": cid, "reason": "frequently encountered but not yet promoted"})
    # priority 2: proficient → push for mastered (needs drill ≥0.55)
    if drill_avg < 0.55 and proficient:
        for cid in proficient[:2]:
            out.append({"concept_id": cid, "reason": "proficient — drill score 향상 시 mastered 도달"})
    # priority 3: underexplored that may be next step
    for cid in underexplored[:2]:
        if not any(r["concept_id"] == cid for r in out):
            out.append({"concept_id": cid, "reason": "초기 노출 — 깊이 학습 권장"})
    return out
