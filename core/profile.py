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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.state import DEFAULT_STATE_ROOT, read_history

PROFILE_PATH = "profile.json"
UNIFIED_PROFILE_PATH = "unified_profile.json"
SCHEMA_VERSION = "v3"
UNIFIED_SCHEMA_VERSION = "unified_v1"
RECENT_WINDOW_SECS = 86400  # 24h
DEFAULT_DRILL_WINDOW = 5
_COMPUTED_PROFILE_KEYS = {
    "schema_version",
    "learner_id",
    "computed_at",
    "experience_level",
    "concepts",
    "activity",
    "recent_code_changes_24h",
    "calibration_status",
    "next_recommendations",
}
_DIMENSION_CEILINGS = {
    "accuracy": 10.0,
    "depth": 10.0,
    "practicality": 10.0,
    "completeness": 10.0,
}
_LEGACY_DIMENSION_CEILINGS = {
    "accuracy": 4.0,
    "depth": 3.0,
    "practicality": 2.0,
    "completeness": 1.0,
}


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


def _event_ts(ev: dict) -> float:
    raw = ev.get("ts", 0.0)
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _event_day(ts: float) -> str | None:
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _event_payload(ev: dict) -> dict[str, Any]:
    payload = ev.get("payload")
    if isinstance(payload, dict):
        return payload
    return {}


def _concept_ids_from_event(ev: dict) -> list[str]:
    payload = _event_payload(ev)
    raw = (
        payload.get("concept_ids")
        or payload.get("top_concept_ids")
        or ev.get("concept_ids")
        or ev.get("top_concept_ids")
        or []
    )
    out: list[str] = []
    if isinstance(raw, list):
        out.extend(str(cid) for cid in raw if cid)
    elif raw:
        out.append(str(raw))
    concept_id = payload.get("concept_id") or ev.get("concept_id")
    if concept_id:
        out.append(str(concept_id))
    seen: set[str] = set()
    deduped: list[str] = []
    for cid in out:
        if cid not in seen:
            seen.add(cid)
            deduped.append(cid)
    return deduped


def _drill_score_0_to_1(ev: dict) -> float | None:
    payload = _event_payload(ev)
    raw = payload.get("score")
    if raw is None:
        raw = ev.get("score")
    if raw is None:
        raw = ev.get("total_score")
    if raw is None:
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score / 10.0 if score > 1.0 else score))


def _drill_total_0_to_10(ev: dict) -> float | None:
    score = _drill_score_0_to_1(ev)
    if score is None:
        return None
    return round(score * 10.0, 3)


def _drill_dimensions(ev: dict) -> dict[str, float]:
    dims = _event_payload(ev).get("dimensions") or ev.get("dimensions") or {}
    if not isinstance(dims, dict):
        return {}
    out: dict[str, float] = {}
    for key in ("accuracy", "depth", "practicality", "completeness"):
        value = dims.get(key)
        if isinstance(value, (int, float)):
            # v2 stores 0..10. Keep legacy-compatible 0..10 dimensions.
            out[key] = round(max(0.0, min(10.0, float(value))), 3)
    return out


def _drill_weak_tags(ev: dict, dims: dict[str, float]) -> list[str]:
    raw = _event_payload(ev).get("weak_tags") or ev.get("weak_tags") or []
    tags = [str(tag) for tag in raw if tag] if isinstance(raw, list) else []
    if tags:
        return tags
    labels = {
        "accuracy": "정확도",
        "depth": "깊이",
        "practicality": "실전성",
        "completeness": "완결성",
    }
    return [
        labels[k] for k, value in dims.items()
        if value < _dimension_ceiling(k, dims) * 0.6
    ]


