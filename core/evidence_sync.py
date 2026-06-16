"""Production evidence emission from the PR archive → Bloom mastery (F0-e).

Closes the "starved mastery loop": ingest_pr_merge / ingest_mentor_accept have
always existed but were only ever called from tests, so every concept stayed
stuck at `attempted`. This reads the per-repo SQLite archive + mission_patterns
(the learner's own code→concept links) + review_anchors and emits:

  - pr_merge      for every distinct mission_patterns concept of a *merged* PR
  - mentor_accept for those concepts when a mentor *APPROVED* the PR
  - mentor_accept for an anchor's topic_concept_ids when its thread is *resolved*
                  (learner replied to the mentor's root comment) — uses F0-d links

A merged + mentor-approved PR therefore lands its coded concepts at `proficient`
(pr_merge + mentor_accept), exactly the Bloom definition. Idempotent via
dedup_key; every record carries `repo` for L (cross-mission) attribution.

Reviews-table schema drifts across collection runs (`reviewer_login` vs
`user_login`, with/without `reviewer_role`) — login column is resolved at
runtime so older archives still work.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from anchors.extract import load_anchors
from core.feedback import ingest_mentor_accept, ingest_pr_merge
from core.mastery import (
    BLOOM_LEVELS,
    DEFAULT_STATE_ROOT,
    compact_all,
    list_by_level,
    summary,
)


def _level_map(state_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for lvl in BLOOM_LEVELS:
        for cid in list_by_level(lvl, state_root=state_root):
            out[cid] = lvl
    return out


def _count_promotions(before: dict[str, str], after: dict[str, str]) -> int:
    """Concepts whose Bloom level rose (idempotent: re-run yields 0)."""
    rank = {lvl: i for i, lvl in enumerate(BLOOM_LEVELS)}
    promoted = 0
    for cid, lvl in after.items():
        if rank[lvl] > rank.get(before.get(cid, "attempted"), 0):
            promoted += 1
    return promoted


def _archive_db(state_root: Path, repo: str) -> Path:
    return state_root / "repos" / repo / "archive" / "prs.sqlite3"


def _distinct_mission_concepts(state_root: Path, repo: str) -> list[str]:
    """Distinct matched_concept_id from the learner's own mission_patterns."""
    p = state_root / "repos" / repo / "mission_patterns.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[str] = []
    for pat in data.get("patterns", []):
        cid = pat.get("matched_concept_id")
        if cid and cid not in out:
            out.append(cid)
    return out


def _review_login_col(conn: sqlite3.Connection) -> str | None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pull_request_reviews_current)")}
    if "reviewer_login" in cols:
        return "reviewer_login"
    if "user_login" in cols:
        return "user_login"
    return None


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _anchor_comment_id(thread_id: str) -> int | None:
    """thread_id == 'repo:pr:comment_id' (repo has no colon)."""
    tail = thread_id.rsplit(":", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None


def sync_repo(
    repo: str,
    learner_login: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    now: float | None = None,
) -> dict:
    """Emit pr_merge / mentor_accept evidence for one repo, then recompute Bloom."""
    db = _archive_db(state_root, repo)
    before = summary(state_root=state_root)["counts"]
    if not db.exists():
        return {"repo": repo, "status": "missing_archive",
                "pr_merge_emitted": 0, "mentor_accept_emitted": 0,
                "promotions": 0, "before": before, "after": before}

    before_levels = _level_map(state_root)
    concepts = _distinct_mission_concepts(state_root, repo)
    anchors = [a for a in load_anchors(state_root=state_root) if a.repo == repo]

    pr_merge_emitted = 0
    mentor_accept_emitted = 0

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        login_col = _review_login_col(conn)
        prs = conn.execute(
            "SELECT id, number, merged_at FROM pull_requests_current "
            "WHERE author_login=? ORDER BY number",
            (learner_login,),
        ).fetchall()

        for pr in prs:
            pr_id, number, merged_at = pr["id"], pr["number"], pr["merged_at"]

            # 1. pr_merge — merged PR's coded concepts (highest-weight signal).
            if merged_at and concepts:
                ingest_pr_merge(
                    concepts, state_root=state_root, ts=_parse_iso(merged_at),
                    payload={"repo": repo, "pr_number": number, "signal": "pr_merge"},
                    dedup_prefix=f"pr_merge:{repo}:{number}",
                )
                pr_merge_emitted += len(concepts)

            # 2. mentor_accept — a mentor APPROVED the PR (blanket acceptance).
            if login_col and concepts:
                approved = conn.execute(
                    f"SELECT {login_col} AS login, submitted_at FROM "
                    "pull_request_reviews_current WHERE pull_request_id=? AND state='APPROVED'",
                    (pr_id,),
                ).fetchall()
                mentor_rows = [r for r in approved if r["login"] and r["login"] != learner_login]
                if mentor_rows:
                    ingest_mentor_accept(
                        concepts, state_root=state_root,
                        ts=_parse_iso(mentor_rows[0]["submitted_at"]),
                        payload={"repo": repo, "pr_number": number,
                                 "signal": "review_approved", "reviewer": mentor_rows[0]["login"]},
                        dedup_prefix=f"mentor_accept:approved:{repo}:{number}",
                    )
                    mentor_accept_emitted += len(concepts)

            # 3. mentor_accept — resolved threads (learner replied to mentor root).
            reply_ids = {
                r["in_reply_to_github_comment_id"] for r in conn.execute(
                    "SELECT in_reply_to_github_comment_id FROM "
                    "pull_request_review_comments_current WHERE pull_request_id=? AND user_login=?",
                    (pr_id, learner_login),
                ).fetchall() if r["in_reply_to_github_comment_id"]
            }
            for a in anchors:
                if a.pr_number != number or not a.topic_concept_ids:
                    continue
                cid = _anchor_comment_id(a.thread_id)
                if cid is not None and cid in reply_ids:
                    ingest_mentor_accept(
                        a.topic_concept_ids, state_root=state_root,
                        # W6: use the PR's real merged_at (deterministic, consistent
                        # with pr_merge/review_approved) instead of ts=now (None ->
                        # wall-clock, non-deterministic across runs).
                        ts=_parse_iso(merged_at),
                        payload={"repo": repo, "pr_number": number,
                                 "signal": "thread_resolved", "thread_id": a.thread_id},
                        dedup_prefix=f"mentor_accept:thread:{repo}:{cid}",
                    )
                    mentor_accept_emitted += len(a.topic_concept_ids)
    finally:
        conn.close()

    compact_all(state_root=state_root, now=now)
    after = summary(state_root=state_root)["counts"]
    promotions = _count_promotions(before_levels, _level_map(state_root))
    return {
        "repo": repo,
        "status": "ok",
        "concepts_considered": len(concepts),
        "pr_merge_emitted": pr_merge_emitted,
        "mentor_accept_emitted": mentor_accept_emitted,
        "promotions": promotions,
        "before": before,
        "after": after,
    }
