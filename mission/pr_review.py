"""mission.pr_review — F2 family A+N (received-review surface, mode pr_review).

Repo-scoped, learner-scoped. Answers "받은 리뷰 정리해줘" / "아직 답 안 단 리뷰
있어?": it surfaces the mentor/peer review the learner RECEIVED on their own PR,
which review threads are still unresolved, the concept-linked review anchors,
and a PR-level issue-comment overview.

  A  anchors            — concept-linked review anchors on the learner's own
                          code (state/learner/review_anchors.json, F0-d), scoped
                          to this repo. The grounded "what mentor flagged" set.
  A  unresolved_threads — mentor root comments with no learner reply (reuses
                          core.pr_retro.build_retrospective.unresolved_threads).
  N  pr_overview        — PR-level issue comments the learner received (non-bot,
                          non-self) on their own PR. Schema drifts across repos:
                          member has a comment_role column, waiting does not, so
                          the query never relies on comment_role.

Pure read-only over state/repos/<repo>/archive/prs.sqlite3 +
state/learner/review_anchors.json. Artifact lands at
state/repos/<repo>/pr_review.json (mirrors diff_evolution's per-repo save).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

_ANCHOR_CHARS = 200
_OVERVIEW_CHARS = 200
_UNRESOLVED_CAP = 10


def _anchors_for_repo(repo: str, state_root: Path) -> list[dict]:
    """Review anchors filtered to this repo. Anchors are unresolved review
    points on the learner's own code, so no reply state is fabricated."""
    p = state_root / "learner" / "review_anchors.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[dict] = []
    for a in data.get("anchors", []):
        if a.get("repo") != repo:
            continue
        text = (a.get("comment_text") or "").strip().replace("\n", " ")
        if len(text) > _ANCHOR_CHARS:
            text = text[:_ANCHOR_CHARS].rstrip() + "…"
        out.append({
            "thread_id": a.get("thread_id"),
            "pr_number": a.get("pr_number"),
            "path": a.get("path"),
            "line": a.get("line"),
            "mentor_login": a.get("mentor_login"),
            "comment_excerpt": text,
            "concept_ids": (a.get("topic_concept_ids") or [])[:3],
        })
    return out


def _pr_overview(repo: str, learner_login: str, state_root: Path) -> list[dict]:
    """PR-level issue comments the learner received on their own PR(s).

    Schema drift: member has comment_role + html_url etc., waiting does not.
    Only user_login/body/created_at + pull_request_id are read so the query is
    column-stable across both. Bot + the learner's own comments are excluded."""
    db = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "pull_request_issue_comments_current" not in tables:
            return []
        try:
            rows = conn.execute(
                "SELECT c.pull_request_id AS pid, p.number AS number, "
                "c.user_login AS user_login, c.body AS body, c.created_at AS created_at "
                "FROM pull_request_issue_comments_current c "
                "JOIN pull_requests_current p ON p.id = c.pull_request_id "
                "WHERE p.author_login = ? AND c.user_login != ? "
                "AND c.user_login NOT LIKE '%[bot]' "
                "ORDER BY c.created_at",
                (learner_login, learner_login),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        body = (r["body"] or "").strip().replace("\n", " ")
        out.append({
            "pr_number": r["number"],
            "user_login": r["user_login"],
            "body_excerpt": body[:_OVERVIEW_CHARS],
            "created_at": r["created_at"],
        })
    return out


def analyze(repo: str, learner_login: str, state_root: Path = DEFAULT_STATE_ROOT,
            now: float | None = None) -> dict:
    """Build the pr_review artifact for one repo's received reviews. Read-only."""
    from core.pr_retro import build_retrospective

    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    retro = build_retrospective(repo, learner_login, state_root, now=now)
    if retro.source == "missing_archive":
        return {
            "repo": repo,
            "learner_login": learner_login,
            "status": "missing_archive",
            "generated_at": generated_at,
            "anchors": [],
            "unresolved_threads": [],
            "pr_overview": [],
            "counts": {"anchors": 0, "linked_anchors": 0,
                       "unresolved": 0, "overview": 0},
        }

    anchors = _anchors_for_repo(repo, state_root)
    linked = sum(1 for a in anchors if a["concept_ids"])

    unresolved = [
        {"pr_number": u.pr_number, "path": u.path, "line": u.line,
         "mentor_login": u.mentor_login, "body_excerpt": u.body_excerpt,
         "open_days": u.open_days}
        for u in retro.unresolved_threads[:_UNRESOLVED_CAP]
    ]

    overview = _pr_overview(repo, learner_login, state_root)

    return {
        "repo": repo,
        "learner_login": learner_login,
        "status": "ok",
        "generated_at": generated_at,
        "anchors": anchors,
        "unresolved_threads": unresolved,
        "pr_overview": overview,
        "counts": {
            "anchors": len(anchors),
            "linked_anchors": linked,
            "unresolved": len(unresolved),
            "overview": len(overview),
        },
    }


def save_pr_review(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "pr_review.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
