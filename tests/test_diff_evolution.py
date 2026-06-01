"""Tests for mission.diff_evolution — F1 family H (pr_diff_evolution)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from core.coach import _render_pr_diff_evolution
from core.lazy_loader import _load_pr_diff_evolution
from mission.diff_evolution import analyze_repo, save_evolution

LEARNER = "donkey"
MENTOR = "hyeonic"
REPO = "spring-roomescape-member"
SVC = "src/main/java/roomescape/ReservationService.java"
CTRL = "src/main/java/roomescape/ReservationController.java"


def _archive(tmp_path: Path, *, reviews_schema="new", head_sha="da14655") -> sqlite3.Connection:
    arch = tmp_path / "repos" / REPO / "archive"
    arch.mkdir(parents=True)
    conn = sqlite3.connect(str(arch / "prs.sqlite3"))
    conn.executescript(f"""
      CREATE TABLE pull_requests_current (
        id INTEGER PRIMARY KEY, number INTEGER, author_login TEXT, head_sha TEXT,
        base_ref_name TEXT, commits_count INTEGER, merged_at TEXT, state TEXT);
      INSERT INTO pull_requests_current VALUES
        (1, 390, '{LEARNER}', '{head_sha}', 'base', 5, '2026-05-11T13:52:27Z', 'closed');
      CREATE TABLE pull_request_review_comments_current (
        id INTEGER PRIMARY KEY, pull_request_id INTEGER, github_review_id INTEGER,
        github_comment_id INTEGER, user_login TEXT, path TEXT, commit_id TEXT,
        in_reply_to_github_comment_id INTEGER, diff_hunk TEXT, body TEXT, created_at TEXT);
      CREATE TABLE pull_request_files_current (
        id INTEGER PRIMARY KEY, pull_request_id INTEGER, path TEXT, patch_text TEXT);
    """)
    # H3 hotspot + round comments (2 mentor reviews + 1 learner self-review)
    rc = [
        (1, 1, 5001, MENTOR, SVC, "원자성 어떻게 보장?", "2026-05-07T14:00:00Z"),
        (2, 1, 5001, MENTOR, SVC, "중복 검증 위치?", "2026-05-07T14:01:00Z"),
        (3, 1, 5001, MENTOR, CTRL, "애노테이션 목적?", "2026-05-07T14:02:00Z"),
        (4, 1, 5002, LEARNER, SVC, "고쳤습니다", "2026-05-10T07:00:00Z"),  # learner reply
        (5, 1, 5003, MENTOR, SVC, "트랜잭션 경계 재검토", "2026-05-10T23:00:00Z"),
    ]
    for cid, pid, rid, who, path, body, created in rc:
        conn.execute(
            "INSERT INTO pull_request_review_comments_current "
            "(id, pull_request_id, github_review_id, github_comment_id, user_login, path, body, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, pid, rid, cid + 9000, who, path, body, created))
    # H4 smell — patch_text with a Spring annotation + JDBC exception
    conn.execute(
        "INSERT INTO pull_request_files_current (pull_request_id, path, patch_text) VALUES (?,?,?)",
        (1, SVC, "@Service\nimport ...DataIntegrityViolationException;\nclass ReservationService {}"))

    if reviews_schema == "new":
        conn.execute("CREATE TABLE pull_request_reviews_current (id INTEGER PRIMARY KEY, "
                     "pull_request_id INTEGER, github_review_id INTEGER, reviewer_login TEXT, "
                     "state TEXT, submitted_at TEXT, reviewer_role TEXT)")
        rows = [
            (1, 1, 5001, MENTOR, "CHANGES_REQUESTED", "2026-05-07T14:53:16Z", "mentor"),
            (2, 1, 5002, LEARNER, "COMMENTED", "2026-05-10T07:51:15Z", "author"),
            (3, 1, 5003, MENTOR, "CHANGES_REQUESTED", "2026-05-10T23:47:21Z", "mentor"),
            (4, 1, 5004, MENTOR, "APPROVED", "2026-05-11T13:51:47Z", "mentor"),
        ]
        conn.executemany("INSERT INTO pull_request_reviews_current VALUES (?,?,?,?,?,?,?)", rows)
    else:  # legacy: user_login, no reviewer_role
        conn.execute("CREATE TABLE pull_request_reviews_current (id INTEGER PRIMARY KEY, "
                     "pull_request_id INTEGER, github_review_id INTEGER, user_login TEXT, "
                     "state TEXT, submitted_at TEXT)")
        rows = [
            (1, 1, 5001, MENTOR, "CHANGES_REQUESTED", "2026-05-07T14:53:16Z"),
            (3, 1, 5003, MENTOR, "CHANGES_REQUESTED", "2026-05-10T23:47:21Z"),
            (4, 1, 5004, MENTOR, "APPROVED", "2026-05-11T13:51:47Z"),
        ]
        conn.executemany("INSERT INTO pull_request_reviews_current VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def _git(d: Path, *args, when=None):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", str(d), *args], check=True,
                   capture_output=True, env={**_os_environ(), **env})


def _os_environ() -> dict:
    import os
    return dict(os.environ)


def _make_clone(mission_root: Path) -> str:
    """Tiny real git repo: base commit + a post-review fix commit touching SVC."""
    d = mission_root / REPO
    d.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    (d / "base.txt").write_text("base")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "base", when="2026-05-01T00:00:00")
    _git(d, "branch", "base")  # base ref (PR base)
    svc = d / SVC
    svc.parent.mkdir(parents=True, exist_ok=True)
    svc.write_text("class ReservationService { /* UNIQUE + tx */ }")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "feat: 중복 예약 방지 UNIQUE + 트랜잭션",
         when="2026-05-11T01:55:00")
    head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                          capture_output=True, text=True, env=_os_environ()).stdout.strip()
    return head


# ── clone-free analysis (H3/H4 + review-only rounds) ──────────────────────

def test_missing_archive(tmp_path: Path) -> None:
    rep = analyze_repo("nope", LEARNER, tmp_path)
    assert rep["status"] == "missing_archive"
    assert rep["prs"] == []


def test_hotspots_grouped_by_path(tmp_path: Path) -> None:
    _archive(tmp_path).close()
    pr = analyze_repo(REPO, LEARNER, tmp_path)["prs"][0]
    hs = {h["path"]: h["comments"] for h in pr["hotspots"]}
    assert hs[SVC] == 4  # 3 mentor + 1 learner comment on SVC
    assert hs[CTRL] == 1


def test_smells_from_patch_text(tmp_path: Path) -> None:
    _archive(tmp_path).close()
    pr = analyze_repo(REPO, LEARNER, tmp_path)["prs"][0]
    cids = {s["concept_id"] for s in pr["smells"]}
    assert "spring/bean-di-basics" in cids            # @Service
    assert "database/jdbc-jpa-mybatis-basics" in cids  # DataIntegrityViolationException


def test_rounds_are_mentor_reviews_only(tmp_path: Path) -> None:
    _archive(tmp_path).close()
    pr = analyze_repo(REPO, LEARNER, tmp_path)["prs"][0]
    # 3 mentor reviews → 3 rounds; learner's own COMMENTED review is excluded
    assert pr["round_count"] == 3
    assert all(r["reviewer"] == MENTOR for r in pr["rounds"])
    r1 = pr["rounds"][0]
    assert r1["state"] == "CHANGES_REQUESTED"
    assert r1["comment_count"] == 3  # 3 comments under review 5001


def test_legacy_reviews_schema_user_login(tmp_path: Path) -> None:
    _archive(tmp_path, reviews_schema="old").close()
    pr = analyze_repo(REPO, LEARNER, tmp_path)["prs"][0]
    assert pr["round_count"] == 3  # user_login column resolved


def test_no_clone_means_no_review_to_fix(tmp_path: Path) -> None:
    _archive(tmp_path).close()
    rep = analyze_repo(REPO, LEARNER, tmp_path)  # no mission clone in tmp
    assert rep["clone_available"] is False
    assert rep["prs"][0]["review_to_fix"] == []


# ── with a real git clone (H1 fix commits + H2 review→fix) ────────────────

def test_review_to_fix_links_with_clone(tmp_path: Path) -> None:
    mission_root = tmp_path / "missions"
    head = _make_clone(mission_root)
    _archive(tmp_path, head_sha=head).close()
    rep = analyze_repo(REPO, LEARNER, tmp_path, mission_root=mission_root)
    pr = rep["prs"][0]
    assert rep["clone_available"] is True
    links = pr["review_to_fix"]
    assert any(l["path"] == SVC for l in links)  # mentor SVC comment → fix commit
    svc_link = next(l for l in links if l["path"] == SVC)
    assert "트랜잭션" in svc_link["fix_subject"]
    # the fix commit is also attributed to the round it followed (R2, after 05-10)
    r_with_fix = [r for r in pr["rounds"] if r["fix_commits"]]
    assert r_with_fix and r_with_fix[0]["fix_commits"][0]["subject"].startswith("feat")


# ── loader + coach rendering ──────────────────────────────────────────────

def test_loader_missing_when_unbuilt(tmp_path: Path) -> None:
    out = _load_pr_diff_evolution(REPO, tmp_path)
    assert out["missing"] is True
    assert out["prs"] == []


def test_loader_reads_built_artifact(tmp_path: Path) -> None:
    _archive(tmp_path).close()
    rep = analyze_repo(REPO, LEARNER, tmp_path)
    save_evolution(rep, REPO, tmp_path)
    out = _load_pr_diff_evolution(REPO, tmp_path)
    assert out["ready"] is True
    assert out["pr_count"] == 1
    assert out["prs"][0]["number"] == 390


def test_render_missing_says_not_built() -> None:
    md = _render_pr_diff_evolution({"missing": True, "prs": []})
    assert "NOT YET BUILT" in md


def test_render_includes_rounds_and_links() -> None:
    art = {
        "ready": True, "pr_count": 1, "clone_available": True,
        "prs": [{
            "number": 390, "merged": True, "round_count": 1,
            "rounds": [{"index": 1, "reviewer": MENTOR, "state": "APPROVED",
                        "submitted_at": "2026-05-11T13:51:47Z", "comment_count": 0,
                        "comment_paths": [], "fix_commits": []}],
            "review_to_fix": [{"path": SVC, "reviewer": MENTOR,
                               "review_at": "2026-05-07T14:00:00Z",
                               "comment_excerpt": "원자성 어떻게 보장?",
                               "fix_sha": "c890e188", "fix_at": "2026-05-11T01:55:00+09:00",
                               "fix_subject": "feat: 트랜잭션 처리"}],
            "hotspots": [{"path": SVC, "comments": 4}],
            "smells": [{"path": SVC, "concept_id": "spring/bean-di-basics",
                        "value": "@Service", "line": 1, "kind": "annotation"}],
        }],
    }
    md = _render_pr_diff_evolution(art)
    assert "PR#390" in md
    assert "리뷰→수정" in md
    assert "c890e188" in md
    assert "핫스팟" in md