def _load_existing_profile(state_root: Path) -> dict:
    path = state_root / "learner" / PROFILE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge_preserved_profile_fields(existing: dict, computed: dict) -> dict:
    """Preserve v3 non-computed fields across recompute.

    Profile recompute is a projection refresh, not a destructive admin clear.
    Manual overrides, pending triggers, and future extension fields must
    survive round trips unless the recompute explicitly owns them.
    """
    if not (
        isinstance(existing, dict)
        and (str(existing.get("schema_version", "")).startswith("v3")
             or "concepts" in existing)
    ):
        return computed
    preserved = {
        key: value for key, value in existing.items()
        if key not in _COMPUTED_PROFILE_KEYS
    }
    return {**preserved, **computed}


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
        ets = _event_ts(e)
        last_ts = max(last_ts, ets)
        day = _event_day(ets)
        if day:
            days.add(day)
        # Recent (24h) code attempts
        if et == "code_attempt" and ets >= ts - RECENT_WINDOW_SECS:
            recent_code_n += 1
        # Concept hit tally
        for cid in _concept_ids_from_event(e):
            concept_hit_counter[cid] += 1
        # Drill / self_assess for calibration
        if et == "drill_answer":
            score = _drill_score_0_to_1(e)
            if score is not None:
                drill_scores.append(score)
        elif et == "self_assessment":
            payload = _event_payload(e)
            try:
                self_scores.append(float(payload.get("score", e.get("score", 0.0)) or 0.0))
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
    profile = _merge_preserved_profile_fields(_load_existing_profile(state_root), profile)
    out = state_root / "learner" / PROFILE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def compute_cs_view(
    history: list[dict] | None,
    *,
    window: int = DEFAULT_DRILL_WINDOW,
) -> dict[str, Any] | None:
    """Build the compact CS drill view from recent non-quarantined drills.

    Mirrors the legacy projection semantics but accepts both legacy top-level
    drill fields and v2 ``payload`` drill fields.
    """
    drills = [
        ev for ev in (history or [])
        if isinstance(ev, dict)
        and ev.get("event_type") == "drill_answer"
        and _is_learning_event(ev)
        and not ev.get("quarantined")
        and not (_event_payload(ev).get("quarantined"))
    ]
    if not drills:
        return None
    recent = drills[-window:]
    scored = [(ev, _drill_total_0_to_10(ev)) for ev in recent]
    scored = [(ev, total) for ev, total in scored if total is not None]
    if not scored:
        return None

    totals = [float(total) for _, total in scored]
    avg = round(sum(totals) / len(totals), 2)
    dim_sums: dict[str, float] = {}
    dim_counts: dict[str, int] = {}
    weak_tags: list[str] = []
    low_categories: list[str] = []
    recent_drills: list[dict[str, Any]] = []
    for ev, total in scored:
        dims = _drill_dimensions(ev)
        for key, value in dims.items():
            dim_sums[key] = dim_sums.get(key, 0.0) + value
            dim_counts[key] = dim_counts.get(key, 0) + 1
        for tag in _drill_weak_tags(ev, dims):
            if tag not in weak_tags:
                weak_tags.append(tag)
        source_doc = _event_payload(ev).get("source_doc") or ev.get("source_doc") or {}
        category = source_doc.get("category") if isinstance(source_doc, dict) else None
        if category and total < 6.0 and category not in low_categories:
            low_categories.append(str(category))
        recent_drills.append({
            "event_id": ev.get("event_id"),
            "scored_at": (
                _event_payload(ev).get("scored_at")
                or ev.get("scored_at")
                or ev.get("ts")
            ),
            "score": round(total / 10.0, 3),
            "total_score": round(total, 3),
            "level": _event_payload(ev).get("level") or ev.get("level"),
            "weak_tags": _drill_weak_tags(ev, dims),
            "concept_ids": _concept_ids_from_event(ev),
        })

    weak_dimensions: list[str] = []
    for dim in ("accuracy", "depth", "practicality", "completeness"):
        count = dim_counts.get(dim, 0)
        if count == 0:
            continue
        avg_dim = dim_sums[dim] / count
        if avg_dim < _dimension_ceiling(dim, _average_dims(dim_sums, dim_counts)) * 0.6:
            weak_dimensions.append(dim)

    return {
        "avg_score": avg,
        "level": _level_for(avg),
        "weak_dimensions": weak_dimensions,
        "weak_tags": weak_tags,
        "low_categories": low_categories,
        "recent_drills": recent_drills,
    }


