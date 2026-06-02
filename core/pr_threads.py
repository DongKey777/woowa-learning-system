"""Live, pending-aware PR review-thread reconciliation.

Unlike the offline SQLite archive (core/pr_retro.py, mission/thread_recon.py),
which is submitted-only and stale since the last `bin/sync-prs`, this queries
GitHub live on every call and reconciles three sources:

  1. mentor root comments      — REST /pulls/{n}/comments, in_reply_to absent, non-learner non-bot
  2. submitted learner replies — REST /pulls/{n}/comments, in_reply_to == root, author == learner
  3. learner PENDING drafts     — REST /pulls/{n}/reviews/{pending_id}/comments (author-only)

unanswered = mentor roots ∖ (submitted replies ∪ pending drafts).

GraphQL reviewThreads overlays per-thread isResolved/isOutdated/resolvedBy and
PR-level reviewDecision — state the REST API cannot provide — keyed by the root
comment's databaseId. A snapshot is persisted per call so the next call can
report the delta (newly resolved / new mentor comments / newly answered).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.state import DEFAULT_STATE_ROOT, atomic_write_json
from scripts.collection.github_client import GitHubCLIClient, GitHubCLIError

EXCERPT_LIMIT = 160
_DECISIVE_STATES = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
_STATUS_ORDER = {"unanswered": 0, "answered_pending": 1, "answered_submitted": 2}


def _excerpt(body: Any, limit: int = EXCERPT_LIMIT) -> str:
    """Collapse newlines to spaces, strip, truncate with a trailing ellipsis."""
    if not body:
        return ""
    text = " ".join(str(body).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _is_bot(user: dict[str, Any] | None) -> bool:
    user = user or {}
    login = user.get("login") or ""
    return user.get("type") == "Bot" or login.endswith("[bot]")


def _comment_ts(comment: dict[str, Any]) -> str:
    return comment.get("updated_at") or comment.get("created_at") or ""


def _snapshot_path(repo: str, pr_number: int, state_root: Path) -> Path:
    return Path(state_root) / "repos" / repo / "pr_threads" / f"{pr_number}.json"


def _find_pending_review(reviews: list[dict], learner_login: str) -> dict | None:
    candidates = [
        r for r in reviews
        if r.get("state") == "PENDING"
        and (r.get("user") or {}).get("login") == learner_login
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("id") or 0)


def _graphql_overlay(client: GitHubCLIClient, pr_number: int) -> tuple[dict[int, dict], str | None]:
    """Best-effort GraphQL enrichment. Returns ({root_id: {...}}, review_decision).

    GraphQL is advisory: a failure degrades to an empty overlay rather than
    failing the whole reconciliation (the REST spine stands alone).
    """
    try:
        result = client.fetch_review_threads(pr_number)
    except GitHubCLIError:
        return {}, None
    overlay: dict[int, dict] = {}
    for node in result.get("nodes") or []:
        comments = (node.get("comments") or {}).get("nodes") or []
        if not comments:
            continue
        db_id = comments[0].get("databaseId")
        if db_id is None:
            continue
        overlay[int(db_id)] = {
            "is_resolved": bool(node.get("isResolved")),
            "is_outdated": bool(node.get("isOutdated")),
            "resolved_by": (node.get("resolvedBy") or {}).get("login"),
        }
    return overlay, result.get("review_decision")


def _reviewer_decisions(reviews: list[dict], learner_login: str) -> dict[str, str]:
    """Per non-learner reviewer, their most recent decisive review state."""
    decisions: dict[str, str] = {}
    for r in sorted(reviews, key=lambda x: x.get("submitted_at") or ""):
        login = (r.get("user") or {}).get("login")
        state = r.get("state")
        if not login or login == learner_login or state not in _DECISIVE_STATES:
            continue
        decisions[login] = state  # later (more recent) wins
    return decisions


def _round_timeline(reviews: list[dict], submitted: list[dict], learner_login: str) -> list[dict]:
    counts_by_review: dict[Any, int] = {}
    for c in submitted:
        rid = c.get("pull_request_review_id")
        counts_by_review[rid] = counts_by_review.get(rid, 0) + 1
    timeline = []
    for r in sorted(reviews, key=lambda x: x.get("submitted_at") or ""):
        if not r.get("submitted_at"):  # PENDING / unsubmitted
            continue
        login = (r.get("user") or {}).get("login")
        timeline.append({
            "actor": "learner" if login == learner_login else "mentor",
            "login": login,
            "state": r.get("state"),
            "submitted_at": r.get("submitted_at"),
            "comment_count": counts_by_review.get(r.get("id"), 0),
        })
    return timeline


def _load_snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _compute_delta(threads: list[dict], prev: dict | None) -> dict:
    if not prev:
        return {"first_run": True, "newly_resolved": [], "new_mentor_roots": [],
                "newly_answered": []}
    prev_threads = {int(k): v for k, v in (prev.get("threads") or {}).items()}
    newly_resolved, new_roots, newly_answered = [], [], []
    for t in threads:
        rid = t["root_comment_id"]
        ref = {"root_comment_id": rid, "path": t["path"]}
        before = prev_threads.get(rid)
        if before is None:
            new_roots.append(ref)
            if t["is_resolved"]:
                newly_resolved.append(ref)
            if t["status"] == "answered_submitted":
                newly_answered.append(ref)
            continue
        if t["is_resolved"] and not before.get("is_resolved"):
            newly_resolved.append(ref)
        if t["status"] == "answered_submitted" and before.get("status") != "answered_submitted":
            newly_answered.append(ref)
    return {"first_run": False, "newly_resolved": newly_resolved,
            "new_mentor_roots": new_roots, "newly_answered": newly_answered}


def _write_snapshot(path: Path, pr_number: int, threads: list[dict]) -> None:
    payload = {
        "pr_number": pr_number,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "threads": {
            str(t["root_comment_id"]): {"status": t["status"], "is_resolved": t["is_resolved"]}
            for t in threads
        },
    }
    atomic_write_json(path, payload)


def reconcile_pr_threads(
    repo: str,
    pr_number: int,
    learner_login: str,
    owner: str = "woowacourse",
    state_root: Path = DEFAULT_STATE_ROOT,
    client: GitHubCLIClient | None = None,
    write_snapshot: bool = True,
) -> dict[str, Any]:
    client = client or GitHubCLIClient(owner, repo)
    try:
        detail = client.fetch_pull_request_detail(pr_number)
        reviews = client.fetch_pull_request_reviews(pr_number)
        submitted = client.fetch_pull_request_review_comments(pr_number)
        pending_review = _find_pending_review(reviews, learner_login)
        pending_comments: list[dict] = []
        if pending_review is not None:
            pending_comments = client.fetch_pull_request_review_comments_for_review(
                pr_number, pending_review["id"])
    except GitHubCLIError as e:
        return {"status": "error", "pr_number": pr_number, "error": str(e)}

    overlay, review_decision = _graphql_overlay(client, pr_number)

    # REST spine: group submitted/pending replies by their thread root id.
    submitted_by_root: dict[int, list[dict]] = {}
    for c in submitted:
        rid = c.get("in_reply_to_id")
        if rid is not None:
            submitted_by_root.setdefault(int(rid), []).append(c)
    pending_by_root: dict[int, list[dict]] = {}
    for c in pending_comments:
        rid = c.get("in_reply_to_id")
        if rid is not None:
            pending_by_root.setdefault(int(rid), []).append(c)

    threads: list[dict] = []
    for c in submitted:
        if c.get("in_reply_to_id") is not None:
            continue  # not a root
        author = (c.get("user") or {}).get("login")
        if author == learner_login or _is_bot(c.get("user")):
            continue  # only mentor-authored roots
        root_id = int(c["id"])
        replies = submitted_by_root.get(root_id, [])
        drafts = pending_by_root.get(root_id, [])
        learner_replied = any((r.get("user") or {}).get("login") == learner_login for r in replies)
        has_pending_draft = bool(drafts)
        if learner_replied:
            status = "answered_submitted"
        elif has_pending_draft:
            status = "answered_pending"
        else:
            status = "unanswered"
        ov = overlay.get(root_id, {})
        timestamps = [_comment_ts(c)] + [_comment_ts(x) for x in replies + drafts]
        threads.append({
            "root_comment_id": root_id,
            "path": c.get("path"),
            "line": c.get("line"),
            "root_author": author,
            "root_excerpt": _excerpt(c.get("body")),
            "diff_hunk": c.get("diff_hunk"),
            "status": status,
            "has_pending_draft": has_pending_draft,
            "is_resolved": ov.get("is_resolved", False),
            "is_outdated": ov.get("is_outdated", False),
            "resolved_by": ov.get("resolved_by"),
            "last_activity_at": max([t for t in timestamps if t], default=None),
            "reply_count": len(replies),
        })

    threads.sort(key=lambda t: (_STATUS_ORDER.get(t["status"], 9),
                                t["last_activity_at"] or "", t["root_comment_id"]))

    counts = {
        "total": len(threads),
        "unanswered": sum(1 for t in threads if t["status"] == "unanswered"),
        "answered_pending": sum(1 for t in threads if t["status"] == "answered_pending"),
        "answered_submitted": sum(1 for t in threads if t["status"] == "answered_submitted"),
        "resolved": sum(1 for t in threads if t["is_resolved"]),
        "outdated": sum(1 for t in threads if t["is_outdated"]),
    }

    latest = max(
        (r for r in reviews if r.get("submitted_at")),
        key=lambda r: r.get("submitted_at") or "", default=None)

    snap_path = _snapshot_path(repo, pr_number, state_root)
    delta = _compute_delta(threads, _load_snapshot(snap_path))
    if write_snapshot:
        _write_snapshot(snap_path, pr_number, threads)

    return {
        "status": "ok",
        "pr_number": pr_number,
        "pr_state": detail.get("state"),
        "mergeable": detail.get("mergeable"),
        "review_decision": review_decision,
        "latest_review_state": (latest or {}).get("state"),
        "reviewer_decisions": _reviewer_decisions(reviews, learner_login),
        "pending_review_id": (pending_review or {}).get("id"),
        "round_timeline": _round_timeline(reviews, submitted, learner_login),
        "counts": counts,
        "delta": delta,
        "threads": threads,
    }
