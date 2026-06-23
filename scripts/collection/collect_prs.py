"""PR collection orchestrator (Phase U).

paradigm-v2 self-contained PR archive ingest: gh CLI → SQLite.

Public API:
  collect(owner, repo, db_path, mode='full', since=None, limit=None,
          max_calls=None) -> CollectReport

When max_calls is None the gh-call budget is derived from the live
authenticated REST limit (`gh api rate_limit`, 5000/hr) minus a safety
margin, instead of an arbitrary constant.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.collection.db import ArchiveDatabase
from scripts.collection.github_client import GitHubCLIClient, GitHubCLIError

# Leave headroom below the live authenticated REST budget for other gh usage
# in the same hour (interactive gh, downstream tooling).
RATE_LIMIT_MARGIN = 500
# Used only when the live rate_limit probe is unavailable.
FALLBACK_MAX_CALLS = 4500


@dataclass
class CollectReport:
    owner: str
    repo: str
    mode: str
    prs_seen: int = 0
    prs_processed: int = 0
    prs_skipped: int = 0
    files_upserted: int = 0
    reviews_upserted: int = 0
    review_comments_upserted: int = 0
    issue_comments_upserted: int = 0
    gh_calls_used: int = 0
    gh_call_budget: int | None = None
    failures: list[dict] = field(default_factory=list)
    finished_status: str = "succeeded"


def _resolve_budget(client: GitHubCLIClient, max_calls: int | None) -> int:
    """Effective gh-call cap from the live authenticated REST budget.

    budget = remaining - margin; with an explicit max_calls we take the
    smaller of the two. Falls back to a constant if the probe is unavailable.
    """
    try:
        rl = client.fetch_rate_limit()
        remaining = rl["resources"]["core"]["remaining"]
        budget = max(remaining - RATE_LIMIT_MARGIN, 1)
    except (GitHubCLIError, KeyError, TypeError, ValueError):
        return max_calls if max_calls is not None else FALLBACK_MAX_CALLS
    return min(max_calls, budget) if max_calls is not None else budget


def _filter_prs(prs: list[dict], since: str | None, limit: int | None,
                  title_contains: str | None = None) -> list[dict]:
    if since:
        since_dt = dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
        prs = [p for p in prs
                if (p.get("updated_at") or "")
                and dt.datetime.fromisoformat(p["updated_at"].replace("Z", "+00:00")) > since_dt]
    if title_contains:
        prs = [p for p in prs if title_contains.lower() in (p.get("title") or "").lower()]
    # Oldest-updated first, BEFORE limit, so a capped/limited run collects a
    # contiguous updated_at prefix and advances the cursor without leaving gaps.
    prs = sorted(prs, key=lambda p: p.get("updated_at") or "")
    if limit is not None:
        prs = prs[:limit]
    return prs


def collect(
    owner: str,
    repo: str,
    db_path: Path,
    mode: str = "full",
    since: str | None = None,
    limit: int | None = None,
    title_contains: str | None = None,
    max_calls: int | None = None,
    track: str | None = None,
    mission_name: str | None = None,
) -> CollectReport:
    report = CollectReport(owner=owner, repo=repo, mode=mode)
    client = GitHubCLIClient(owner, repo, max_calls=max_calls)

    db = ArchiveDatabase(db_path)
    db.initialize_schema()
    try:
        # 0. auth check
        client.fetch_authenticated_user()

        # 0b. resolve gh-call budget from the live authenticated REST limit
        client.max_calls = _resolve_budget(client, max_calls)
        report.gh_call_budget = client.max_calls

        # 1. upsert repository row
        repo_id = db.upsert_repository(
            owner=owner, name=repo, full_name=f"{owner}/{repo}",
            track=track, mission_name=mission_name,
        )
        run_id = db.start_collection_run(repo_id, mode)

        # 2. fetch PR list (single paginated call)
        try:
            prs = client.fetch_pull_requests(state="all")
        except GitHubCLIError as e:
            db.record_failure(run_id, None, "pulls", str(e))
            db.finish_collection_run(run_id, "failed", 0, str(e)[:200])
            report.finished_status = "failed"
            report.failures.append({"endpoint": "pulls", "error": str(e)})
            report.gh_calls_used = client.calls_used
            return report

        # _filter_prs returns PRs oldest-updated first so a capped/partial run
        # advances the cursor monotonically and the next run resumes from there.
        prs_filtered = _filter_prs(prs, since=since, limit=limit,
                                    title_contains=title_contains)
        report.prs_seen = len(prs)

        # Cursor for the next incremental run: the updated_at of the last PR in
        # the contiguous fully-collected prefix. Frozen once any PR is skipped
        # or partially collected, so nothing past a gap is marked as done.
        cursor_ts = since
        clean_prefix = True

        # 3. per-PR detail + files + reviews + comments
        for idx, pr in enumerate(prs_filtered):
            # Stop before the gh-call budget is exhausted — otherwise every
            # remaining PR's fetches fail and flood the DB with redundant failure
            # rows + per-PR commits. Record ONE budget marker (keeps the run
            # 'partial', not the per-PR flood) and leave the cursor un-advanced.
            if client.max_calls is not None and client.calls_used >= client.max_calls:
                remaining = len(prs_filtered) - idx
                report.failures.append({
                    "endpoint": "gh_budget",
                    "error": f"gh call cap exhausted ({client.calls_used}/{client.max_calls}); "
                             f"{remaining} PR(s) skipped"})
                report.prs_skipped += remaining
                clean_prefix = False
                break
            number = pr["number"]
            try:
                detail = client.fetch_pull_request_detail(number)
                pr_id = db.upsert_pull_request(repo_id, run_id, detail)
            except GitHubCLIError as e:
                db.record_failure(run_id, number, "pull_detail", str(e))
                report.failures.append({"pr": number, "endpoint": "pull_detail",
                                         "error": str(e)})
                report.prs_skipped += 1
                clean_prefix = False
                continue

            pr_clean = True
            for endpoint, fetcher, upserter, counter in [
                ("files", client.fetch_pull_request_files,
                 db.upsert_pr_file, "files_upserted"),
                ("reviews", client.fetch_pull_request_reviews,
                 db.upsert_pr_review, "reviews_upserted"),
                ("review_comments", client.fetch_pull_request_review_comments,
                 db.upsert_pr_review_comment, "review_comments_upserted"),
                ("issue_comments", client.fetch_pull_request_issue_comments,
                 db.upsert_pr_issue_comment, "issue_comments_upserted"),
            ]:
                try:
                    items = fetcher(number)
                    for it in items:
                        upserter(pr_id, it)
                    setattr(report, counter, getattr(report, counter) + len(items))
                except GitHubCLIError as e:
                    db.record_failure(run_id, number, endpoint, str(e))
                    report.failures.append({"pr": number, "endpoint": endpoint,
                                             "error": str(e)})
                    pr_clean = False
            report.prs_processed += 1
            if pr_clean and clean_prefix:
                cursor_ts = pr.get("updated_at") or cursor_ts
            else:
                clean_prefix = False
            db.conn.commit()  # commit per PR for resumability

        # 4. finish
        status = "succeeded" if not report.failures else "partial"
        db.finish_collection_run(run_id, status, report.prs_processed,
                                  finished_at=cursor_ts)
        report.finished_status = status
    finally:
        report.gh_calls_used = client.calls_used
        db.close()

    return report
