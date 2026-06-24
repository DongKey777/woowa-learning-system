"""Tests for core.mastery — Bloom 4-level + cross-axis evidence."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.mastery import (
    BLOOM_LEVELS,
    EVIDENCE_WEIGHTS,
    compact_all,
    get,
    list_by_level,
    promote,
    record_evidence,
    summary,
)


def test_record_evidence_creates_attempted(tmp_path: Path) -> None:
    record_evidence("spring/bean", "mission_use", state_root=tmp_path)
    entry = get("spring/bean", state_root=tmp_path)
    assert entry is not None
    assert entry.bloom_level == "attempted"
    assert entry.evidence_count == 1


def test_unknown_source_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown evidence source"):
        record_evidence("spring/bean", "made_up", state_root=tmp_path)


def test_familiar_threshold_3_events_2_sources_within_14d(tmp_path: Path) -> None:
    now = 1_000_000.0
    record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=now - 1)
    record_evidence("spring/bean", "self_assess", state_root=tmp_path, ts=now - 2)
    record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=now - 3)
    assert promote("spring/bean", state_root=tmp_path, now=now) == "familiar"


def test_familiar_requires_distinct_sources(tmp_path: Path) -> None:
    now = 1_000_000.0
    for i in range(5):
        record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=now - i)
    # 5 events but all single source → still attempted
    assert promote("spring/bean", state_root=tmp_path, now=now) == "attempted"


def test_dedup_key_makes_record_idempotent(tmp_path: Path) -> None:
    eid1 = record_evidence("spring/bean", "pr_merge", state_root=tmp_path,
                           dedup_key="pr_merge:r:1:spring/bean")
    eid2 = record_evidence("spring/bean", "pr_merge", state_root=tmp_path,
                           dedup_key="pr_merge:r:1:spring/bean")
    assert eid1 == eid2  # second call short-circuits to the prior row
    assert get("spring/bean", state_root=tmp_path).evidence_count == 1


def test_dedup_key_distinct_keys_both_recorded(tmp_path: Path) -> None:
    record_evidence("spring/bean", "pr_merge", state_root=tmp_path, dedup_key="k1")
    record_evidence("spring/bean", "mentor_accept", state_root=tmp_path, dedup_key="k2")
    assert get("spring/bean", state_root=tmp_path).evidence_count == 2


def test_proficient_requires_pr_merge_and_mentor_accept(tmp_path: Path) -> None:
    now = 1_000_000.0
    record_evidence("spring/bean", "pr_merge", state_root=tmp_path, ts=now - 100)
    assert promote("spring/bean", state_root=tmp_path, now=now) == "attempted"
    record_evidence("spring/bean", "mentor_accept", state_root=tmp_path, ts=now - 50)
    assert promote("spring/bean", state_root=tmp_path, now=now) == "proficient"


def test_mastered_requires_proficient_plus_strong_drill(tmp_path: Path) -> None:
    now = 1_000_000.0
    record_evidence("spring/bean", "pr_merge", state_root=tmp_path, ts=now - 100)
    record_evidence("spring/bean", "mentor_accept", state_root=tmp_path, ts=now - 50)
    record_evidence("spring/bean", "drill_score", score=0.85, state_root=tmp_path, ts=now - 10)
    assert promote("spring/bean", state_root=tmp_path, now=now) == "mastered"


def test_mastered_via_proficient_plus_retained_self_assess(tmp_path: Path) -> None:
    now = 1_000_000.0
    older = now - 31 * 24 * 3600  # 31일 전 self_assess
    record_evidence("spring/bean", "self_assess", state_root=tmp_path, ts=older)
    record_evidence("spring/bean", "pr_merge", state_root=tmp_path, ts=now - 100)
    record_evidence("spring/bean", "mentor_accept", state_root=tmp_path, ts=now - 50)
    assert promote("spring/bean", state_root=tmp_path, now=now) == "mastered"


def test_weak_drill_does_not_promote_to_mastered(tmp_path: Path) -> None:
    now = 1_000_000.0
    record_evidence("spring/bean", "pr_merge", state_root=tmp_path, ts=now - 100)
    record_evidence("spring/bean", "mentor_accept", state_root=tmp_path, ts=now - 50)
    record_evidence("spring/bean", "drill_score", score=0.5, state_root=tmp_path, ts=now - 10)
    # weight 0.35 < 0.55 threshold
    assert promote("spring/bean", state_root=tmp_path, now=now) == "proficient"


def test_promote_persists_trace(tmp_path: Path) -> None:
    now = 1_000_000.0
    record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=now - 3)
    record_evidence("spring/bean", "self_assess", state_root=tmp_path, ts=now - 2)
    record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=now - 1)
    promote("spring/bean", state_root=tmp_path, now=now)
    entry = get("spring/bean", state_root=tmp_path)
    assert entry.bloom_level == "familiar"
    assert entry.promotion_trace[0]["from"] == "attempted"
    assert entry.promotion_trace[0]["to"] == "familiar"


def test_summary_counts_per_level(tmp_path: Path) -> None:
    now = 1_000_000.0
    # one attempted
    record_evidence("spring/a", "mission_use", state_root=tmp_path, ts=now - 1)
    # one familiar — distinct sources required
    for src in ("mission_use", "self_assess", "mission_use"):
        record_evidence("spring/b", src, state_root=tmp_path, ts=now - 1)
    # one proficient
    record_evidence("spring/c", "pr_merge", state_root=tmp_path, ts=now - 1)
    record_evidence("spring/c", "mentor_accept", state_root=tmp_path, ts=now - 1)
    compact_all(state_root=tmp_path, now=now)
    s = summary(state_root=tmp_path)
    assert s["counts"]["attempted"] == 1
    assert s["counts"]["familiar"] == 1
    assert s["counts"]["proficient"] == 1
    assert s["counts"]["mastered"] == 0
    assert s["total_tracked"] == 3


def test_list_by_level(tmp_path: Path) -> None:
    now = 1_000_000.0
    record_evidence("spring/a", "mission_use", state_root=tmp_path, ts=now - 1)
    record_evidence("spring/b", "mission_use", state_root=tmp_path, ts=now - 1)
    assert sorted(list_by_level("attempted", state_root=tmp_path)) == ["spring/a", "spring/b"]


def test_evidence_weights_complete() -> None:
    """All BLOOM_LEVELS literals + EVIDENCE_WEIGHTS keys are documented set."""
    assert set(EVIDENCE_WEIGHTS) == {"pr_merge", "mentor_accept", "drill_score", "mission_use", "self_assess"}
    assert set(BLOOM_LEVELS) == {"attempted", "familiar", "proficient", "mastered"}


def test_get_returns_none_when_missing(tmp_path: Path) -> None:
    assert get("nope", state_root=tmp_path) is None


def test_familiar_window_excludes_old_events(tmp_path: Path) -> None:
    now = 1_000_000.0
    old = now - 15 * 24 * 3600  # 15일 전, 14d window 밖
    record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=old)
    record_evidence("spring/bean", "self_assess", state_root=tmp_path, ts=old)
    record_evidence("spring/bean", "mission_use", state_root=tmp_path, ts=old)
    assert promote("spring/bean", state_root=tmp_path, now=now) == "attempted"


def test_record_evidence_last_seen_at_keeps_max_not_last_written(tmp_path) -> None:
    from core.mastery import get
    # A later-arriving OLDER event must not regress recency: sync_repo emits
    # pr_merge(merged_at, later) then mentor_accept(reply ts, earlier) for one concept.
    record_evidence("c/x", "mission_use", state_root=tmp_path, ts=5000.0)
    record_evidence("c/x", "mentor_accept", state_root=tmp_path, ts=1000.0)
    assert get("c/x", state_root=tmp_path).last_seen_at == 5000.0


def test_connection_sets_busy_timeout(tmp_path) -> None:
    # Without busy_timeout a concurrent writer's lock makes the loser raise
    # 'database is locked' immediately (default 0); the daemon swallowed that →
    # lost evidence. 5s wait lets the loser succeed.
    from core.mastery import _connect
    with _connect(state_root=tmp_path) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_record_evidence_dedup_no_duplicate_under_concurrency(tmp_path) -> None:
    # Concurrent same-dedup_key inserts must produce exactly one row (the
    # SELECT-then-INSERT used to race → duplicates). BEGIN IMMEDIATE + busy_timeout
    # serialize the check-then-insert.
    import threading

    from core.mastery import _connect, record_evidence
    record_evidence("c/seed", "mission_use", state_root=tmp_path)  # init schema
    results, barrier = [], threading.Barrier(4)

    def _ins() -> None:
        barrier.wait()
        results.append(record_evidence("c/dup", "pr_merge", state_root=tmp_path, dedup_key="K1"))

    threads = [threading.Thread(target=_ins) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with _connect(tmp_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE json_extract(payload_json,'$.dedup_key')='K1'"
        ).fetchone()[0]
    assert n == 1
    assert len(set(results)) == 1  # all callers got the same id
