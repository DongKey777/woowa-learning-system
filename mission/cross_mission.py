"""mission.cross_mission — F2 family L (cross-mission learning analytics).

Cross-repo, learner-scoped. Answers "how does my learning carry between
missions": which proven concepts I reuse in a later mission, which mentor
signals recur across missions, and how the missions compare in difficulty.

  L1 carryover  — a concept I *proved* (pr_merge/mentor_accept evidence) in an
                  earlier mission that reappears in a later mission's
                  mission_patterns (I'm applying a mastered concept again).
  L2 recurring  — mentor-signal topics that show up across ≥2 missions
                  (reuses core.pr_retro.build_retrospective per repo).
  L3 difficulty — avg_review_rounds + merged ratio per mission (pr_retro).

Pure read-only over state/learner/mastery_graph.sqlite +
state/repos/<repo>/mission_patterns.json + archive. Artifact lands at
state/learner/cross_mission.json (mirrors _load_anchors learner scope).
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
_PROVEN_SOURCES = ("pr_merge", "mentor_accept")
_TOP_CARRYOVER = 12
_TOP_RECURRING = 8


def _mastery_db(state_root: Path) -> Path:
    return state_root / "learner" / "mastery_graph.sqlite"


def _evidence_repos(conn: sqlite3.Connection) -> dict[str, float]:
    """repo -> earliest evidence ts (for temporal ordering). pr_merge carries
    the true merge date, so a completed mission sorts before a later one even
    if its mission_use rows were written at build time."""
    out: dict[str, float] = {}
    for row in conn.execute(
        "SELECT json_extract(payload_json,'$.repo') repo, MIN(ts) mn "
        "FROM evidence WHERE json_extract(payload_json,'$.repo') IS NOT NULL "
        "GROUP BY repo"
    ):
        out[row[0]] = float(row[1])
    return out


def _proven_concepts(conn: sqlite3.Connection, repo: str) -> set[str]:
    q = ("SELECT DISTINCT concept_id FROM evidence WHERE source IN (?,?) "
         "AND json_extract(payload_json,'$.repo')=?")
    return {r[0] for r in conn.execute(q, (*_PROVEN_SOURCES, repo))}


def _evidence_concepts(conn: sqlite3.Connection, repo: str) -> set[str]:
    q = ("SELECT DISTINCT concept_id FROM evidence "
         "WHERE json_extract(payload_json,'$.repo')=?")
    return {r[0] for r in conn.execute(q, (repo,))}


def _pattern_concepts(repo: str, state_root: Path) -> set[str]:
    p = state_root / "repos" / repo / "mission_patterns.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    out: set[str] = set()
    for pat in data.get("patterns", []):
        cid = pat.get("matched_concept_id") or pat.get("concept_id")
        if cid:
            out.add(cid)
    return out


def _discover_repos(conn: sqlite3.Connection, state_root: Path) -> list[str]:
    """Real missions only: a repo with evidence OR an archive db. Excludes
    fixture dirs (no archive, no evidence)."""
    repos: set[str] = set(_evidence_repos(conn))
    repos_dir = state_root / "repos"
    if repos_dir.is_dir():
        for d in repos_dir.iterdir():
            if (d / "archive" / "prs.sqlite3").exists():
                repos.add(d.name)
    return sorted(repos)


def analyze(learner_login: str, state_root: Path = DEFAULT_STATE_ROOT,
            now: float | None = None) -> dict:
    now = now or time.time()
    db = _mastery_db(state_root)
    if not db.exists():
        return {"learner_login": learner_login, "status": "missing_mastery",
                "generated_at": now, "repos_analyzed": [],
                "carryover": [], "recurring_across_missions": [], "difficulty": []}

    conn = sqlite3.connect(str(db))
    try:
        first_ts = _evidence_repos(conn)
        repos = _discover_repos(conn, state_root)
        per_repo: dict[str, dict] = {}
        for repo in repos:
            per_repo[repo] = {
                "proven": _proven_concepts(conn, repo),
                "used": _pattern_concepts(repo, state_root)
                        | _evidence_concepts(conn, repo),
                "first_ts": first_ts.get(repo),
            }
    finally:
        conn.close()

    # Per-repo retrospective (avg_review_rounds + recurring signals). Pure SQLite.
    from core.pr_retro import build_retrospective
    retros: dict[str, object] = {}
    for repo in repos:
        try:
            retros[repo] = build_retrospective(repo, learner_login, state_root, now=now)
        except Exception:
            retros[repo] = None

    # Real missions only: the learner left a trace (evidence, extracted patterns,
    # or an authored PR). Drops empty fixture/scaffold repos (archive present but
    # 0 patterns + 0 learner PRs) so they never reach the learner-facing answer.
    def _is_real(repo: str) -> bool:
        if per_repo[repo]["first_ts"] is not None or per_repo[repo]["used"]:
            return True
        r = retros.get(repo)
        return r is not None and getattr(r, "prs_total", 0) > 0

    real = [r for r in repos if _is_real(r)]

    # Temporal order: earliest evidence ts, else earliest PR created_at, else name.
    def _order_key(repo: str):
        ts = per_repo[repo]["first_ts"]
        if ts is not None:
            return (0, ts, repo)
        r = retros.get(repo)
        created = None
        if r is not None and getattr(r, "timeline", None):
            created = min((t.created_at for t in r.timeline if t.created_at),
                          default=None)
        return (1, created or "9999", repo)

    ordered = sorted(real, key=_order_key)

    # L1 carryover: concept proven in an earlier mission, reused in a later one.
    carryover: list[dict] = []
    for i, earlier in enumerate(ordered):
        proven = per_repo[earlier]["proven"]
        if not proven:
            continue
        for later in ordered[i + 1:]:
            shared = proven & per_repo[later]["used"]
            for cid in sorted(shared):
                carryover.append({
                    "concept_id": cid,
                    "proven_in": earlier,
                    "reused_in": later,
                })

    # L2 recurring across missions: same mentor topic in ≥2 missions.
    topic_repos: dict[str, set[str]] = defaultdict(set)
    topic_comments: dict[str, int] = defaultdict(int)
    for repo in ordered:
        r = retros.get(repo)
        if r is None:
            continue
        for sig in getattr(r, "recurring_mentor_signals", []) or []:
            topic_repos[sig.topic].add(repo)
            topic_comments[sig.topic] += sig.comment_count
    recurring = [
        {"topic": t, "repos": sorted(topic_repos[t]),
         "total_comments": topic_comments[t]}
        for t in sorted(topic_repos, key=lambda k: -topic_comments[k])
        if len(topic_repos[t]) >= 2
    ][:_TOP_RECURRING]

    # L3 difficulty: avg_review_rounds + merged ratio, hardest first.
    difficulty = []
    for repo in ordered:
        r = retros.get(repo)
        if r is None or getattr(r, "prs_total", 0) == 0:
            continue
        difficulty.append({
            "repo": repo,
            "avg_review_rounds": r.avg_review_rounds,
            "prs_total": r.prs_total,
            "prs_merged": r.prs_merged,
        })
    difficulty.sort(key=lambda d: -d["avg_review_rounds"])

    return {
        "learner_login": learner_login,
        "status": "ok",
        "generated_at": now,
        "repos_analyzed": ordered,
        "carryover": carryover[:_TOP_CARRYOVER],
        "recurring_across_missions": recurring,
        "difficulty": difficulty,
    }


def save_cross_mission(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out_dir = state_root / "learner"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "cross_mission.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
