"""Tests for mission.peer_pr — Jaccard peer selection + artifact shape."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mission.peer_pr import analyze_repo, save_peer_pr


def _seed(tmp_path: Path, repo: str = "roomescape") -> Path:
    arch = tmp_path / "repos" / repo / "archive"
    arch.mkdir(parents=True)
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    conn.executescript(
        """
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            title TEXT, additions INTEGER, deletions INTEGER
        );
        CREATE TABLE pull_request_files_current (
            pull_request_id INTEGER, path TEXT,
            additions INTEGER, deletions INTEGER, patch_text TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
            github_comment_id INTEGER PRIMARY KEY, pull_request_id INTEGER, body TEXT,
            path TEXT, line INTEGER, in_reply_to_github_comment_id INTEGER, user_login TEXT
        );
        -- learner DongKey777: target = most-recent #50 (files A,B,C); older #40 (A)
        INSERT INTO pull_requests_current VALUES (10, 50, 'DongKey777', '[2단계] 동키(이동훈) 미션 제출합니다.', 90, 10);
        INSERT INTO pull_requests_current VALUES (9, 40, 'DongKey777', '[1단계] 동키(이동훈) 미션', 30, 5);
        -- 구름: focused overlap {A,B,Z} → Jaccard vs {A,B,C} = 2/4 = 0.5 (BEST)
        INSERT INTO pull_requests_current VALUES (8, 30, 'alstn113', '[2단계] 구름(김민수) 미션 제출합니다.', 70, 8);
        -- 릴리: touched everything {A,B,C,D,E,F,G} → Jaccard = 3/7 ≈ 0.43 (lower)
        INSERT INTO pull_requests_current VALUES (7, 20, 'lily', '[2단계] 릴리(최윤서) 미션 제출합니다.', 300, 40);
        -- bot: excluded
        INSERT INTO pull_requests_current VALUES (6, 10, 'octo[bot]', '[2단계] 봇 미션', 5, 0);
        INSERT INTO pull_request_files_current VALUES
            (10, 'src/main/A.java', 1,1,'a'),
            (10, 'src/main/B.java', 1,1,'b'),
            (10, 'src/main/C.java', 1,1,'c'),
            (9,  'src/main/A.java', 1,1,'a'),
            (8,  'src/main/A.java', 1,1,'a'),
            (8,  'src/main/B.java', 1,1,'b'),
            (8,  'src/main/Z.java', 1,1,'z'),
            (7,  'src/main/A.java', 1,1,'a'),
            (7,  'src/main/B.java', 1,1,'b'),
            (7,  'src/main/C.java', 1,1,'c'),
            (7,  'src/main/D.java', 1,1,'d'),
            (7,  'src/main/E.java', 1,1,'e'),
            (7,  'src/main/F.java', 1,1,'f'),
            (7,  'src/main/G.java', 1,1,'g'),
            (6,  'src/main/A.java', 1,1,'a');
        INSERT INTO pull_request_review_comments_current VALUES
            (1, 8, 'split this class', 'src/main/Z.java', 5, NULL, 'mentor'),
            (2, 8, 'good point', 'src/main/Z.java', 5, 1, 'alstn113'),
            (3, 10, 'naming', 'src/main/A.java', 3, NULL, 'mentor');
        """
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_analyze_selects_highest_jaccard_peer(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    art = analyze_repo("roomescape", "DongKey777", state_root=root, max_peers=1)
    assert art["status"] == "ok"
    # target is the learner's most-recent PR (#50, not #40)
    assert art["learner_pr"]["number"] == 50
    assert art["learner_pr"]["nickname"] == "동키"
    # 구름 (Jaccard 0.5) beats 릴리 (0.43)
    assert len(art["selection"]) == 1
    sel = art["selection"][0]
    assert sel["number"] == 30
    assert sel["nickname"] == "구름"
    assert sel["jaccard"] == 0.5
    assert sel["common_files"] == 2


def test_analyze_artifact_shape_and_string_keys(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    art = analyze_repo("roomescape", "DongKey777", state_root=root, max_peers=2)
    # both peers selected, 구름 first
    assert [p["number"] for p in art["peer_prs"]] == [30, 20]
    assert art["peer_prs"][0]["nickname"] == "구름"
    # peer_files + peer_only_files + representative_threads keyed by STRING (JSON-stable)
    assert "30" in art["peer_files"]
    assert isinstance(art["peer_files"]["30"], list)
    assert all(isinstance(k, str) for k in art["peer_only_files"])
    assert all(isinstance(k, str) for k in art["representative_threads"])
    # path partition relative to the union of selected peers
    assert "src/main/A.java" in art["common_paths"]
    assert "src/main/Z.java" in art["peer_only_paths"]
    # mentor thread on the peer PR survived
    peer30_threads = art["representative_threads"]["30"]
    assert peer30_threads[0]["root"]["author_login"] == "mentor"
    assert peer30_threads[0]["reply_count"] == 1


def test_analyze_roundtrips_through_json(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    art = analyze_repo("roomescape", "DongKey777", state_root=root)
    out = save_peer_pr(art, state_root=root)
    reloaded = json.loads(Path(out).read_text(encoding="utf-8"))
    # string keys are preserved (no int/str drift after JSON round-trip)
    assert "30" in reloaded["peer_files"]
    assert "30" in reloaded["peer_only_files"]
    assert reloaded["learner_pr"]["number"] == 50


def test_peer_only_files_computed_from_full_set_not_capped(tmp_path: Path) -> None:
    # Regression: a learner PR with >40 files (the producer cap) where a shared
    # file sorts AFTER the cap. peer_only_files must be derived from the FULL
    # learner set, so that shared file is NOT mislabeled as peer-only — even
    # though it has been truncated out of the capped learner_files list.
    arch = tmp_path / "repos" / "big" / "archive"
    arch.mkdir(parents=True)
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    conn.executescript(
        """
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            title TEXT, additions INTEGER, deletions INTEGER
        );
        CREATE TABLE pull_request_files_current (
            pull_request_id INTEGER, path TEXT,
            additions INTEGER, deletions INTEGER, patch_text TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
            github_comment_id INTEGER PRIMARY KEY, pull_request_id INTEGER, body TEXT,
            path TEXT, line INTEGER, in_reply_to_github_comment_id INTEGER, user_login TEXT
        );
        INSERT INTO pull_requests_current VALUES (1, 50, 'DongKey777', '[2단계] 동키(이동훈) 미션', 99, 9);
        INSERT INTO pull_requests_current VALUES (2, 30, 'peer1', '[2단계] 피어(김피어) 미션', 50, 5);
        """
    )
    # learner touches 50 files A00.java..A49.java (sorts alphabetically)
    learner_files = [f"src/main/A{i:02d}.java" for i in range(50)]
    conn.executemany(
        "INSERT INTO pull_request_files_current VALUES (1, ?, 1, 1, 'x')",
        [(p,) for p in learner_files])
    # peer touches A45.java (SHARED, sorts at rank 45 > the 40-cap) + a unique file
    conn.executemany(
        "INSERT INTO pull_request_files_current VALUES (2, ?, 1, 1, 'y')",
        [("src/main/A45.java",), ("src/main/PEER_ONLY.java",)])
    conn.commit()
    conn.close()

    art = analyze_repo("big", "DongKey777", state_root=tmp_path, max_peers=1)
    assert art["status"] == "ok"
    # the shared file is beyond the 40-cap, so it's absent from learner_files...
    assert "src/main/A45.java" not in art["learner_files"]
    # ...but it IS a common path (full-set computation) and must NOT be peer-only
    assert "src/main/A45.java" in art["common_paths"]
    peer_only = art["peer_only_files"]["30"]
    assert "src/main/A45.java" not in peer_only      # the bug would fabricate this
    assert "src/main/PEER_ONLY.java" in peer_only     # the genuine peer-only file


def test_jaccard_tie_break_prefers_recency(tmp_path: Path) -> None:
    # Two peers with IDENTICAL Jaccard to the learner target → the more recent
    # PR (higher number) surfaces first (documented tie-break: recency desc).
    arch = tmp_path / "repos" / "tie" / "archive"
    arch.mkdir(parents=True)
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    conn.executescript(
        """
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            title TEXT, additions INTEGER, deletions INTEGER
        );
        CREATE TABLE pull_request_files_current (
            pull_request_id INTEGER, path TEXT,
            additions INTEGER, deletions INTEGER, patch_text TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
            github_comment_id INTEGER PRIMARY KEY, pull_request_id INTEGER, body TEXT,
            path TEXT, line INTEGER, in_reply_to_github_comment_id INTEGER, user_login TEXT
        );
        INSERT INTO pull_requests_current VALUES (1, 50, 'DongKey777', '[2단계] 동키(이동훈) 미션', 9, 1);
        INSERT INTO pull_requests_current VALUES (2, 30, 'peerLow', '[2단계] 로우(김로우) 미션', 9, 1);
        INSERT INTO pull_requests_current VALUES (3, 40, 'peerHigh', '[2단계] 하이(김하이) 미션', 9, 1);
        -- learner {A,B}; peer #30 {A,C} and peer #40 {A,D} → both Jaccard = 1/3
        INSERT INTO pull_request_files_current VALUES
            (1, 'src/main/A.java', 1,1,'a'), (1, 'src/main/B.java', 1,1,'b'),
            (2, 'src/main/A.java', 1,1,'a'), (2, 'src/main/C.java', 1,1,'c'),
            (3, 'src/main/A.java', 1,1,'a'), (3, 'src/main/D.java', 1,1,'d');
        """
    )
    conn.commit()
    conn.close()
    art = analyze_repo("tie", "DongKey777", state_root=tmp_path, max_peers=2)
    sel = art["selection"]
    assert sel[0]["jaccard"] == sel[1]["jaccard"]      # genuine tie
    assert [s["number"] for s in sel] == [40, 30]       # recency-desc decides


def test_status_missing_archive(tmp_path: Path) -> None:
    art = analyze_repo("ghost", "DongKey777", state_root=tmp_path)
    assert art["status"] == "missing_archive"


def test_status_no_learner_pr(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    art = analyze_repo("roomescape", "SomeoneElse", state_root=root)
    assert art["status"] == "no_learner_pr"


def test_status_no_peer_when_only_learner(tmp_path: Path) -> None:
    arch = tmp_path / "repos" / "solo" / "archive"
    arch.mkdir(parents=True)
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    conn.executescript(
        """
        CREATE TABLE pull_requests_current (
            id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT,
            title TEXT, additions INTEGER, deletions INTEGER
        );
        CREATE TABLE pull_request_files_current (
            pull_request_id INTEGER, path TEXT,
            additions INTEGER, deletions INTEGER, patch_text TEXT
        );
        INSERT INTO pull_requests_current VALUES (1, 5, 'DongKey777', '[1단계] 동키(이동훈) 미션', 10, 1);
        INSERT INTO pull_request_files_current VALUES (1, 'src/main/A.java', 1, 1, 'a');
        """
    )
    conn.commit()
    conn.close()
    art = analyze_repo("solo", "DongKey777", state_root=tmp_path)
    assert art["status"] == "no_peer"
