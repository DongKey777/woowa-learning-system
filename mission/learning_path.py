"""mission.learning_path — F2 family D (learning-path planning).

Cross-repo, learner-scoped. Answers "what should I learn next, and in what
order": given my current mastery state plus the concept prerequisite/next/
confusable graph, surface the concepts that my proven knowledge unlocks and
flag which ones still have an unmet prerequisite.

  D-next   next_concepts — for each concept I already KNOW (proficient+),
            the next_docs targets it points to plus the prerequisite-children
            it unlocks (concepts that list a known concept as a prerequisite).
  D-gate   unmet_prereqs — for surfaced candidates that are NOT ready, the
            specific prerequisites I have not yet reached, with their level.
  D-mix    confusable_pairs — confusable_with edges touching my known/candidate
            set (concepts I should be careful not to conflate while learning).

Pure read-only over corpus/concept_graph.json + state/learner/
mastery_graph.sqlite (via core.mastery). Artifact lands at
state/learner/learning_path.json (mirrors the learner-scoped modules).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core import mastery
from core.state import DEFAULT_STATE_ROOT

REPO_ROOT = Path(__file__).resolve().parent.parent
_GRAPH_PATH = REPO_ROOT / "corpus" / "concept_graph.json"

# Levels that count as "known enough to build on". familiar = touched but not
# proven; attempted = merely tried. Only proficient+ unlocks downstream work.
_KNOWN_LEVELS = ("proficient", "mastered")
_TOP_NEXT = 12
_TOP_UNMET = 12
_TOP_CONFUSABLE = 8


def _graph_path() -> Path:
    """Indirection so tests can monkeypatch the graph location cleanly."""
    return _GRAPH_PATH


def _load_graph() -> dict | None:
    p = _graph_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _iso(now: float | None) -> str:
    if now is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(now, tz=timezone.utc).isoformat()


def analyze(learner_login: str = "DongKey777",
            state_root: Path = DEFAULT_STATE_ROOT,
            now: float | None = None) -> dict:
    generated_at = _iso(now)

    graph = _load_graph()
    if graph is None:
        return _empty("missing_graph", learner_login, generated_at,
                      {"counts": {lvl: 0 for lvl in mastery.BLOOM_LEVELS},
                       "total_tracked": 0})

    summary = mastery.summary(state_root)
    counts = summary["counts"]
    mastery_distribution = {**counts, "total": summary["total_tracked"]}

    db = state_root / "learner" / "mastery_graph.sqlite"
    if not db.exists() or summary["total_tracked"] == 0:
        return _empty("missing_mastery", learner_login, generated_at,
                      mastery_distribution)

    nodes: dict = graph.get("nodes", {})
    edges: dict = graph.get("edges", {})
    prereq_edges = edges.get("prerequisite", [])
    next_edges = edges.get("next_docs", [])
    confusable_edges = edges.get("confusable_with", [])

    # KNOWN = proficient + mastered. familiar/attempted are excluded.
    known: set[str] = set()
    for lvl in _KNOWN_LEVELS:
        known.update(mastery.list_by_level(lvl, state_root))

    # Bloom level lookup for prereq_level reporting ("미학습" if untracked).
    levels: dict[str, str] = {}
    for lvl in mastery.BLOOM_LEVELS:
        for cid in mastery.list_by_level(lvl, state_root):
            levels[cid] = lvl

    # Build adjacency. prerequisite [src, dst] = dst is a prereq OF src.
    # next_docs [src, dst]   = after src, dst is a natural next step.
    prereqs_of: dict[str, list[str]] = {}      # src -> [dst prereqs]
    for src, dst in prereq_edges:
        prereqs_of.setdefault(src, []).append(dst)
    next_of: dict[str, list[str]] = {}         # src -> [next-step dst]
    for src, dst in next_edges:
        next_of.setdefault(src, []).append(dst)
    # prerequisite-children: src concepts for which dst (a known concept) is a
    # prereq — known concept dst unlocks those src.
    unlocked_by: dict[str, list[str]] = {}     # prereq dst -> [src it unlocks]
    for src, dst in prereq_edges:
        unlocked_by.setdefault(dst, []).append(src)

    # Candidate surfacing: for each known concept k, gather next_docs targets
    # and prerequisite-children. Keep the first known concept that surfaced it.
    surfaced_by: dict[str, str] = {}
    for k in sorted(known):
        for dst in next_of.get(k, []):
            surfaced_by.setdefault(dst, k)
        for child in unlocked_by.get(k, []):
            surfaced_by.setdefault(child, k)

    next_concepts: list[dict] = []
    unmet: list[dict] = []
    for cid, from_known in surfaced_by.items():
        if cid in known:
            continue
        cand_prereqs = prereqs_of.get(cid, [])
        prereqs_total = len(cand_prereqs)
        met = [p for p in cand_prereqs if p in known]
        prereqs_met = len(met)
        ready = prereqs_total == 0 or prereqs_met == prereqs_total
        node = nodes.get(cid) or {}
        next_concepts.append({
            "concept_id": cid,
            "category": node.get("category", "") if node else "",
            "from_known": from_known,
            "prereqs_met": prereqs_met,
            "prereqs_total": prereqs_total,
            "ready": ready,
        })
        if not ready:
            for p in cand_prereqs:
                if p in known:
                    continue
                unmet.append({
                    "concept_id": cid,
                    "missing_prereq": p,
                    "prereq_level": levels.get(p, "미학습"),
                })

    # Ready-first, then most-prepared (prereqs_met desc), stable by concept_id.
    next_concepts.sort(
        key=lambda c: (0 if c["ready"] else 1, -c["prereqs_met"], c["concept_id"]))
    next_concepts = next_concepts[:_TOP_NEXT]
    # Keep only unmet-prereq rows for candidates that survived the next_concepts
    # truncation — otherwise unmet could reference a concept the learner never
    # sees in next_concepts (a dangling, inconsistent recommendation).
    surviving = {c["concept_id"] for c in next_concepts}
    unmet = [u for u in unmet if u["concept_id"] in surviving][:_TOP_UNMET]

    # confusable_with edges touching known ∪ candidate. Dedupe unordered pairs.
    candidate_ids = {c["concept_id"] for c in next_concepts}
    in_scope = known | candidate_ids
    confusable_pairs: list[dict] = []
    seen_pairs: set[frozenset] = set()
    for a, b in confusable_edges:
        if a not in in_scope and b not in in_scope:
            continue
        key = frozenset((a, b))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        confusable_pairs.append({"a": a, "b": b})
        if len(confusable_pairs) >= _TOP_CONFUSABLE:
            break

    return {
        "learner_login": learner_login,
        "status": "ok",
        "mastery_distribution": mastery_distribution,
        "next_concepts": next_concepts,
        "unmet_prereqs": unmet,
        "confusable_pairs": confusable_pairs,
        "graph_sparse": len(prereq_edges) == 0,
        "counts": {
            "next_concepts": len(next_concepts),
            "unmet_prereqs": len(unmet),
            "confusable_pairs": len(confusable_pairs),
        },
        "generated_at": generated_at,
    }


def _empty(status: str, learner_login: str, generated_at: str,
           mastery_distribution: dict) -> dict:
    return {
        "learner_login": learner_login,
        "status": status,
        "mastery_distribution": mastery_distribution,
        "next_concepts": [],
        "unmet_prereqs": [],
        "confusable_pairs": [],
        "graph_sparse": status == "missing_graph",
        "counts": {"next_concepts": 0, "unmet_prereqs": 0, "confusable_pairs": 0},
        "generated_at": generated_at,
    }


def save_learning_path(report: dict,
                       state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out_dir = state_root / "learner"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "learning_path.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
