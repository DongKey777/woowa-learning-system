"""Tests for mission.memory_review — F2 family M (memory_review)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from core.coach import _render_memory_review
from core.lazy_loader import _load_memory_review
from core.mastery import record_evidence
from mission.memory_review import analyze, save_memory_review

LEARNER = "donkey"
REPO = "spring-roomescape-waiting"

USED_BLIND = "language/atomic-classes"        # used, never asked, no mastery
USED_ASKED = "spring/bean-di-basics"          # used + asked → not blind
USED_MASTERED = "spring/ioc-di-basics"        # used, not asked, mastered → excluded
FORGOTTEN = "database/lock-basics"            # familiar, last seen 40d ago

NOW = 1_700_000_000.0
DAY = 86400.0


def _set_level(state_root: Path, cid: str, level: str) -> None:
    db = state_root / "learner" / "mastery_graph.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE mastery SET bloom_level=? WHERE concept_id=?", (level, cid))
        conn.commit()
    finally:
        conn.close()


def _patterns(state_root: Path, repo: str, concept_ids: list[str]) -> None:
    d = state_root / "repos" / repo
    d.mkdir(parents=True, exist_ok=True)
    pats = [{"matched_concept_id": c, "snippet": f"// {c}"} for c in concept_ids]
    (d / "mission_patterns.json").write_text(
        json.dumps({"repo": repo, "patterns": pats}, ensure_ascii=False), encoding="utf-8")


def _history(state_root: Path, asked: list[str]) -> None:
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"event_type": "ask", "payload": {"top_concept_ids": asked}},
                      ensure_ascii=False)
    (d / "history.jsonl").write_text(line + "\n", encoding="utf-8")


def _anchors(state_root: Path) -> None:
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    anchors = [
        {"repo": REPO, "pr_number": 391, "path": "src/Foo.java",
         "comment_text": "왜 403이 아니라 404 인가요?",
         "topic_concept_ids": ["security/auth-failure-response-401-403-404"]},
        {"repo": REPO, "pr_number": 391, "path": "src/Bar.java",
         "comment_text": "이건 링크 안 된 코멘트", "topic_concept_ids": []},
    ]
    (d / "review_anchors.json").write_text(
        json.dumps({"anchor_count": len(anchors), "anchors": anchors}, ensure_ascii=False),
        encoding="utf-8")


def _seed(state_root: Path) -> None:
    # create mastery db + rows. USED_BLIND gets no evidence (level None).
    record_evidence(USED_ASKED, "mission_use", payload={"repo": REPO},
                    state_root=state_root, ts=NOW - DAY)
    record_evidence(USED_MASTERED, "mission_use", payload={"repo": REPO},
                    state_root=state_root, ts=NOW - DAY)
    record_evidence(FORGOTTEN, "mission_use", payload={"repo": REPO},
                    state_root=state_root, ts=NOW - 40 * DAY)
    _set_level(state_root, USED_MASTERED, "mastered")
    _set_level(state_root, FORGOTTEN, "familiar")
    _patterns(state_root, REPO, [USED_BLIND, USED_ASKED, USED_MASTERED])
    _history(state_root, [USED_ASKED])
    _anchors(state_root)


def test_missing_mastery_db(tmp_path: Path) -> None:
    rep = analyze(LEARNER, tmp_path)
    assert rep["status"] == "missing_mastery"
    assert rep["blind_spots"] == []


def test_blind_spot_used_but_never_asked(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    cids = {b["concept_id"] for b in rep["blind_spots"]}
    assert USED_BLIND in cids        # used in code, never asked
    assert USED_ASKED not in cids    # asked → not a blind spot


def test_blind_spot_excludes_mastered(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    cids = {b["concept_id"] for b in rep["blind_spots"]}
    assert USED_MASTERED not in cids  # never asked but mastered → not surfaced


def test_blind_spot_unstudied_first(tmp_path: Path) -> None:
    """A concept with no mastery row (level None) sorts ahead of a familiar one."""
    _seed(tmp_path)
    # make FORGOTTEN also a blind spot (used, not asked) by adding to patterns
    _patterns(tmp_path, REPO, [USED_BLIND, USED_ASKED, USED_MASTERED, FORGOTTEN])
    rep = analyze(LEARNER, tmp_path, now=NOW)
    order = [b["concept_id"] for b in rep["blind_spots"]]
    assert order.index(USED_BLIND) < order.index(FORGOTTEN)


def test_forgetting_old_familiar(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    forgot = {f["concept_id"] for f in rep["forgetting"]}
    assert FORGOTTEN in forgot                       # familiar + 40d old
    assert USED_ASKED not in forgot                  # recent → not forgotten


def test_review_cards_only_linked_anchors(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    assert len(rep["review_cards"]) == 1             # only the concept-linked anchor
    card = rep["review_cards"][0]
    assert card["pr_number"] == 391
    assert card["concept_ids"] == ["security/auth-failure-response-401-403-404"]


def test_loader_missing_when_unbuilt(tmp_path: Path) -> None:
    out = _load_memory_review(tmp_path)
    assert out["missing"] is True
    assert out["ready"] is False
    assert out["blind_spots"] == []


def test_loader_reads_built_artifact(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    save_memory_review(rep, tmp_path)
    out = _load_memory_review(tmp_path)
    assert out["ready"] is True
    assert any(b["concept_id"] == USED_BLIND for b in out["blind_spots"])


def test_render_missing_says_not_built() -> None:
    md = _render_memory_review({"missing": True})
    assert "NOT YET BUILT" in md


def test_render_includes_blind_and_cards() -> None:
    art = {
        "ready": True,
        "blind_spots": [{"concept_id": USED_BLIND, "repos": [REPO], "bloom_level": None}],
        "forgetting": [{"concept_id": FORGOTTEN, "bloom_level": "familiar",
                        "days_since_seen": 40.0}],
        "review_cards": [{"repo": REPO, "pr_number": 391, "path": "src/Foo.java",
                          "concept_ids": ["security/auth-failure-response-401-403-404"],
                          "mentor_prompt": "왜 403이 아니라 404 인가요?"}],
    }
    md = _render_memory_review(art)
    assert "사각지대" in md
    assert USED_BLIND in md
    assert "복습 카드" in md
    assert "403" in md
