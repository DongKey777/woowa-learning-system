"""mission.memory_review — F2 family M (memory / spaced-review).

Cross-repo, learner-scoped. Answers "what should I review / what have I never
actually studied":

  M3 blind_spots  — a concept I *use* in mission code (mission_patterns) but
                    have *never asked about* (absent from every history turn's
                    top_concept_ids) and have not mastered. Unstudied-first.
  M2 forgetting   — a concept I reached familiar+ on but haven't touched in
                    longer than the retention window (mastery.last_seen_at).
  M1 review_cards — real mentor feedback on my own code (review_anchors with
                    concept links from F0-d). Past questions worth re-reviewing.

Pure read-only over state/learner/{mastery_graph.sqlite,history.jsonl,
review_anchors.json} + state/repos/<repo>/mission_patterns.json. Artifact lands
at state/learner/memory_review.json (mirrors _load_anchors learner scope).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
RETENTION_WINDOW_SECS = 30 * 24 * 3600
_BLOOM_RANK = {"attempted": 0, "familiar": 1, "proficient": 2, "mastered": 3}
_TOP_BLIND = 12
_TOP_FORGET = 10
_TOP_CARDS = 8
_CARD_CHARS = 200


def _mastery_db(state_root: Path) -> Path:
    return state_root / "learner" / "mastery_graph.sqlite"


def _asked_concepts(state_root: Path) -> set[str]:
    """Every concept that ever surfaced as a RAG top-hit in the learner's ask
    history. A concept absent here was never explored, even as a neighbor."""
    p = state_root / "learner" / "history.jsonl"
    asked: set[str] = set()
    if not p.exists():
        return asked
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for cid in (row.get("payload") or {}).get("top_concept_ids") or []:
                asked.add(cid)
    return asked


def _used_concepts(state_root: Path) -> dict[str, list[str]]:
    """concept_id -> repos that apply it in mission code (mission_patterns)."""
    used: dict[str, set[str]] = {}
    repos_dir = state_root / "repos"
    if not repos_dir.is_dir():
        return {}
    for d in sorted(repos_dir.iterdir()):
        p = d / "mission_patterns.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for pat in data.get("patterns", []):
            cid = pat.get("matched_concept_id") or pat.get("concept_id")
            if cid:
                used.setdefault(cid, set()).add(d.name)
    return {cid: sorted(repos) for cid, repos in used.items()}


def _levels(conn: sqlite3.Connection) -> dict[str, tuple[str, float]]:
    return {
        r[0]: (r[1], float(r[2]))
        for r in conn.execute(
            "SELECT concept_id, bloom_level, last_seen_at FROM mastery"
        )
    }


def _review_cards(state_root: Path) -> list[dict]:
    """Concept-linked review anchors = real mentor questions on the learner's
    own code. Only anchors with topic_concept_ids (F0-d) become cards — an
    unlinked anchor has no concept to review against."""
    p = state_root / "learner" / "review_anchors.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    cards: list[dict] = []
    for a in data.get("anchors", []):
        cids = a.get("topic_concept_ids") or []
        if not cids:
            continue
        text = (a.get("comment_text") or "").strip().replace("\n", " ")
        if len(text) > _CARD_CHARS:
            text = text[:_CARD_CHARS].rstrip() + "…"
        cards.append({
            "repo": a.get("repo"),
            "pr_number": a.get("pr_number"),
            "path": a.get("path"),
            "concept_ids": cids[:3],
            "mentor_prompt": text,
        })
    return cards[:_TOP_CARDS]


def analyze(learner_login: str, state_root: Path = DEFAULT_STATE_ROOT,
            now: float | None = None) -> dict:
    now = now or time.time()
    db = _mastery_db(state_root)
    if not db.exists():
        return {"learner_login": learner_login, "status": "missing_mastery",
                "generated_at": now, "blind_spots": [],
                "forgetting": [], "review_cards": []}

    conn = sqlite3.connect(str(db))
    try:
        levels = _levels(conn)
    finally:
        conn.close()

    asked = _asked_concepts(state_root)
    used = _used_concepts(state_root)

    # M3 blind spots: used in code, never asked, not at the top "mastered"
    # level. Unstudied (no mastery row) first, then by ascending bloom level.
    blind: list[dict] = []
    for cid, repos in used.items():
        if cid in asked:
            continue
        level = levels.get(cid, (None, 0.0))[0]
        if level == "mastered":
            continue
        blind.append({"concept_id": cid, "repos": repos, "bloom_level": level})
    blind.sort(key=lambda b: (_BLOOM_RANK.get(b["bloom_level"], -1),
                              b["concept_id"]))
    blind = blind[:_TOP_BLIND]

    # M2 forgetting: reached familiar+ but untouched past the retention window.
    forgetting: list[dict] = []
    for cid, (level, seen) in levels.items():
        if _BLOOM_RANK.get(level, -1) >= 1 and (now - seen) > RETENTION_WINDOW_SECS:
            forgetting.append({
                "concept_id": cid, "bloom_level": level,
                "days_since_seen": round((now - seen) / 86400, 1),
            })
    forgetting.sort(key=lambda f: -f["days_since_seen"])
    forgetting = forgetting[:_TOP_FORGET]

    return {
        "learner_login": learner_login,
        "status": "ok",
        "generated_at": now,
        "blind_spots": blind,
        "forgetting": forgetting,
        "review_cards": _review_cards(state_root),
    }


def save_memory_review(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out_dir = state_root / "learner"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "memory_review.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
