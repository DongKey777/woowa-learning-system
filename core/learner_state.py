"""Phase T5: assess-learner-state — snapshot learner's mission repo state.

paradigm-v2 v1: pure local — git subprocess + SQLite archive read only. No
GitHub API call (gh wrapper deferred to Phase U `scripts/collection/`).

Output: state/repos/<repo>/contexts/learner-state.json with
- head_branch, head_sha
- working_copy: {clean, dirty_paths, untracked_paths, ahead, behind}
- prs[]: open + recent closed (state, title, head_ref) from SQLite archive
- target_pr_detail.threads[]: mentor review threads at target PR (most recent)
- coverage: full | partial | none

Thread classification heuristic (no GraphQL isResolved access):
- "still-applies": mentor root with no learner reply AND target file/line still
  present in HEAD diff
- "likely-fixed": mentor root with no learner reply AND target file no longer
  in HEAD diff (cited code removed)
- "already-fixed": mentor root WITH learner reply on same comment thread
- "ambiguous": mentor root with no learner reply AND file present but line
  context can't be matched
- "unread": no learner activity on this PR after mentor comment

Public API:
  assess_learner_state(repo, mission_path, state_root, budget_secs) -> dict
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

BUDGET_DEFAULT = 60.0


def _git(cwd: Path, *args: str) -> tuple[str, str, int]:
    """Run git -C cwd <args> and return (stdout, stderr, rc)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except FileNotFoundError:
        return "", "git CLI not installed", 127
    except subprocess.TimeoutExpired:
        return "", "git command timed out", 124


