"""mission.meta_analytics — F2 family E (meta-learning analytics).

Cross-repo, learner-scoped self-reflection over the whole ask history. Answers
"what are my learning PATTERNS" rather than "how is this one mission going":

  E1 repeated_concepts — CS concepts I keep coming back to (asked ≥2× across all
                         missions). Re-asking is the signal of a sticky concept.
  E2 mode_mix          — what KINDS of questions I ask (router_mode share).
  E3 quality_trend     — answer-quality flag trends (quality + contract flags).
  E4 mastery_distribution — a Bloom mastery snapshot (mirror learning_path).

Pure read-only over state/learner/{history.jsonl,response-quality.jsonl} +
core.mastery. Every source may be absent → degrade gracefully (status reflects
what's available; never crash). Artifact lands at
state/learner/meta_analytics.json (mirrors the learner-scoped builders).
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

_TOP_REPEATED = 15
_NO_REPO = "(no repo)"


def _iter_jsonl(path: Path):
    """Yield parsed rows from a jsonl file, skipping blank/garbled lines."""
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _repeated_concepts(rag_events: list[dict]) -> list[dict]:
    """concept_id → {times_asked, repos} for concepts asked ≥2× (re-asked)."""
    times: Counter[str] = Counter()
    repos: dict[str, set[str]] = defaultdict(set)
    for ev in rag_events:
        payload = ev.get("payload") or {}
        repo = payload.get("repo") or ev.get("repo") or None
        repo_label = repo if repo else _NO_REPO
        for cid in payload.get("top_concept_ids") or []:
            if not cid:
                continue
            times[cid] += 1
            repos[cid].add(repo_label)
    out = [
        {
            "concept_id": cid,
            "times_asked": n,
            "repos": sorted(repos[cid]),
        }
        for cid, n in times.items()
        if n >= 2
    ]
    out.sort(key=lambda c: (-c["times_asked"], c["concept_id"]))
    return out[:_TOP_REPEATED]


def _mode_mix(rag_events: list[dict]) -> list[dict]:
    """router_mode share across all rag_ask events, descending n."""
    modes: Counter[str] = Counter()
    for ev in rag_events:
        payload = ev.get("payload") or {}
        mode = payload.get("router_mode") or "(unknown)"
        modes[mode] += 1
    total = sum(modes.values())
    rows = [
        {
            "mode": mode,
            "n": n,
            "pct": round(100.0 * n / total, 1) if total else 0.0,
        }
        for mode, n in modes.items()
    ]
    rows.sort(key=lambda r: (-r["n"], r["mode"]))
    return rows


def _quality_trend(state_root: Path) -> dict:
    """Aggregate quality_flags + contract_flags across response-quality.jsonl."""
    path = state_root / "learner" / "response-quality.jsonl"
    if not path.exists():
        return {"total_quality_events": 0, "flag_counts": []}
    total = 0
    flags: Counter[str] = Counter()
    for row in _iter_jsonl(path):
        total += 1
        for f in (row.get("quality_flags") or []):
            if f:
                flags[f] += 1
        for f in (row.get("contract_flags") or []):
            if f:
                flags[f] += 1
    flag_counts = [{"flag": f, "n": n} for f, n in flags.items()]
    flag_counts.sort(key=lambda r: (-r["n"], r["flag"]))
    return {"total_quality_events": total, "flag_counts": flag_counts}


def _mastery_distribution(state_root: Path) -> dict:
    """Bloom snapshot: per-level counts + total. Mirrors learning_path /
    memory_review. Mastery unavailable → all zeros."""
    try:
        from core import mastery
        summary = mastery.summary(state_root)
        counts = {lvl: int(summary["counts"].get(lvl, 0))
                  for lvl in mastery.BLOOM_LEVELS}
        return {**counts, "total": int(summary["total_tracked"])}
    except Exception:
        return {"attempted": 0, "familiar": 0, "proficient": 0,
                "mastered": 0, "total": 0}


def analyze(learner_login: str = "DongKey777",
            state_root: Path = DEFAULT_STATE_ROOT,
            now: float | None = None) -> dict:
    now = now or time.time()
    generated_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    history = state_root / "learner" / "history.jsonl"
    rag_events = [
        ev for ev in _iter_jsonl(history)
        if ev.get("event_type") == "rag_ask"
    ]

    mastery_distribution = _mastery_distribution(state_root)

    if not rag_events:
        return {
            "learner_login": learner_login,
            "status": "missing_history",
            "repeated_concepts": [],
            "mode_mix": [],
            "quality_trend": _quality_trend(state_root),
            "mastery_distribution": mastery_distribution,
            "counts": {
                "rag_ask_events": 0,
                "distinct_concepts_asked": 0,
                "repeated_concepts": 0,
            },
            "generated_at": generated_at,
        }

    repeated = _repeated_concepts(rag_events)
    distinct = {
        cid
        for ev in rag_events
        for cid in ((ev.get("payload") or {}).get("top_concept_ids") or [])
        if cid
    }

    return {
        "learner_login": learner_login,
        "status": "ok",
        "repeated_concepts": repeated,
        "mode_mix": _mode_mix(rag_events),
        "quality_trend": _quality_trend(state_root),
        "mastery_distribution": mastery_distribution,
        "counts": {
            "rag_ask_events": len(rag_events),
            "distinct_concepts_asked": len(distinct),
            "repeated_concepts": len(repeated),
        },
        "generated_at": generated_at,
    }


def save_meta_analytics(report: dict,
                        state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out_dir = state_root / "learner"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "meta_analytics.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
