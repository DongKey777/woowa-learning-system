"""Tests for anchors.extract + anchors.match (F11)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from anchors.extract import ReviewAnchor, load_anchors, save_anchors
from anchors.match import (
    CrossCrewMatch,
    assemble_matches,
    jaccard,
    parse_ai_veto,
    stage_1_path_filter,
    stage_2_jaccard_filter,
    stage_3_embed_rank,
    stage_4_ai_veto_prompt,
)


# ── jaccard ────────────────────────────────────────────────────────────


def test_jaccard_identical_code() -> None:
    code = "public void reserve(Request req) { service.save(req); }"
    assert jaccard(code, code) == 1.0


def test_jaccard_disjoint_zero() -> None:
    assert jaccard("xyz aaa", "qrs zzz") == 0.0


def test_jaccard_excludes_java_keywords() -> None:
    """`public class void return` keywords filtered → jaccard 0 on keyword-only."""
    assert jaccard("public class void", "private void return") == 0.0


def test_jaccard_partial() -> None:
    j = jaccard(
        "service.save(reservation)",
        "service.update(reservation)",
    )
    assert 0.2 < j < 0.7


# ── stage 1 + 2 ──────────────────────────────────────────────────────────


def _seed_archive(tmp_path: Path, repo: str = "myrepo") -> Path:
    p = tmp_path / "state" / "repos" / repo / "archive"
    p.mkdir(parents=True)
    db = p / "prs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE pull_requests_current (
          id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT
        );
        CREATE TABLE pull_request_review_comments_current (
          id INTEGER PRIMARY KEY, github_comment_id INTEGER, pull_request_id INTEGER,
          path TEXT, line INTEGER, diff_hunk TEXT, user_login TEXT, body TEXT,
          in_reply_to_github_comment_id INTEGER, created_at TEXT
        );
        INSERT INTO pull_requests_current VALUES (1, 100, 'donkey');
        INSERT INTO pull_requests_current VALUES (2, 200, 'cubin');
        INSERT INTO pull_requests_current VALUES (3, 300, 'rocky');
        -- mentor 'm1' on donkey PR (own thread → anchor candidate)
        INSERT INTO pull_request_review_comments_current
            (github_comment_id, pull_request_id, path, line, diff_hunk, user_login, body, in_reply_to_github_comment_id, created_at)
            VALUES (1001, 1, 'src/main/X.java', 42, 'service.save(req)', 'm1',
                    'DI 권장', NULL, '2026-01-01');
        -- mentor 'm2' on cubin PR (cross-crew at same path)
        INSERT INTO pull_request_review_comments_current
            (github_comment_id, pull_request_id, path, line, diff_hunk, user_login, body, in_reply_to_github_comment_id, created_at)
            VALUES (1002, 2, 'src/main/X.java', 42, 'service.save(req)', 'm2',
                    'context에 따라 OK', NULL, '2026-01-02');
        -- different path (Stage 1 must filter out)
        INSERT INTO pull_request_review_comments_current
            (github_comment_id, pull_request_id, path, line, diff_hunk, user_login, body, in_reply_to_github_comment_id, created_at)
            VALUES (1003, 3, 'src/main/Z.java', 5, 'unrelated', 'm3',
                    'comment', NULL, '2026-01-03');
        """
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_stage_1_path_filter_excludes_anchor_mentor(tmp_path: Path) -> None:
    seeded = _seed_archive(tmp_path)
    anchor = ReviewAnchor(
        thread_id="myrepo:100:1001", repo="myrepo", pr_number=100,
        path="src/main/X.java", line=42, code_hunk="service.save(req)",
        mentor_login="m1", comment_text="DI 권장", topic_concept_ids=[],
    )
    # archive_root expects {root}/repos/<repo>/archive/prs.sqlite3 layout
    cands = stage_1_path_filter(anchor, archive_root=seeded / "state")
    # m1 자체 (donkey PR thread) 제외 + 다른 path Z.java 제외 → m2 only
    assert len(cands) == 1
    assert cands[0]["comment_id"] == 1002
    assert cands[0]["reviewer"] == "m2"


def test_stage_2_jaccard_filter_threshold() -> None:
    anchor = ReviewAnchor(
        thread_id="x", repo="r", pr_number=1, path="p", line=1,
        code_hunk="service.save(reservation)", mentor_login="m", comment_text="c",
        topic_concept_ids=[],
    )
    candidates = [
        {"diff_hunk": "service.save(reservation)", "comment_id": 1},  # exact
        {"diff_hunk": "service.update(reservation)", "comment_id": 2},  # partial
        {"diff_hunk": "totally unrelated tokens", "comment_id": 3},   # disjoint
    ]
    out = stage_2_jaccard_filter(anchor, candidates, threshold=0.4)
    cids = [c["comment_id"] for c, _ in out]
    assert 1 in cids   # j=1.0
    assert 3 not in cids