def _working_copy(mission_path: Path) -> dict:
    if not (mission_path / ".git").exists():
        return {"clean": None, "error": "not a git repo"}
    # porcelain status — must preserve leading whitespace per line (status
    # column convention: " M file", "?? file", "MM file" etc.)
    try:
        r = subprocess.run(
            ["git", "-C", str(mission_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
        rc = r.returncode
        raw = r.stdout
    except FileNotFoundError:
        return {"clean": None, "error": "git CLI not installed"}
    except subprocess.TimeoutExpired:
        return {"clean": None, "error": "git status timed out"}
    if rc != 0:
        return {"clean": None, "error": "git status failed"}
    lines = [l for l in raw.splitlines() if l.strip()]
    dirty = [l[3:] for l in lines if not l.startswith("??")]
    untracked = [l[3:] for l in lines if l.startswith("??")]
    # ahead/behind
    upstream, _, _ = _git(mission_path, "rev-parse", "--abbrev-ref", "@{u}")
    ahead = behind = 0
    if upstream:
        rev_list, _, rc2 = _git(mission_path, "rev-list", "--left-right", "--count",
                                  f"HEAD...{upstream}")
        if rc2 == 0 and rev_list:
            try:
                a, b = rev_list.split()
                ahead, behind = int(a), int(b)
            except ValueError:
                pass
    return {
        "clean": len(dirty) == 0 and len(untracked) == 0,
        "dirty_paths": dirty[:20],
        "untracked_paths": untracked[:20],
        "ahead": ahead, "behind": behind, "upstream": upstream or None,
    }


def _git_refs(mission_path: Path) -> dict:
    branch, _, _ = _git(mission_path, "rev-parse", "--abbrev-ref", "HEAD")
    sha, _, _ = _git(mission_path, "rev-parse", "HEAD")
    return {"head_branch": branch or None, "head_sha": sha or None}


def _list_prs_from_sqlite(repo: str, learner_login: str,
                            state_root: Path, limit: int = 5) -> list[dict]:
    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, number, title, state, head_ref_name, base_ref_name, "
            "head_sha, created_at, merged_at, closed_at "
            "FROM pull_requests_current "
            "WHERE author_login = ? "
            "ORDER BY number DESC LIMIT ?",
            (learner_login, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _classify_thread(
    mentor_body: str, path: str | None, line: int | None,
    has_learner_reply: bool, file_present_in_head: bool,
) -> str:
    """Heuristic classification — no GraphQL isResolved access in v1."""
    if has_learner_reply:
        return "already-fixed"
    if path is None:
        return "ambiguous"
    if not file_present_in_head:
        return "likely-fixed"
    if line is None:
        return "ambiguous"
    return "still-applies"


def _file_exists_in_head(mission_path: Path, path: str) -> bool:
    if not path:
        return False
    out, _, rc = _git(mission_path, "cat-file", "-e", f"HEAD:{path}")
    return rc == 0


def _target_pr_threads(
    pr_id: int, learner_login: str, mission_path: Path,
    state_root: Path, repo: str, max_threads: int = 20,
) -> list[dict]:
    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        # learner reply lookup table
        learner_replies = {r["in_reply_to_github_comment_id"] for r in conn.execute(
            "SELECT in_reply_to_github_comment_id FROM "
            "pull_request_review_comments_current "
            "WHERE pull_request_id = ? AND user_login = ?",
            (pr_id, learner_login),
        ).fetchall() if r["in_reply_to_github_comment_id"]}
        rows = conn.execute(
            "SELECT github_comment_id, user_login, body, path, line, created_at "
            "FROM pull_request_review_comments_current "
            "WHERE pull_request_id = ? AND user_login != ? "
            "AND in_reply_to_github_comment_id IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (pr_id, learner_login, max_threads),
        ).fetchall()
    finally:
        conn.close()

    threads = []
    for r in rows:
        path = r["path"]
        line = r["line"]
        has_reply = r["github_comment_id"] in learner_replies
        present = _file_exists_in_head(mission_path, path) if path else False
        classification = _classify_thread(
            mentor_body=r["body"] or "", path=path, line=line,
            has_learner_reply=has_reply, file_present_in_head=present,
        )
        threads.append({
            "thread_id": str(r["github_comment_id"]),
            "mentor_login": r["user_login"],
            "path": path,
            "line": line,
            "body_excerpt": (r["body"] or "")[:200],
            "classification": classification,
            "has_learner_reply": has_reply,
            "file_present_in_head": present,
        })
    return threads


def assess_learner_state(
    repo: str,
    mission_path: Path,
    state_root: Path = DEFAULT_STATE_ROOT,
    learner_login: str | None = None,
    budget_secs: float = BUDGET_DEFAULT,
    target_pr_number: int | None = None,
    now: float | None = None,
) -> dict:
    """Snapshot mission repo state. Bounded by `budget_secs` wall-clock.

    learner_login defaults to auto-detection via core.identity.
    """
    from core.identity import resolve_learner_login
    if learner_login is None:
        learner_login = resolve_learner_login(state_root)
    t0 = time.time()
    ts = now or t0

    refs = _git_refs(mission_path)
    if time.time() - t0 > budget_secs:
        return _payload_partial(repo, mission_path, refs, ts, reason="budget exhausted at refs")

    wc = _working_copy(mission_path)
    if time.time() - t0 > budget_secs:
        return _payload_partial(repo, mission_path, refs, ts,
                                  working_copy=wc, reason="budget exhausted at working_copy")

    prs = _list_prs_from_sqlite(repo, learner_login, state_root, limit=10)
    # pick target PR: explicit > most-recent open > most-recent
    target_pr = None
    if target_pr_number is not None:
        target_pr = next((p for p in prs if p["number"] == target_pr_number), None)
    if target_pr is None:
        target_pr = next((p for p in prs if p["state"] == "open"), None)
    if target_pr is None and prs:
        target_pr = prs[0]

    threads = []
    if target_pr is not None:
        threads = _target_pr_threads(target_pr["id"], learner_login,
                                      mission_path, state_root, repo)

    elapsed = time.time() - t0
    coverage = "full" if elapsed < budget_secs else "partial"
    payload = {
        "schema_version": "v1",
        "computed_at": ts,
        "repo": repo,
        "mission_path": str(mission_path),
        "learner_login": learner_login,
        "elapsed_s": round(elapsed, 3),
        "budget_secs": budget_secs,
        "coverage": coverage,
        **refs,
        "working_copy": wc,
        "prs": prs,
        "target_pr": ({
            "number": target_pr["number"],
            "title": target_pr["title"],
            "state": target_pr["state"],
            "threads_total": len(threads),
            "threads": threads,
        } if target_pr else None),
    }
    out = state_root / "repos" / repo / "contexts" / "learner-state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["out_path"] = str(out)
    return payload


def _payload_partial(repo, mission_path, refs, ts, working_copy=None, reason=""):
    return {
        "schema_version": "v1", "computed_at": ts, "repo": repo,
        "mission_path": str(mission_path), "coverage": "partial",
        "partial_reason": reason, **refs,
        "working_copy": working_copy or {"clean": None},
        "prs": [], "target_pr": None,
    }
