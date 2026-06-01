"""Tests for mission.cross_mission — F2 family L (cross_mission)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.coach import _render_cross_mission
from core.lazy_loader import _load_cross_mission
from core.mastery import record_evidence
from mission.cross_mission import analyze, save_cross_mission

LEARNER = "donkey"
A = "spring-roomescape-member"   # earlier mission (proven)
B = "spring-roomescape-waiting"  # later mission (reuses)
SHARED = "spring/bean-di-basics"
A_ONLY = "spring/mvc-controller-basics"

T1 = 1_700_000_000.0   # repo A merge time (earlier)
T2 = 1_700_500_000.0   # repo B mission_use time (later)


def _patterns(state_root: Path, repo: str, concept_ids: list[str]) -> None:
    import json
    d = state_root / "repos" / repo
    d.mkdir(parents=True, exist_ok=True)
    patterns = [{"matched_concept_id": c, "snippet": f"// {c}"} for c in concept_ids]
    (d / "mission_patterns.json").write_text(
        json.dumps({"repo": repo, "patterns": patterns}, ensure_ascii=False),
        encoding="utf-8")


def _seed(state_root: Path) -> None:
    # A: two concepts proven (pr_merge + mentor_accept) at T1
    for c in (SHARED, A_ONLY):
        record_evidence(c, "pr_merge", payload={"repo": A}, state_root=state_root,
                        ts=T1, dedup_key=f"merge:{A}:{c}")
        record_evidence(c, "mentor_accept", payload={"repo": A}, state_root=state_root,
                        ts=T1, dedup_key=f"accept:{A}:{c}")
    # B: learner reuses SHARED (mission_use) at T2 + extracted in patterns
    record_evidence(SHARED, "mission_use", payload={"repo": B}, state_root=state_root,
                    ts=T2, dedup_key=f"use:{B}:{SHARED}")
    _patterns(state_root, A, [SHARED, A_ONLY])
    _patterns(state_root, B, [SHARED, "database/lock-basics"])


def test_missing_mastery_db(tmp_path: Path) -> None:
    rep = analyze(LEARNER, tmp_path)
    assert rep["status"] == "missing_mastery"
    assert rep["carryover"] == []


def test_carryover_proven_earlier_reused_later(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=T2 + 1000)
    pairs = {(c["concept_id"], c["proven_in"], c["reused_in"]) for c in rep["carryover"]}
    # SHARED proven in A, reused in B's patterns → carryover link
    assert (SHARED, A, B) in pairs
    # A_ONLY is proven in A but B doesn't use it → no carryover for it
    assert not any(c["concept_id"] == A_ONLY for c in rep["carryover"])


def test_temporal_order_earlier_first(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=T2 + 1000)
    order = rep["repos_analyzed"]
    assert order.index(A) < order.index(B)  # A (T1) precedes B (T2)


def test_no_reverse_carryover(tmp_path: Path) -> None:
    """Carryover is directional: a concept only used in B (never proven earlier)
    must not produce a B→A link."""
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=T2 + 1000)
    assert not any(c["reused_in"] == A for c in rep["carryover"])


def test_loader_missing_when_unbuilt(tmp_path: Path) -> None:
    out = _load_cross_mission(tmp_path)
    assert out["missing"] is True
    assert out["ready"] is False
    assert out["carryover"] == []


def test_loader_reads_built_artifact(tmp_path: Path) -> None:
    _seed(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=T2 + 1000)
    save_cross_mission(rep, tmp_path)
    out = _load_cross_mission(tmp_path)
    assert out["ready"] is True
    assert any(c["concept_id"] == SHARED for c in out["carryover"])


def test_render_missing_says_not_built() -> None:
    md = _render_cross_mission({"missing": True})
    assert "NOT YET BUILT" in md


def test_render_includes_carryover_and_difficulty() -> None:
    art = {
        "ready": True,
        "repos_analyzed": [A, B],
        "carryover": [{"concept_id": SHARED, "proven_in": A, "reused_in": B}],
        "recurring_across_missions": [
            {"topic": "트랜잭션", "repos": [A, B], "total_comments": 4}],
        "difficulty": [
            {"repo": A, "avg_review_rounds": 3.0, "prs_total": 1, "prs_merged": 1}],
    }
    md = _render_cross_mission(art)
    assert "carryover" in md
    assert SHARED in md
    assert "트랜잭션" in md
    assert "난이도" in md
