"""Extract learner's own review thread anchors (F11 input).

Anchor = single mentor review_comment on a learner-authored PR at a specific
(path, line) with surrounding code hunk. Used by anchors/match.py to find
cross-crew/cross-reviewer patterns of similar code.

Capped at MAX_ANCHORS (200) total — covers learner's auth/member 31 own
review_threads (measured) with 169 future slack.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
MAX_ANCHORS = 200


@dataclass(frozen=True)
class ReviewAnchor:
    thread_id: str          # repo:pr:<root_comment_id>
    repo: str
    pr_number: int
    path: str
    line: int | None
    code_hunk: str          # diff_hunk text (≤10 lines)
    mentor_login: str
    comment_text: str
    topic_concept_ids: list[str]   # empty in v1 (heuristic in match.py)


@contextmanager
def _open(db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(f"no archive at {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def extract_from_archive(
    repo: str,
    learner_login: str,
    legacy_hub: Path,
    limit: int = MAX_ANCHORS,
) -> list[ReviewAnchor]:
    """Pull root mentor comments on learner-authored PRs."""
    db = legacy_hub / "state" / "repos" / repo / "archive" / "prs.sqlite3"
    with _open(db) as conn:
        rows = conn.execute(
            """
            SELECT rc.github_comment_id AS comment_id, rc.path, rc.line, rc.diff_hunk,
                   rc.user_login AS mentor_login, rc.body, pr.number AS pr_number
            FROM pull_request_review_comments_current rc
            JOIN pull_requests_current pr ON pr.id = rc.pull_request_id
            WHERE pr.author_login = ?
              AND rc.in_reply_to_github_comment_id IS NULL
              AND rc.user_login != ?
            ORDER BY rc.created_at DESC
            LIMIT ?
            """,
            (learner_login, learner_login, limit),
        ).fetchall()
    anchors: list[ReviewAnchor] = []
    for r in rows:
        anchors.append(ReviewAnchor(
            thread_id=f"{repo}:{r['pr_number']}:{r['comment_id']}",
            repo=repo,
            pr_number=r["pr_number"],
            path=r["path"] or "",
            line=r["line"],
            code_hunk=(r["diff_hunk"] or "")[:1500],   # cap to keep prompt budget
            mentor_login=r["mentor_login"] or "",
            comment_text=(r["body"] or "")[:1000],
            topic_concept_ids=[],
        ))
    return anchors


def save_anchors(
    anchors: list[ReviewAnchor],
    state_root: Path = DEFAULT_STATE_ROOT,
) -> Path:
    out = state_root / "learner" / "review_anchors.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "anchor_count": len(anchors),
        "anchors": [asdict(a) for a in anchors],
    }
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_anchors(state_root: Path = DEFAULT_STATE_ROOT) -> list[ReviewAnchor]:
    p = state_root / "learner" / "review_anchors.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [ReviewAnchor(**a) for a in data.get("anchors", [])]
