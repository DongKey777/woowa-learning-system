-- paradigm-v2 PR archive schema (Phase U).
-- Compatible with core/archive.py + anchors/extract.py + core/pr_retro.py readers.
-- Drops legacy FTS5 + audit *_history tables (paradigm-v2 doesn't use them).

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL UNIQUE,
    track TEXT,
    mission_name TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    mode TEXT NOT NULL,        -- full | incremental
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,                -- in_progress | succeeded | failed
    prs_processed INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS collection_run_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_run_id INTEGER NOT NULL,
    pr_number INTEGER,
    endpoint TEXT,
    error_message TEXT,
    occurred_at TEXT
);

CREATE TABLE IF NOT EXISTS pull_requests_current (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    github_pr_id INTEGER NOT NULL UNIQUE,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    state TEXT NOT NULL,        -- open | closed
    author_login TEXT NOT NULL,
    base_ref_name TEXT,
    head_ref_name TEXT,
    head_sha TEXT,
    mergeable INTEGER,
    mergeable_state TEXT,
    changed_files INTEGER,
    commits_count INTEGER,
    additions INTEGER,
    deletions INTEGER,
    issue_comments_count INTEGER,
    review_comments_count INTEGER,
    created_at TEXT,
    updated_at TEXT,
    closed_at TEXT,
    merged_at TEXT,
    html_url TEXT,
    collected_at TEXT,
    collection_run_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_prs_author ON pull_requests_current(author_login);
CREATE INDEX IF NOT EXISTS idx_prs_number ON pull_requests_current(repository_id, number);

CREATE TABLE IF NOT EXISTS pull_request_files_current (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pull_request_id INTEGER NOT NULL,
    sha TEXT,
    path TEXT,
    status TEXT,                -- added | modified | removed | renamed
    additions INTEGER,
    deletions INTEGER,
    changes INTEGER,
    patch_text TEXT,
    collected_at TEXT,
    UNIQUE(pull_request_id, path)
);
CREATE INDEX IF NOT EXISTS idx_files_pr ON pull_request_files_current(pull_request_id);

CREATE TABLE IF NOT EXISTS pull_request_reviews_current (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pull_request_id INTEGER NOT NULL,
    github_review_id INTEGER UNIQUE,
    user_login TEXT,
    state TEXT,                 -- APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED
    body TEXT,
    submitted_at TEXT,
    commit_id TEXT,
    collected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_pr ON pull_request_reviews_current(pull_request_id);

CREATE TABLE IF NOT EXISTS pull_request_review_comments_current (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pull_request_id INTEGER NOT NULL,
    github_review_id INTEGER,
    github_comment_id INTEGER NOT NULL UNIQUE,
    user_login TEXT NOT NULL,
    path TEXT,
    position INTEGER,
    original_position INTEGER,
    line INTEGER,
    original_line INTEGER,
    start_line INTEGER,
    side TEXT,
    start_side TEXT,
    commit_id TEXT,
    original_commit_id TEXT,
    in_reply_to_github_comment_id INTEGER,
    diff_hunk TEXT,
    body TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    collected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rc_pr ON pull_request_review_comments_current(pull_request_id);
CREATE INDEX IF NOT EXISTS idx_rc_reply ON pull_request_review_comments_current(in_reply_to_github_comment_id);

CREATE TABLE IF NOT EXISTS pull_request_issue_comments_current (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pull_request_id INTEGER NOT NULL,
    github_comment_id INTEGER NOT NULL UNIQUE,
    user_login TEXT,
    body TEXT,
    created_at TEXT,
    updated_at TEXT,
    collected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ic_pr ON pull_request_issue_comments_current(pull_request_id);