def test_stage_3_embed_rank_with_mock_encoder() -> None:
    anchor = ReviewAnchor(
        thread_id="x", repo="r", pr_number=1, path="p", line=1,
        code_hunk="A", mentor_login="m", comment_text="c", topic_concept_ids=[],
    )
    j_passed = [({"diff_hunk": "X", "comment_id": 1}, 0.6),
                ({"diff_hunk": "Y", "comment_id": 2}, 0.5)]

    def mock_encode(texts):
        # anchor=[1,0], X=[0.9,0.1], Y=[0.1,0.9] → anchor more similar to X
        mapping = {"A": [1.0, 0.0], "X": [0.9, 0.1], "Y": [0.1, 0.9]}
        return np.array([mapping[t] for t in texts], dtype="float32")

    ranked = stage_3_embed_rank(anchor, j_passed, encode_fn=mock_encode)
    # X should rank above Y by cosine
    assert ranked[0][0]["comment_id"] == 1


def test_stage_4_veto_prompt_includes_anchor() -> None:
    anchor = ReviewAnchor(
        thread_id="x", repo="r", pr_number=1, path="p.java", line=42,
        code_hunk="service.save(req)", mentor_login="m1",
        comment_text="DI 권장", topic_concept_ids=[],
    )
    cands = [({"diff_hunk": "X", "comment_id": 1, "pr_number": 99, "reviewer": "m2",
               "crew_login": "cubin", "body": "다른 의견", "path": "p.java", "line": 42}, 0.6, 0.85)]
    prompt = stage_4_ai_veto_prompt(anchor, cands)
    assert "DI 권장" in prompt
    assert "다른 의견" in prompt
    assert "veto:" in prompt


def test_parse_ai_veto_extracts_keep_drop() -> None:
    text = """
    veto: r:99:1=keep
    veto: r:99:2=drop
    Some narrative line — ignored
    veto: r:99:3=KEEP
    """
    out = parse_ai_veto(text)
    assert out == {"r:99:1": True, "r:99:2": False, "r:99:3": True}


def test_assemble_matches_filters_dropped(tmp_path: Path) -> None:
    anchor = ReviewAnchor(
        thread_id="r:100:1001", repo="r", pr_number=100, path="p.java", line=42,
        code_hunk="A", mentor_login="m1", comment_text="c", topic_concept_ids=[],
    )
    ranked = [
        ({"comment_id": 1, "pr_number": 200, "path": "p.java", "line": 42,
          "diff_hunk": "X", "reviewer": "m2", "body": "b1", "crew_login": "cubin"}, 0.6, 0.9),
        ({"comment_id": 2, "pr_number": 300, "path": "p.java", "line": 42,
          "diff_hunk": "Y", "reviewer": "m3", "body": "b2", "crew_login": "rocky"}, 0.5, 0.8),
    ]
    veto = {"r:200:1": True, "r:300:2": False}
    matches = assemble_matches(anchor, ranked, veto=veto)
    assert len(matches) == 1
    assert matches[0].candidate_pr == 200
    assert matches[0].ai_veto is True
    assert matches[0].stage_passed == 4


def test_assemble_matches_no_veto_keeps_all() -> None:
    anchor = ReviewAnchor(
        thread_id="x", repo="r", pr_number=1, path="p", line=1,
        code_hunk="A", mentor_login="m", comment_text="c", topic_concept_ids=[],
    )
    ranked = [
        ({"comment_id": 1, "pr_number": 2, "path": "p", "line": 1,
          "diff_hunk": "X", "reviewer": "m", "body": "b", "crew_login": "c"}, 0.6, 0.9),
    ]
    matches = assemble_matches(anchor, ranked, veto=None)
    assert len(matches) == 1
    assert matches[0].stage_passed == 3


def test_anchor_save_load_roundtrip(tmp_path: Path) -> None:
    anchors = [
        ReviewAnchor(
            thread_id="r:1:1001", repo="r", pr_number=1, path="X.java", line=42,
            code_hunk="code", mentor_login="m", comment_text="c", topic_concept_ids=["spring/di"],
        ),
    ]
    save_anchors(anchors, state_root=tmp_path)
    loaded = load_anchors(state_root=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].thread_id == "r:1:1001"


def test_load_anchors_empty_when_missing(tmp_path: Path) -> None:
    assert load_anchors(state_root=tmp_path) == []
