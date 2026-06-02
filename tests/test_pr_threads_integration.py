"""Integration test for bin/learn-pr-retro --live.

--live annotates stale archive 'unresolved' threads with their real live status
(so an already-answered thread is no longer flagged open). Reply *drafting* is
deliberately NOT a system feature — the session authors replies in-conversation
from pr-thread-status output, so there is no compose tool to test here.

The bin entry is loaded via SourceFileLoader (precedent:
tests/test_index_fetch_auto_upgrade.py) and its module-level reconcile import is
monkeypatched — no network.
"""
from __future__ import annotations

import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    loader = SourceFileLoader(name, str(REPO_ROOT / "bin" / filename))
    mod = types.ModuleType(loader.name)
    mod.__file__ = str(REPO_ROOT / "bin" / filename)
    loader.exec_module(mod)
    return mod


retro_mod = _load("learn_pr_retro_mod", "learn-pr-retro")


def _thread(cid, status, path="A.java", line=5, excerpt="용어를 통일해주세요",
            resolved=False, outdated=False):
    return {"root_comment_id": cid, "path": path, "line": line,
            "root_excerpt": excerpt, "diff_hunk": "@@ -1 +1 @@",
            "status": status, "is_resolved": resolved, "is_outdated": outdated,
            "has_pending_draft": status == "answered_pending", "reply_count": 0}


# ── learn-pr-retro --live ────────────────────────────────────────────────

def _unresolved(pr, path, body, line=None):
    return types.SimpleNamespace(pr_number=pr, path=path, line=line,
                                 body_excerpt=body, mentor_login="jurlring",
                                 open_days=3)


def test_retro_live_annotates_stale_unresolved(monkeypatch):
    # Archive says 3 unresolved; live shows #1 resolved, #2 answered, #3 PR errored.
    retro = types.SimpleNamespace(unresolved_threads=[
        _unresolved(200, "X.java", "트랜잭션 경계를 명확히 해주세요", line=5),
        _unresolved(200, "Y.java", "네이밍을 통일해주세요", line=10),
        _unresolved(300, "Z.java", "왜 필요한가요?"),
    ])

    def fake_reconcile(repo, pr, learner, **kw):
        if pr == 200:
            return {"status": "ok", "threads": [
                _thread(11, "answered_submitted", path="X.java", line=5,
                        excerpt="트랜잭션 경계를 명확히 해주세요", resolved=True),
                _thread(12, "unanswered", path="Y.java", line=10,
                        excerpt="네이밍을 통일해주세요"),
            ]}
        return {"status": "error", "error": "PR not found"}

    monkeypatch.setattr(retro_mod, "reconcile_pr_threads", fake_reconcile)
    rc = retro_mod._live_reconciliation(retro, "repo", "DongKey777",
                                        "woowacourse", Path("/tmp"))
    assert rc["checked_prs"] == [200, 300]
    assert rc["stale_unresolved_total"] == 3
    assert rc["actually_answered_or_resolved"] == 1  # X.java resolved
    assert rc["still_open"] == 1                       # Y.java still unanswered
    by_path = {a["path"]: a for a in rc["annotations"]}
    assert by_path["X.java"]["live_status"] == "answered_submitted"
    assert by_path["X.java"]["is_resolved"] is True
    assert by_path["Y.java"]["live_status"] == "unanswered"
    assert by_path["Z.java"]["live_status"] == "live_unknown"  # PR errored
    assert by_path["Z.java"]["is_resolved"] is None


def test_retro_match_by_line_when_body_differs(monkeypatch):
    u = _unresolved(5, "F.java", "완전히 다른 본문 텍스트", line=42)
    live = [_thread(9, "answered_submitted", path="F.java", line=42,
                    excerpt="여기 멘토 코멘트")]
    m = retro_mod._match_live(u, live)
    assert m is not None and m["root_comment_id"] == 9
