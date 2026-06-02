"""Unit tests for core/pr_threads — live, pending-aware reconciliation.

No real network — gh wrapped by monkeypatching subprocess.run. REST endpoints
are keyed by the endpoint arg (cmd[2]); GraphQL is detected by cmd[2]=="graphql".
Response shapes mirror live PR #391 probes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.pr_threads import reconcile_pr_threads  # noqa: E402

OWNER, REPO, PR = "woowacourse", "repo", 1
LEARNER = "DongKey777"
_BASE = f"repos/{OWNER}/{REPO}/pulls/{PR}"


def _root(cid, login="mentor", path="A.java", line=5, body="root comment", review_id=10):
    """A mentor root: GitHub omits in_reply_to_id entirely on roots."""
    return {"id": cid, "user": {"login": login}, "path": path, "line": line,
            "body": body, "diff_hunk": "@@ -1 +1 @@", "pull_request_review_id": review_id,
            "created_at": "2026-05-29T14:00:00Z", "updated_at": "2026-05-29T14:00:00Z"}


def _reply(cid, root_id, login=LEARNER, review_id=20, ts="2026-06-01T08:00:00Z"):
    return {"id": cid, "user": {"login": login}, "in_reply_to_id": root_id,
            "body": "reply", "pull_request_review_id": review_id,
            "created_at": ts, "updated_at": ts}


def _review(rid, login, state, submitted_at="2026-05-29T14:42:00Z"):
    r = {"id": rid, "user": {"login": login}, "state": state}
    if submitted_at is not None:
        r["submitted_at"] = submitted_at
    return r


def _gthread(db_id, *, resolved=False, outdated=False, resolved_by=None,
             author="mentor", path="A.java", line=5):
    return {"isResolved": resolved, "isOutdated": outdated, "isCollapsed": False,
            "resolvedBy": {"login": resolved_by} if resolved_by else None,
            "path": path, "line": line,
            "comments": {"nodes": [{"databaseId": db_id, "author": {"login": author}}]}}


def _patch(monkeypatch, *, detail=None, reviews=None, comments=None, per_review=None,
           decision=None, gthreads=None, error_on=None):
    """Mock gh. Paginated REST → json.dumps([page]); detail → dict; graphql → data envelope."""
    calls: list[list[str]] = []
    detail = detail or {"state": "open", "mergeable": True}
    reviews = reviews or []
    comments = comments or []
    per_review = per_review or {}
    responses = {
        _BASE: json.dumps(detail),
        f"{_BASE}/reviews?per_page=100": json.dumps([reviews]),
        f"{_BASE}/comments?per_page=100": json.dumps([comments]),
    }
    for rid, cs in per_review.items():
        responses[f"{_BASE}/reviews/{rid}/comments?per_page=100"] = json.dumps([cs])

    class _Result:
        def __init__(self, stdout, returncode=0, stderr=""):
            self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    def _run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:2] != ["gh", "api"]:
            return _Result("", 1, "unexpected")
        if cmd[2] == "graphql":
            if error_on == "graphql":
                return _Result("", 1, "graphql boom")
            pr_payload = {"reviewDecision": decision,
                          "reviewThreads": {"nodes": gthreads or []}}
            return _Result(json.dumps({"data": {"repository": {"pullRequest": pr_payload}}}))
        endpoint = cmd[2]
        if error_on == endpoint:
            return _Result("", 1, "boom")
        if endpoint in responses:
            return _Result(responses[endpoint])
        return _Result("", 1, f"no mock for {endpoint}")

    monkeypatch.setattr("subprocess.run", _run)
    return calls


def _run_reconcile(monkeypatch, tmp_path, **patch_kw):
    _patch(monkeypatch, **patch_kw)
    return reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                                state_root=tmp_path, write_snapshot=False)


# ── status reconciliation ────────────────────────────────────────────────

def test_unanswered_mentor_root_only(monkeypatch, tmp_path):
    r = _run_reconcile(monkeypatch, tmp_path, comments=[_root(100)])
    assert r["status"] == "ok"
    assert r["counts"]["total"] == 1
    assert r["counts"]["unanswered"] == 1
    t = r["threads"][0]
    assert t["root_comment_id"] == 100
    assert t["status"] == "unanswered"
    assert t["has_pending_draft"] is False
    assert t["reply_count"] == 0


def test_answered_by_submitted_reply(monkeypatch, tmp_path):
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100), _reply(101, 100)])
    t = r["threads"][0]
    assert t["status"] == "answered_submitted"
    assert t["reply_count"] == 1
    assert r["counts"]["answered_submitted"] == 1


def test_answered_by_pending_draft_only(monkeypatch, tmp_path):
    reviews = [_review(99, "mentor", "CHANGES_REQUESTED"),
               _review(555, LEARNER, "PENDING", submitted_at=None)]
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100)], reviews=reviews,
                       per_review={555: [_reply(200, 100, review_id=555)]})
    t = r["threads"][0]
    assert t["status"] == "answered_pending"
    assert t["has_pending_draft"] is True
    assert r["pending_review_id"] == 555
    assert r["counts"]["answered_pending"] == 1


def test_submitted_beats_pending(monkeypatch, tmp_path):
    reviews = [_review(555, LEARNER, "PENDING", submitted_at=None)]
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100), _reply(101, 100)], reviews=reviews,
                       per_review={555: [_reply(200, 100, review_id=555)]})
    t = r["threads"][0]
    assert t["status"] == "answered_submitted"
    assert t["has_pending_draft"] is True  # draft still flagged, status wins


def test_no_pending_review_skips_draft_fetch(monkeypatch, tmp_path):
    reviews = [_review(99, "mentor", "CHANGES_REQUESTED")]
    calls = _patch(monkeypatch, comments=[_root(100)], reviews=reviews)
    r = reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                             state_root=tmp_path, write_snapshot=False)
    assert r["pending_review_id"] is None
    assert not any("/reviews/" in c[2] for c in calls if len(c) > 2)


def test_learner_own_root_excluded(monkeypatch, tmp_path):
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100, login=LEARNER), _root(101, login="mentor")])
    ids = [t["root_comment_id"] for t in r["threads"]]
    assert ids == [101]


def test_bot_root_excluded(monkeypatch, tmp_path):
    bot = {"id": 100, "user": {"login": "github-actions[bot]", "type": "Bot"},
           "path": "A.java", "line": 1, "body": "ci"}
    r = _run_reconcile(monkeypatch, tmp_path, comments=[bot, _root(101)])
    ids = [t["root_comment_id"] for t in r["threads"]]
    assert ids == [101]


def test_multi_reviewer_decisive(monkeypatch, tmp_path):
    reviews = [
        _review(1, "alice", "CHANGES_REQUESTED", "2026-05-29T10:00:00Z"),
        _review(2, "alice", "APPROVED", "2026-05-30T10:00:00Z"),  # later wins
        _review(3, "bob", "COMMENTED", "2026-05-30T11:00:00Z"),  # not decisive
        _review(4, LEARNER, "COMMENTED", "2026-05-30T12:00:00Z"),  # learner excluded
        _review(5, "carol", "DISMISSED", "2026-05-30T13:00:00Z"),
    ]
    r = _run_reconcile(monkeypatch, tmp_path, comments=[_root(100)], reviews=reviews)
    assert r["reviewer_decisions"] == {"alice": "APPROVED", "carol": "DISMISSED"}


def test_dangling_in_reply_to_graceful(monkeypatch, tmp_path):
    # reply points at a root id that isn't present → grouped but no phantom thread
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100), _reply(101, 999)])
    assert len(r["threads"]) == 1
    assert r["threads"][0]["root_comment_id"] == 100
    assert r["threads"][0]["reply_count"] == 0


def test_pagination_flattens(monkeypatch, tmp_path):
    # two REST pages for /comments
    page1 = [_root(100), _root(101)]
    page2 = [_root(102)]
    calls = []

    class _Result:
        def __init__(self, stdout, rc=0, stderr=""):
            self.stdout, self.returncode, self.stderr = stdout, rc, stderr

    def _run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[2] == "graphql":
            return _Result(json.dumps({"data": {"repository": {"pullRequest":
                {"reviewDecision": None, "reviewThreads": {"nodes": []}}}}}))
        ep = cmd[2]
        if ep == _BASE:
            return _Result(json.dumps({"state": "open", "mergeable": True}))
        if ep == f"{_BASE}/reviews?per_page=100":
            return _Result(json.dumps([[]]))
        if ep == f"{_BASE}/comments?per_page=100":
            return _Result(json.dumps([page1, page2]))  # two pages
        return _Result("", 1, "no mock")

    monkeypatch.setattr("subprocess.run", _run)
    r = reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                             state_root=tmp_path, write_snapshot=False)
    assert r["counts"]["total"] == 3


def test_rest_error_returns_envelope(monkeypatch, tmp_path):
    r = _run_reconcile(monkeypatch, tmp_path, comments=[_root(100)],
                       error_on=f"{_BASE}/reviews?per_page=100")
    assert r["status"] == "error"
    assert "boom" in r["error"]


# ── GraphQL overlay ──────────────────────────────────────────────────────

def test_graphql_overlay_matches_by_database_id(monkeypatch, tmp_path):
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100), _reply(101, 100)],
                       decision="CHANGES_REQUESTED",
                       gthreads=[_gthread(100, resolved=True, outdated=True,
                                          resolved_by="mentor")])
    t = r["threads"][0]
    assert t["is_resolved"] is True
    assert t["is_outdated"] is True
    assert t["resolved_by"] == "mentor"
    assert r["review_decision"] == "CHANGES_REQUESTED"
    assert r["counts"]["resolved"] == 1
    assert r["counts"]["outdated"] == 1


def test_graphql_unmatched_thread_dropped(monkeypatch, tmp_path):
    # GraphQL reports a thread whose root id has no REST root → not surfaced
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100)],
                       gthreads=[_gthread(100), _gthread(888, resolved=True)])
    ids = [t["root_comment_id"] for t in r["threads"]]
    assert ids == [100]
    assert r["counts"]["resolved"] == 0  # 888's resolved state never leaks in


def test_graphql_failure_degrades_to_empty_overlay(monkeypatch, tmp_path):
    r = _run_reconcile(monkeypatch, tmp_path, comments=[_root(100)],
                       error_on="graphql")
    assert r["status"] == "ok"
    assert r["review_decision"] is None
    assert r["threads"][0]["is_resolved"] is False


# ── round timeline ───────────────────────────────────────────────────────

def test_round_timeline_ordered_with_counts(monkeypatch, tmp_path):
    reviews = [
        _review(10, "mentor", "CHANGES_REQUESTED", "2026-05-29T14:42:00Z"),
        _review(20, LEARNER, "COMMENTED", "2026-06-01T08:00:00Z"),
        _review(99, LEARNER, "PENDING", submitted_at=None),  # excluded (no submit)
    ]
    r = _run_reconcile(monkeypatch, tmp_path,
                       comments=[_root(100, review_id=10), _reply(101, 100, review_id=20)],
                       reviews=reviews, per_review={99: []})
    tl = r["round_timeline"]
    assert [e["actor"] for e in tl] == ["mentor", "learner"]
    assert tl[0]["state"] == "CHANGES_REQUESTED" and tl[0]["comment_count"] == 1
    assert tl[1]["comment_count"] == 1


# ── delta vs prior snapshot ──────────────────────────────────────────────

def test_delta_first_run_then_newly_resolved_answered_and_new_root(monkeypatch, tmp_path):
    # Run 1: root 100 unanswered, unresolved — write snapshot.
    _patch(monkeypatch, comments=[_root(100)])
    r1 = reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                              state_root=tmp_path, write_snapshot=True)
    assert r1["delta"]["first_run"] is True

    # Run 2: 100 now answered+resolved; new mentor root 102 appears.
    _patch(monkeypatch,
           comments=[_root(100), _reply(101, 100), _root(102)],
           gthreads=[_gthread(100, resolved=True, resolved_by="mentor")])
    r2 = reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                              state_root=tmp_path, write_snapshot=True)
    d = r2["delta"]
    assert d["first_run"] is False
    assert {x["root_comment_id"] for x in d["newly_resolved"]} == {100}
    assert {x["root_comment_id"] for x in d["newly_answered"]} == {100}
    assert {x["root_comment_id"] for x in d["new_mentor_roots"]} == {102}

    # Run 3: no change → empty delta lists (idempotent).
    r3 = reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                              state_root=tmp_path, write_snapshot=True)
    assert r3["delta"]["newly_resolved"] == []
    assert r3["delta"]["newly_answered"] == []
    assert r3["delta"]["new_mentor_roots"] == []


def test_snapshot_written_to_builder_path(monkeypatch, tmp_path):
    _patch(monkeypatch, comments=[_root(100)])
    reconcile_pr_threads(REPO, PR, LEARNER, owner=OWNER,
                         state_root=tmp_path, write_snapshot=True)
    snap = tmp_path / "repos" / REPO / "pr_threads" / f"{PR}.json"
    assert snap.exists()
    payload = json.loads(snap.read_text())
    assert payload["pr_number"] == PR
    assert "100" in payload["threads"]
