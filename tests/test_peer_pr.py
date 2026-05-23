"""Tests for core.peer_pr — nickname extraction + compare()."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.peer_pr import (
    PeerPRComparison,
    build_nickname_map,
    compare,
    extract_nickname,
    resolve_nickname,
)


def _seed_archive(tmp_path: Path, repo: str = "roomescape") -> Path:
    arch_dir = tmp_path / "repos" / repo / "archive"
    arch_dir.mkdir(parents=True)
    db = arch_dir / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE pull_requests (
            number INTEGER PRIMARY KEY, title TEXT, author_login TEXT,
            additions INTEGER, deletions INTEGER
        );
        CREATE TABLE pull_request_files_current (
            pr_number INTEGER, path TEXT, additions INTEGER, deletions INTEGER, patch_text TEXT
        );
        CREATE TABLE review_comments (
            id INTEGER PRIMARY KEY, pr_number INTEGER, body TEXT, path TEXT, line INTEGER,
            in_reply_to_id INTEGER, author_login TEXT
        );
        INSERT INTO pull_requests VALUES (37, '[1단계 - 콘솔] 동키', 'donkey', 120, 30);
        INSERT INTO pull_requests VALUES (38, '[2단계 - DB] 동키', 'donkey', 80, 10);
        INSERT INTO pull_requests VALUES (40, '[1단계 - 콘솔] 큐빈', 'cubinkim', 150, 40);
        INSERT INTO pull_requests VALUES (41, '[2단계] 라키', 'rocky', 200, 50);
        INSERT INTO pull_request_files_current VALUES
            (37, 'src/main/X.java', 80, 10, 'X content'),
            (37, 'src/main/Y.java', 40, 20, 'Y content'),
            (40, 'src/main/X.java', 90, 5, 'cubin X'),
            (40, 'src/main/Z.java', 60, 0, 'cubin Z');
        INSERT INTO review_comments VALUES
            (1, 37, 'use builder', 'X.java', 10, NULL, 'mentor'),
            (2, 37, 'thanks', 'X.java', 10, 1, 'donkey'),
            (3, 40, 'split this', 'Z.java', 5, NULL, 'mentor');
        """
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_extract_nickname_step_console() -> None:
    assert extract_nickname("[1단계 - 콘솔] 동키") == "동키"
    assert extract_nickname("[2단계 - DB] 큐빈") == "큐빈"


def test_extract_nickname_no_match_returns_none() -> None:
    assert extract_nickname("Update README") is None
    assert extract_nickname("") is None
    assert extract_nickname(None) is None  # type: ignore[arg-type]


def test_build_nickname_map_excludes_ambiguous(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    mp = build_nickname_map("roomescape", state_root=root)
    assert mp == {"동키": "donkey", "큐빈": "cubinkim", "라키": "rocky"}


def test_resolve_nickname_returns_login(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    assert resolve_nickname("동키", "roomescape", state_root=root) == "donkey"
    assert resolve_nickname("ghost", "roomescape", state_root=root) is None


def test_compare_returns_path_overlap(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    cmp = compare("roomescape", learner_pr_number=37, peer_pr_numbers=[40], state_root=root)
    assert isinstance(cmp, PeerPRComparison)
    assert cmp.learner_pr["nickname"] == "동키"
    assert cmp.peer_prs[0]["nickname"] == "큐빈"
    assert cmp.common_paths == ["src/main/X.java"]
    assert cmp.learner_only_paths == ["src/main/Y.java"]
    assert cmp.peer_only_paths == ["src/main/Z.java"]


def test_compare_attaches_threads(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    cmp = compare("roomescape", 37, [40], state_root=root)
    assert 37 in cmp.representative_threads
    assert 40 in cmp.representative_threads
    learner_threads = cmp.representative_threads[37]
    assert len(learner_threads) == 1
    assert learner_threads[0]["replies"][0]["body"] == "thanks"


def test_compare_enforces_max_peers(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    cmp = compare("roomescape", 37, [40, 41], max_peers=1, state_root=root)
    assert len(cmp.peer_prs) == 1
    assert cmp.peer_prs[0]["number"] == 40


def test_compare_rejects_missing_learner_pr(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    with pytest.raises(ValueError, match="learner PR"):
        compare("roomescape", 999, [40], state_root=root)


def test_compare_skips_unknown_peer_pr_silently(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    cmp = compare("roomescape", 37, [40, 9999], state_root=root)
    assert len(cmp.peer_prs) == 1
    assert cmp.peer_prs[0]["number"] == 40
