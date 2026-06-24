"""mission.peer_pr — family P (learner-vs-peer PR diff comparison, mode peer_compare).

Repo-scoped, learner-scoped. Answers "동기들은 이 미션 코드 어떻게 짰어?": it
sets the learner's most-recent PR next to the most *comparable* peers' PRs on
the same mission and surfaces the QUALITATIVE difference — which files everyone
touched, which only the learner / only the peer touched, and the real mentor
review threads that landed on the peers' PRs. Pure peer-context, read-only.

This complements family F (cohort): cohort places the learner on a QUANTITATIVE
distribution (PR size / review-count percentiles); peer_pr is the qualitative
"how did they actually structure it, and what did the mentor say there" view.

Peer selection = Jaccard file-set similarity to the learner's target PR
(tie-broken by recency). Jaccard rewards a peer who touched *the same slice* of
the project over one who happened to touch everything, so same-step peers rank
to the top without any explicit step label (empirically the learner's pair
partner + same-cycle crews surface first). Capped at max_peers (learner safety
+ prompt budget).

Reads state/repos/<repo>/archive/prs.sqlite3 (the *_current tables, via
core.archive) and writes state/repos/<repo>/peer_pr.json (loaded in mode
peer_compare). The heavy ranking pass loads every PR's path set in TWO queries
(all PRs + all file rows) rather than one query per PR.

Schema-drift safe: an absent table/db yields status "missing_archive"; no
learner PR → "no_learner_pr"; no other-author PR with files → "no_peer".
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from core import peer_pr as _peer_pr
from core.state import DEFAULT_STATE_ROOT

# Artifact-side caps (the render caps again, tighter — these just bound file size).
_MAX_FILES_STORED = 40
_MAX_THREADS_PER_PR = 5
_MAX_THREAD_BODY_CHARS = 600
_DEFAULT_MAX_PEERS = 2


def _present(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _paths_by_pr_id(conn: sqlite3.Connection) -> dict[int, set[str]]:
    """One pass over pull_request_files_current → {pull_request_id: {path,...}}."""
    out: dict[int, set[str]] = {}
    if not _present(conn, "pull_request_files_current"):
        return out
    for r in conn.execute(
        "SELECT pull_request_id, path FROM pull_request_files_current"
    ):
        pid, path = r[0], r[1]
        if pid is None or not path:
            continue
        out.setdefault(int(pid), set()).add(path)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _select_peers(
    pr_rows: list[sqlite3.Row],
    paths_by_id: dict[int, set[str]],
    learner_login: str,
    target_id: int,
    max_peers: int,
) -> list[dict]:
    """Rank other-author PRs by Jaccard file-similarity to the target, tie-broken
    by recency (PR number desc). Returns up to max_peers selection rows."""
    learner_paths = paths_by_id.get(target_id, set())
    ranked: list[tuple[float, int, dict]] = []
    for r in pr_rows:
        if r["author_login"] is None or r["author_login"] == learner_login:
            continue
        if "[bot]" in str(r["author_login"]):
            continue
        peer_paths = paths_by_id.get(int(r["id"]), set())
        if not peer_paths:
            continue
        jac = _jaccard(learner_paths, peer_paths)
        ranked.append((jac, int(r["number"]), {
            "number": int(r["number"]),
            "author_login": r["author_login"],
            "nickname": _peer_pr.extract_nickname(r["title"] or ""),
            "jaccard": round(jac, 3),
            "common_files": len(learner_paths & peer_paths),
        }))
    # Highest Jaccard first; ties → most recent PR first.
    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [row for _, _, row in ranked[:max_peers]]


def _cap_files(paths: list[str]) -> list[str]:
    return list(paths)[:_MAX_FILES_STORED]


def _thread_to_artifact(t: dict) -> dict:
    """Trim a stitched thread to {root, reply_count} with a bounded body."""
    root = dict(t.get("root") or {})
    body = (root.get("body") or "")[:_MAX_THREAD_BODY_CHARS]
    return {
        "root": {
            "author_login": root.get("author_login"),
            "path": root.get("path"),
            "line": root.get("line"),
            "body": body,
        },
        "reply_count": len(t.get("replies") or []),
    }


def _to_artifact(repo: str, learner_login: str, cmp, selection: list[dict],
                 generated_at: str) -> dict:
    """PeerPRComparison → the flat JSON shape the prompt renderer consumes.

    peer_files / representative_threads are keyed by the STRING PR number on
    purpose: JSON coerces int keys to strings on write, so building them as
    strings here keeps the artifact load-stable (the renderer looks them up with
    str(number))."""
    nick_by_number = {s["number"]: s["nickname"] for s in selection}
    peer_prs = [
        {
            "number": p["number"],
            "title": p.get("title", ""),
            "nickname": p.get("nickname") or nick_by_number.get(p["number"]),
            "author_login": p.get("author_login"),
            "additions": p.get("additions", 0),
            "deletions": p.get("deletions", 0),
        }
        for p in cmp.peer_prs
    ]
    peer_files = {
        str(p["number"]): _cap_files([f["path"] for f in p.get("files", [])])
        for p in cmp.peer_prs
    }
    # Per-peer "files this peer touched that the learner did NOT" — precomputed
    # from the FULL (uncapped) learner path set here so the renderer never
    # re-derives it from the capped learner_files (which would mislabel a shared
    # file as peer-only once learner_files exceeds the cap). Mirrors how the
    # cohort artifact precomputes its comparison rows for the renderer to read.
    learner_full_paths = {f["path"] for f in cmp.learner_pr.get("files", [])}
    peer_only_files = {
        str(p["number"]): _cap_files(
            [f["path"] for f in p.get("files", []) if f["path"] not in learner_full_paths])
        for p in cmp.peer_prs
    }
    threads = {
        str(pn): [_thread_to_artifact(t) for t in ts[:_MAX_THREADS_PER_PR]]
        for pn, ts in (cmp.representative_threads or {}).items()
    }
    learner = cmp.learner_pr
    return {
        "repo": repo,
        "status": "ok",
        "learner_login": learner_login,
        "learner_pr": {
            "number": learner["number"],
            "title": learner.get("title", ""),
            "nickname": learner.get("nickname"),
            "additions": learner.get("additions", 0),
            "deletions": learner.get("deletions", 0),
        },
        "learner_files": _cap_files([f["path"] for f in learner.get("files", [])]),
        "peer_prs": peer_prs,
        "peer_files": peer_files,
        "peer_only_files": peer_only_files,
        "common_paths": list(cmp.common_paths),
        "learner_only_paths": list(cmp.learner_only_paths),
        "peer_only_paths": _cap_files(list(cmp.peer_only_paths)),
        "representative_threads": threads,
        "selection": selection,
        "counts": {
            "peers": len(peer_prs),
            "common_paths": len(cmp.common_paths),
            "learner_only_paths": len(cmp.learner_only_paths),
            "peer_only_paths": len(cmp.peer_only_paths),
        },
        "generated_at": generated_at,
    }


def analyze_repo(repo: str, learner_login: str = "DongKey777",
                 state_root: Path = DEFAULT_STATE_ROOT,
                 max_peers: int = _DEFAULT_MAX_PEERS,
                 now: float | None = None) -> dict:
    """Build the peer-PR comparison artifact for one repo. Read-only."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    def _empty(status: str) -> dict:
        return {
            "repo": repo, "status": status, "learner_login": learner_login,
            "learner_pr": {}, "learner_files": [], "peer_prs": [],
            "peer_files": {}, "common_paths": [], "learner_only_paths": [],
            "peer_only_paths": [], "representative_threads": {},
            "selection": [], "counts": {"peers": 0},
            "generated_at": generated_at,
        }

    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return _empty("missing_archive")

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        if not _present(conn, "pull_requests_current"):
            return _empty("missing_archive")
        pr_rows = conn.execute(
            "SELECT id, number, author_login, title, additions, deletions "
            "FROM pull_requests_current"
        ).fetchall()
        paths_by_id = _paths_by_pr_id(conn)
    finally:
        conn.close()

    learner_rows = [r for r in pr_rows if r["author_login"] == learner_login]
    if not learner_rows:
        return _empty("no_learner_pr")
    target = max(learner_rows, key=lambda r: int(r["number"]))
    target_number = int(target["number"])

    selection = _select_peers(
        pr_rows, paths_by_id, learner_login, int(target["id"]), max_peers)
    if not selection:
        return _empty("no_peer")

    cmp = _peer_pr.compare(
        repo, target_number, [s["number"] for s in selection],
        max_peers=max_peers, state_root=state_root)
    return _to_artifact(repo, learner_login, cmp, selection, generated_at)


def save_peer_pr(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "peer_pr.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
