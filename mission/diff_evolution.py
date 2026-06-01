"""PR code-evolution analysis (F1 / family H, mode ``pr_diff_evolution``).

Reads the per-repo SQLite archive + the mission git clone (scoped to the PR
commit range via ``base..head``) to reconstruct how a learner's PR evolved
across mentor review rounds:

  H1 rounds        — mentor reviews (``reviews.submitted_at``, reviewer != learner)
                     are round markers; the learner commits that followed each
                     review are the "fix" pushed in response. Comments are
                     grouped to their review via ``github_review_id``.
  H2 review→fix    — for a mentor comment on path P at time T, the earliest
                     PR-range learner commit after T that touched P is the fix
                     that review triggered (``review_comments`` carries the real
                     anchor commit SHA, so this is grounded, not guessed).
  H3 hotspots      — ``review_comments`` GROUP BY path (SQL only; clone-free).
  H4 smells        — ``mission.extract`` over the latest ``patch_text`` so the
                     learner gets a pre-push pattern read (clone-free).

H3/H4 always work from the archive alone. H1/H2 degrade to review-only rounds
when the clone is absent or the PR commit range can't be resolved. Git is
always scoped to ``base..head`` (capped by ``commits_count`` on fallback) —
never the whole fork history.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
from pathlib import Path

from mission.extract import extract_from_text

_TOP_PATHS = 6
_TOP_LINKS = 8
_TOP_SMELLS = 12


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _reviews_login_col(conn: sqlite3.Connection) -> str:
    """Reviews table drifts: member/auth use reviewer_login, waiting user_login."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pull_request_reviews_current)")}
    return "reviewer_login" if "reviewer_login" in cols else "user_login"


