"""PR collection orchestrator (Phase U).

paradigm-v2 self-contained PR archive ingest: gh CLI → SQLite.

Public API:
  collect(owner, repo, db_path, mode='full', since=None, limit=None,
          max_calls=500) -> CollectReport
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.collection.db import ArchiveDatabase
from scripts.collection.github_client import GitHubCLIClient, GitHubCLIError


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
    failures: list[dict] = field(default_factory=list)
    finished_status: str = "succeeded"


def _filter_prs(prs: list[dict], since: str | None, limit: int | None,
                  title_contains: str | None = None) -> list[dict]:
    if since:
        since_dt = dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
        prs = [p for p in prs
                if (p.get("updated_at") or "")
                and dt.datetime.fromisoformat(p["updated_at"].replace("Z", "+00:00")) > since_dt]
    if title_contains:
        prs = [p for p in prs if title_contains.lower() in (p.get("title") or "").lower()]
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
    max_calls: int = 500,
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

        prs_filtered = _filter_prs(prs, since=since, limit=limit,
                                    title_contains=title_contains)
        report.prs_seen = len(prs)

        # 3. per-PR detail + files + reviews + comments
        for pr in prs_filtered:
            number = pr["number"]
            try:
                detail = client.fetch_pull_request_detail(number)
                pr_id = db.upsert_pull_request(repo_id, run_id, detail)
            except GitHubCLIError as e:
                db.record_failure(run_id, number, "pull_detail", str(e))
                report.failures.append({"pr": number, "endpoint": "pull_detail",
                                         "error": str(e)})
                report.prs_skipped += 1
                continue

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
            report.prs_processed += 1
            db.conn.commit()  # commit per PR for resumability

        # 4. finish
        status = "succeeded" if not report.failures else "partial"
        db.finish_collection_run(run_id, status, report.prs_processed)
        report.finished_status = status
    finally:
        report.gh_calls_used = client.calls_used
        db.close()

    return report
