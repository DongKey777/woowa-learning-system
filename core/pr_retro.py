"""Phase T1: PR retrospective — recurring mentor signals + unresolved threads
+ timeline from SQLite archive.

paradigm-v2 self-contained: reads state/repos/<repo>/archive/prs.sqlite3.
No GitHub API call by default — pure SQLite. (`gh_calls_used` always 0 here;
future GraphQL fallback can be wired into a separate function.)

Public API:
  build_retrospective(repo, learner_login, state_root, pr_limit, now) -> PRRetrospective
  save_retrospective(retro, state_root) -> Path
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
MAX_PRS = 30
SIGNAL_MIN_FREQ = 2  # signal must appear in ≥2 PRs to count as "recurring"
UNRESOLVED_CAP = 20

# Korean noun fragments (≥2 chars) + CamelCase English (≥3 chars)
_TOPIC_PATTERN = re.compile(r"[가-힣]{2,}|[A-Z][A-Za-z]{2,}")
_STOP_TOPICS = frozenset(s.lower() for s in {
    # Korean common nouns (low signal)
    "코드", "리뷰", "메서드", "클래스", "변수", "내용", "부분", "이슈", "사용", "기능",
    "확인", "수정", "추가", "제거", "변경", "구현", "필요", "처리", "동작", "테스트",
    # English low-signal words common in mentor/bot comments
    "the", "and", "for", "you", "PR", "PullRequest",
    "Potential", "Script", "FILE", "Line", "Major", "Minor", "Refactor",
    "Note", "Tip", "Issue", "Comment", "Fix", "Suggestion", "Add", "Remove",
    "Change", "Update", "Improve", "Consider", "Cause", "Performance",
    "Style", "Resolved", "Open", "Closed",
})


@dataclass(frozen=True)
class PRTimelineEntry:
    pr_number: int
    title: str
    created_at: str | None
    merged_at: str | None
    review_rounds: int
    mentor_topics: list[str]


@dataclass(frozen=True)
class RecurringSignal:
    topic: str
    pr_count: int          # number of distinct PRs the topic appeared in
    comment_count: int     # total mentor comments touching the topic
    sample_prs: list[int]


@dataclass(frozen=True)
class UnresolvedThread:
    pr_number: int
    path: str | None
    line: int | None
    mentor_login: str
    body_excerpt: str
    open_days: int | None


@dataclass
class PRRetrospective:
    repo: str
    learner_login: str
    computed_at: float
    prs_total: int
    prs_merged: int
    avg_review_rounds: float
    timeline: list[PRTimelineEntry] = field(default_factory=list)
    recurring_mentor_signals: list[RecurringSignal] = field(default_factory=list)
    unresolved_threads: list[UnresolvedThread] = field(default_factory=list)
    gh_calls_used: int = 0
    source: str = "sqlite"


def _topics_from_body(body: str, max_n: int = 4) -> list[str]:
    if not body:
        return []
    toks = _TOPIC_PATTERN.findall(body)
    toks = [t for t in toks if t.lower() not in _STOP_TOPICS and len(t) >= 3]
    return [t for t, _ in Counter(toks).most_common(max_n)]


def _open_days(created_iso: str | None, now_ts: float | None) -> int | None:
    if not (created_iso and now_ts):
        return None
    try:
        opened = dt.datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        now_dt = dt.datetime.fromtimestamp(now_ts, dt.timezone.utc)
        return max(0, int((now_dt - opened).total_seconds() / 86400))
    except Exception:
        return None


def build_retrospective(
    repo: str,
    learner_login: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    pr_limit: int = MAX_PRS,
    now: float | None = None,
    include_bot: bool = False,
) -> PRRetrospective:
    """Compute retrospective from local SQLite archive. Pure read-only."""
    db_path = state_root / "repos" / repo / "archive" / "prs.sqlite3"
    now = now or time.time()
    if not db_path.exists():
        return PRRetrospective(
            repo=repo, learner_login=learner_login, computed_at=now,
            prs_total=0, prs_merged=0, avg_review_rounds=0.0,
            source="missing_archive",
        )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        prs = conn.execute(
            "SELECT id, number, title, created_at, merged_at, state "
            "FROM pull_requests_current "
            "WHERE author_login = ? ORDER BY number DESC LIMIT ?",
            (learner_login, pr_limit),
        ).fetchall()

        timeline: list[PRTimelineEntry] = []
        topic_to_prs: dict[str, list[int]] = defaultdict(list)
        topic_to_comments: Counter = Counter()
        unresolved: list[UnresolvedThread] = []
        merged_n = 0
        rounds_sum = 0

        for pr in prs:
            if pr["merged_at"]:
                merged_n += 1
            # mentor root comments (no learner reply)
            bot_clause = "" if include_bot else "AND user_login NOT LIKE '%[bot]' "
            mentor_rows = conn.execute(
                "SELECT github_comment_id, user_login, body, path, line, created_at "
                "FROM pull_request_review_comments_current "
                "WHERE pull_request_id = ? AND user_login != ? "
                f"{bot_clause}"
                "AND in_reply_to_github_comment_id IS NULL "
                "ORDER BY created_at",
                (pr["id"], learner_login),
            ).fetchall()
            # learner replies per root comment
            reply_ids = {r["in_reply_to_github_comment_id"] for r in conn.execute(
                "SELECT in_reply_to_github_comment_id FROM "
                "pull_request_review_comments_current "
                "WHERE pull_request_id = ? AND user_login = ?",
                (pr["id"], learner_login),
            ).fetchall() if r["in_reply_to_github_comment_id"]}

            review_rounds = len({m["created_at"][:10] for m in mentor_rows if m["created_at"]})
            rounds_sum += review_rounds

            pr_topics: list[str] = []
            for m in mentor_rows:
                tops = _topics_from_body(m["body"] or "")
                pr_topics.extend(tops)
                for t in set(tops):  # each topic counted once per comment
                    topic_to_comments[t] += 1
                    if pr["number"] not in topic_to_prs[t]:
                        topic_to_prs[t].append(pr["number"])
                # unresolved heuristic: mentor root comment lacks learner reply
                if m["github_comment_id"] not in reply_ids:
                    unresolved.append(UnresolvedThread(
                        pr_number=pr["number"],
                        path=m["path"],
                        line=m["line"],
                        mentor_login=m["user_login"],
                        body_excerpt=(m["body"] or "")[:200],
                        open_days=_open_days(m["created_at"], now),
                    ))

            timeline.append(PRTimelineEntry(
                pr_number=pr["number"],
                title=pr["title"],
                created_at=pr["created_at"],
                merged_at=pr["merged_at"],
                review_rounds=review_rounds,
                mentor_topics=list(dict.fromkeys(pr_topics))[:5],
            ))

        # Recurring: topic mentioned in ≥2 PRs (legacy semantics) OR ≥2
        # mentor comments within a single PR (useful for early-stage learner
        # with only 1-2 own PRs).
        recurring = [
            RecurringSignal(
                topic=t,
                pr_count=len(topic_to_prs[t]),
                comment_count=topic_to_comments[t],
                sample_prs=topic_to_prs[t][:3],
            )
            for t in sorted(topic_to_prs, key=lambda k: -topic_to_comments[k])
            if len(topic_to_prs[t]) >= SIGNAL_MIN_FREQ
               or topic_to_comments[t] >= SIGNAL_MIN_FREQ
        ][:10]

        return PRRetrospective(
            repo=repo, learner_login=learner_login, computed_at=now,
            prs_total=len(prs), prs_merged=merged_n,
            avg_review_rounds=round(rounds_sum / max(1, len(prs)), 2),
            timeline=timeline,
            recurring_mentor_signals=recurring,
            unresolved_threads=unresolved[:UNRESOLVED_CAP],
            source="sqlite",
        )
    finally:
        conn.close()


def save_retrospective(retro: PRRetrospective,
                       state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out_dir = state_root / "learner" / "pr_retrospective"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{retro.repo}.json"
    payload = {
        "repo": retro.repo,
        "learner_login": retro.learner_login,
        "computed_at": retro.computed_at,
        "prs_total": retro.prs_total,
        "prs_merged": retro.prs_merged,
        "avg_review_rounds": retro.avg_review_rounds,
        "gh_calls_used": retro.gh_calls_used,
        "source": retro.source,
        "timeline": [asdict(t) for t in retro.timeline],
        "recurring_mentor_signals": [asdict(s) for s in retro.recurring_mentor_signals],
        "unresolved_threads": [asdict(u) for u in retro.unresolved_threads],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