def build_unified_projection(
    profile: dict[str, Any] | None,
    history: list[dict] | None = None,
    *,
    computed_at: float | None = None,
) -> dict[str, Any]:
    """Build a deterministic derived learner projection from profile truth."""
    profile = profile or {}
    concepts = profile.get("concepts") or {}
    mastered = list(concepts.get("mastered") or [])
    proficient = list(concepts.get("proficient") or [])
    uncertain = list(concepts.get("uncertain") or [])
    underexplored = list(concepts.get("underexplored") or [])
    recommendations = list(profile.get("next_recommendations") or [])
    cs_view = compute_cs_view(history)
    weak_drill_concepts: list[str] = []
    for row in (cs_view or {}).get("recent_drills") or []:
        if row.get("score", 1.0) < 0.65:
            for cid in row.get("concept_ids") or []:
                if cid and cid not in weak_drill_concepts:
                    weak_drill_concepts.append(cid)
    priority_focus: list[str] = []
    for cid in weak_drill_concepts + uncertain:
        if cid not in priority_focus:
            priority_focus.append(cid)
        if len(priority_focus) >= 5:
            break
    theoretical_gaps = list((cs_view or {}).get("weak_tags") or [])
    theoretical_gaps.extend((cs_view or {}).get("low_categories") or [])

    return {
        "schema_version": UNIFIED_SCHEMA_VERSION,
        "source_schema_version": profile.get("schema_version"),
        "learner_id": profile.get("learner_id", "default"),
        "computed_at": computed_at if computed_at is not None else time.time(),
        "coach_view": {
            "experience_level": profile.get("experience_level", "junior"),
            "confidence": _confidence_for_profile(profile),
            "mastered_concepts": mastered[:30],
            "proficient_concepts": proficient[:30],
            "uncertain_concepts": uncertain[:30],
            "underexplored_concepts": underexplored[:30],
            "next_recommendations": recommendations[:5],
            "must_skip_explanations_of": (mastered + proficient)[:50],
        },
        "cs_view": cs_view,
        "reconciled": {
            "priority_focus": priority_focus,
            "empirical_only_gaps": underexplored[:5],
            "theoretical_only_gaps": _dedupe(theoretical_gaps)[:5],
        },
    }


def rebuild_unified_projection(
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
    profile: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Write ``state/learner/unified_profile.json`` from profile + history."""
    loaded_profile = profile if profile is not None else _load_existing_profile(state_root)
    if not loaded_profile:
        loaded_profile = recompute_profile(learner_id, state_root=state_root, now=now)
    history = [e for e in read_history(state_root=state_root) if _is_learning_event(e)]
    projection = build_unified_projection(
        loaded_profile,
        history,
        computed_at=now if now is not None else time.time(),
    )
    out = state_root / "learner" / UNIFIED_PROFILE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(projection, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    return projection


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


def _level_for(avg: float) -> str:
    if avg >= 9:
        return "L5"
    if avg >= 7:
        return "L4"
    if avg >= 5:
        return "L3"
    if avg >= 3:
        return "L2"
    return "L1"


def _confidence_for_profile(profile: dict[str, Any]) -> str:
    activity = profile.get("activity") or {}
    concepts = profile.get("concepts") or {}
    events_total = int(activity.get("events_total", 0) or 0)
    progressed = len(concepts.get("mastered") or []) + len(concepts.get("proficient") or [])
    if events_total >= 1000 or progressed >= 5:
        return "high"
    if events_total >= 100 or progressed >= 1:
        return "medium"
    return "low"


def _dedupe(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _dimension_ceiling(dim: str, dims: dict[str, float]) -> float:
    legacy = _LEGACY_DIMENSION_CEILINGS[dim]
    if all(value <= _LEGACY_DIMENSION_CEILINGS.get(key, 10.0)
           for key, value in dims.items()):
        return legacy
    return _DIMENSION_CEILINGS[dim]


def _average_dims(
    dim_sums: dict[str, float],
    dim_counts: dict[str, int],
) -> dict[str, float]:
    return {
        key: dim_sums[key] / dim_counts[key]
        for key in dim_sums
        if dim_counts.get(key)
    }