def _git(clone_dir: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(clone_dir), *args],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _pr_commits(clone_dir: Path, base_ref: str | None, head_sha: str,
                commits_count: int | None) -> list[dict]:
    """Ordered PR commits {sha, at, subject}. base..head preferred; capped
    head-only walk on fallback (never the whole fork history)."""
    fmt = "--format=%H\x1f%cI\x1f%s"
    out = None
    if base_ref:
        out = _git(clone_dir, "log", fmt, f"{base_ref}..{head_sha}")
    if out is None:
        cap = ["-n", str(commits_count)] if commits_count else ["-n", "60"]
        out = _git(clone_dir, "log", fmt, *cap, head_sha)
    if out is None:
        return []
    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        commits.append({"sha": parts[0], "at": _parse_iso(parts[1]),
                        "subject": parts[2]})
    commits.sort(key=lambda c: c["at"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return commits


def _commit_files(clone_dir: Path, sha: str, cache: dict[str, set[str]]) -> set[str]:
    if sha in cache:
        return cache[sha]
    out = _git(clone_dir, "show", "--name-only", "--format=", sha)
    files = {ln.strip() for ln in (out or "").splitlines() if ln.strip()}
    cache[sha] = files
    return files


def _earliest_fix(commits: list[dict], after: dt.datetime | None, path: str,
                  clone_dir: Path, cache: dict[str, set[str]]) -> dict | None:
    """First PR commit after `after` that touched `path`."""
    if after is None:
        return None
    for c in commits:
        if c["at"] is None or c["at"] <= after:
            continue
        if path in _commit_files(clone_dir, c["sha"], cache):
            return c
    return None


def _hotspots(conn: sqlite3.Connection, pr_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT path, COUNT(*) c FROM pull_request_review_comments_current "
        "WHERE pull_request_id=? AND path IS NOT NULL GROUP BY path "
        "ORDER BY c DESC, path LIMIT ?",
        (pr_id, _TOP_PATHS),
    ).fetchall()
    return [{"path": r["path"], "comments": r["c"]} for r in rows]


def _smells(conn: sqlite3.Connection, pr_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT path, patch_text FROM pull_request_files_current "
        "WHERE pull_request_id=? AND path LIKE '%.java'",
        (pr_id,),
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        if not r["patch_text"]:
            continue
        for p in extract_from_text(r["path"], r["patch_text"]):
            key = (p.file, p.matched_concept_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({"path": p.file, "concept_id": p.matched_concept_id,
                        "value": p.value, "line": p.line, "kind": p.kind})
            if len(out) >= _TOP_SMELLS:
                return out
    return out


def _analyze_pr(conn: sqlite3.Connection, pr: sqlite3.Row, login_col: str,
                clone_dir: Path | None) -> dict:
    pr_id, number = pr["id"], pr["number"]
    head_sha = pr["head_sha"]
    learner = pr["author_login"]

    reviews = conn.execute(
        f"SELECT github_review_id AS rid, {login_col} AS who, state, submitted_at "
        "FROM pull_request_reviews_current WHERE pull_request_id=? ORDER BY submitted_at",
        (pr_id,),
    ).fetchall()
    mentor_reviews = [r for r in reviews if r["who"] and r["who"] != learner]

    # comments grouped to their review via github_review_id
    comment_rows = conn.execute(
        "SELECT github_review_id AS rid, path, body, created_at, user_login "
        "FROM pull_request_review_comments_current WHERE pull_request_id=?",
        (pr_id,),
    ).fetchall()
    by_review: dict[int, list[sqlite3.Row]] = {}
    for c in comment_rows:
        by_review.setdefault(c["rid"], []).append(c)

    commits: list[dict] = []
    if clone_dir is not None and head_sha:
        commits = _pr_commits(clone_dir, pr["base_ref_name"], head_sha,
                              pr["commits_count"])
    fcache: dict[str, set[str]] = {}

    def _commit_dict(c: dict) -> dict:
        n = len(_commit_files(clone_dir, c["sha"], fcache)) if clone_dir else 0
        return {"sha": c["sha"][:8], "at": c["at"].isoformat() if c["at"] else None,
                "subject": c["subject"], "files_changed": n}

    rounds: list[dict] = []
    for i, rv in enumerate(mentor_reviews):
        t_now = _parse_iso(rv["submitted_at"])
        t_next = _parse_iso(mentor_reviews[i + 1]["submitted_at"]) if i + 1 < len(mentor_reviews) else None
        fixes = [c for c in commits if c["at"] and t_now and c["at"] > t_now
                 and (t_next is None or c["at"] <= t_next)]
        rcomments = by_review.get(rv["rid"], [])
        paths: dict[str, int] = {}
        for c in rcomments:
            if c["path"]:
                paths[c["path"]] = paths.get(c["path"], 0) + 1
        rounds.append({
            "index": i + 1,
            "reviewer": rv["who"],
            "state": rv["state"],
            "submitted_at": rv["submitted_at"],
            "comment_count": len(rcomments),
            "comment_paths": sorted(
                ({"path": p, "count": n} for p, n in paths.items()),
                key=lambda x: (-x["count"], x["path"]))[:_TOP_PATHS],
            "fix_commits": [_commit_dict(c) for c in fixes],
        })

    # H2 review→fix links (mentor comments only, grounded by next-touch commit)
    links: list[dict] = []
    if commits:
        for c in comment_rows:
            if not c["path"] or c["user_login"] == learner:
                continue
            fix = _earliest_fix(commits, _parse_iso(c["created_at"]), c["path"],
                                clone_dir, fcache)
            if fix is None:
                continue
            body = (c["body"] or "").strip().replace("\n", " ")
            links.append({
                "path": c["path"],
                "reviewer": c["user_login"],
                "review_at": c["created_at"],
                "comment_excerpt": body[:140],
                "fix_sha": fix["sha"][:8],
                "fix_at": fix["at"].isoformat() if fix["at"] else None,
                "fix_subject": fix["subject"],
            })
            if len(links) >= _TOP_LINKS:
                break

    return {
        "number": number,
        "merged": bool(pr["merged_at"]),
        "head_sha": (head_sha or "")[:8],
        "round_count": len(rounds),
        "rounds": rounds,
        "review_to_fix": links,
        "hotspots": _hotspots(conn, pr_id),
        "smells": _smells(conn, pr_id),
    }


def analyze_repo(repo: str, learner_login: str, state_root: Path,
                 mission_root: Path | None = None) -> dict:
    """Build the pr_diff_evolution artifact for one repo's learner PRs.

    ``mission_root`` holds the git clones (default: ``state_root.parent/missions``
    — the production layout where missions/ is state/'s sibling). A tmp-state
    test without a clone simply yields ``clone_available=False`` (H3/H4 only)."""
    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return {"repo": repo, "status": "missing_archive", "prs": []}

    if mission_root is None:
        mission_root = state_root.parent / "missions"
    clone = mission_root / repo
    clone_dir = clone if (clone / ".git").exists() else None

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        login_col = _reviews_login_col(conn)
        prs = conn.execute(
            "SELECT id, number, author_login, head_sha, base_ref_name, "
            "commits_count, merged_at FROM pull_requests_current "
            "WHERE author_login=? ORDER BY number",
            (learner_login,),
        ).fetchall()
        analyzed = [_analyze_pr(conn, pr, login_col, clone_dir) for pr in prs]
    finally:
        conn.close()

    return {
        "repo": repo,
        "status": "ok",
        "learner_login": learner_login,
        "clone_available": clone_dir is not None,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "prs": analyzed,
    }


def save_evolution(report: dict, repo: str, state_root: Path) -> Path:
    out = state_root / "repos" / repo / "pr_diff_evolution.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
