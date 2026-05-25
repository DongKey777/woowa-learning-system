"""SQLite archive DB for paradigm-v2 PR collection (Phase U).

Schema in scripts/collection/schema.sql. Reader-compatible with:
  - core/archive.py
  - anchors/extract.py / anchors/match.py
  - core/pr_retro.py
  - core/learner_state.py
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class ArchiveDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ArchiveDatabase":
        return self

    def __exit__(self, *exc) -> None:
        self.conn.commit()
        self.close()

    # ── schema ──────────────────────────────────────────────────────────

    def initialize_schema(self, schema_path: Path | None = None) -> None:
        sql = (schema_path or SCHEMA_PATH).read_text(encoding="utf-8")
        self.conn.executescript(sql)
        self.conn.commit()

    # ── upserts ─────────────────────────────────────────────────────────

    def upsert_repository(self, owner: str, name: str, full_name: str,
                           track: str | None = None,
                           mission_name: str | None = None) -> int:
        cur = self.conn.execute(
            "SELECT id FROM repositories WHERE full_name = ?", (full_name,)
        ).fetchone()
        if cur:
            self.conn.execute(
                "UPDATE repositories SET owner=?, name=?, track=?, mission_name=? "
                "WHERE id=?",
                (owner, name, track, mission_name, cur["id"]),
            )
            return int(cur["id"])
        cur = self.conn.execute(
            "INSERT INTO repositories (owner, name, full_name, track, mission_name, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (owner, name, full_name, track, mission_name, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def start_collection_run(self, repository_id: int, mode: str,
                               started_at: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO collection_runs (repository_id, mode, started_at, status) "
            "VALUES (?, ?, ?, 'in_progress')",
            (repository_id, mode, started_at or _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_collection_run(self, run_id: int, status: str,
                                prs_processed: int = 0, notes: str | None = None) -> None:
        self.conn.execute(
            "UPDATE collection_runs SET finished_at=?, status=?, prs_processed=?, "
            "notes=? WHERE id=?",
            (_now(), status, prs_processed, notes, run_id),
        )
        self.conn.commit()

    def record_failure(self, run_id: int, pr_number: int | None,
                        endpoint: str, error: str) -> None:
        self.conn.execute(
            "INSERT INTO collection_run_failures (collection_run_id, pr_number, "
            "endpoint, error_message, occurred_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, pr_number, endpoint, error[:500], _now()),
        )
        self.conn.commit()

    def upsert_pull_request(self, repo_id: int, run_id: int,
                              pr: dict[str, Any]) -> int:
        """Insert/update a PR from gh API payload."""
        gh_id = pr["id"]
        cur = self.conn.execute(
            "SELECT id FROM pull_requests_current WHERE github_pr_id = ?", (gh_id,)
        ).fetchone()
        fields = (
            repo_id, gh_id, pr["number"], pr.get("title", ""), pr.get("body"),
            pr.get("state", "open"), (pr.get("user") or {}).get("login", ""),
            (pr.get("base") or {}).get("ref"), (pr.get("head") or {}).get("ref"),
            (pr.get("head") or {}).get("sha"),
            int(bool(pr.get("mergeable"))) if pr.get("mergeable") is not None else None,
            pr.get("mergeable_state"),
            pr.get("changed_files"), pr.get("commits"),
            pr.get("additions"), pr.get("deletions"),
            pr.get("comments"), pr.get("review_comments"),
            pr.get("created_at"), pr.get("updated_at"),
            pr.get("closed_at"), pr.get("merged_at"),
            pr.get("html_url"), _now(), run_id,
        )
        if cur:
            self.conn.execute(
                "UPDATE pull_requests_current SET "
                "repository_id=?, github_pr_id=?, number=?, title=?, body=?, "
                "state=?, author_login=?, base_ref_name=?, head_ref_name=?, "
                "head_sha=?, mergeable=?, mergeable_state=?, changed_files=?, "
                "commits_count=?, additions=?, deletions=?, "
                "issue_comments_count=?, review_comments_count=?, "
                "created_at=?, updated_at=?, closed_at=?, merged_at=?, "
                "html_url=?, collected_at=?, collection_run_id=? WHERE id=?",
                (*fields, cur["id"]),
            )
            return int(cur["id"])
        cur2 = self.conn.execute(
            "INSERT INTO pull_requests_current ("
            "repository_id, github_pr_id, number, title, body, state, "
            "author_login, base_ref_name, head_ref_name, head_sha, "
            "mergeable, mergeable_state, changed_files, commits_count, "
            "additions, deletions, issue_comments_count, review_comments_count, "
            "created_at, updated_at, closed_at, merged_at, html_url, "
            "collected_at, collection_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            fields,
        )
        return int(cur2.lastrowid)

    def upsert_pr_file(self, pr_id: int, f: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO pull_request_files_current ("
            "pull_request_id, sha, path, status, additions, deletions, changes, "
            "patch_text, collected_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(pull_request_id, path) DO UPDATE SET "
            "sha=excluded.sha, status=excluded.status, additions=excluded.additions, "
            "deletions=excluded.deletions, changes=excluded.changes, "
            "patch_text=excluded.patch_text, collected_at=excluded.collected_at",
            (pr_id, f.get("sha"), f.get("filename"), f.get("status"),
             f.get("additions"), f.get("deletions"), f.get("changes"),
             f.get("patch"), _now()),
        )

    def upsert_pr_review(self, pr_id: int, r: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO pull_request_reviews_current ("
            "pull_request_id, github_review_id, user_login, state, body, "
            "submitted_at, commit_id, collected_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(github_review_id) DO UPDATE SET "
            "state=excluded.state, body=excluded.body, "
            "submitted_at=excluded.submitted_at, collected_at=excluded.collected_at",
            (pr_id, r.get("id"), (r.get("user") or {}).get("login"),
             r.get("state"), r.get("body"), r.get("submitted_at"),
             r.get("commit_id"), _now()),
        )

    def upsert_pr_review_comment(self, pr_id: int, c: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO pull_request_review_comments_current ("
            "pull_request_id, github_review_id, github_comment_id, user_login, "
            "path, position, original_position, line, original_line, start_line, "
            "side, start_side, commit_id, original_commit_id, "
            "in_reply_to_github_comment_id, diff_hunk, body, created_at, "
            "updated_at, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(github_comment_id) DO UPDATE SET "
            "body=excluded.body, updated_at=excluded.updated_at, "
            "collected_at=excluded.collected_at",
            (pr_id, c.get("pull_request_review_id"), c["id"],
             (c.get("user") or {}).get("login", ""),
             c.get("path"), c.get("position"), c.get("original_position"),
             c.get("line"), c.get("original_line"), c.get("start_line"),
             c.get("side"), c.get("start_side"), c.get("commit_id"),
             c.get("original_commit_id"), c.get("in_reply_to_id"),
             c.get("diff_hunk"), c.get("body", ""),
             c.get("created_at"), c.get("updated_at"), _now()),
        )

    def upsert_pr_issue_comment(self, pr_id: int, c: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO pull_request_issue_comments_current ("
            "pull_request_id, github_comment_id, user_login, body, "
            "created_at, updated_at, collected_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(github_comment_id) DO UPDATE SET "
            "body=excluded.body, updated_at=excluded.updated_at, "
            "collected_at=excluded.collected_at",
            (pr_id, c["id"], (c.get("user") or {}).get("login"),
             c.get("body"), c.get("created_at"), c.get("updated_at"), _now()),
        )

    # ── queries (used by status wrappers) ───────────────────────────────

    def count_prs(self, repo_id: int) -> dict:
        r = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN state='open' THEN 1 ELSE 0 END) AS open_n, "
            "SUM(CASE WHEN merged_at IS NOT NULL THEN 1 ELSE 0 END) AS merged_n "
            "FROM pull_requests_current WHERE repository_id = ?",
            (repo_id,),
        ).fetchone()
        return {"total": r["total"], "open": r["open_n"], "merged": r["merged_n"]}

    def latest_collection_run(self, repo_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM collection_runs WHERE repository_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        return dict(r) if r else None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
