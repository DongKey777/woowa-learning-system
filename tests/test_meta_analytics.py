"""Tests for mission.meta_analytics — F2 family E (meta_analytics)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mission.meta_analytics import analyze, save_meta_analytics

LEARNER = "donkey"
A = "spring-roomescape-waiting"
B = "spring-roomescape-member"

STICKY = "spring/bean-di-basics"          # asked 3× across two repos
SECOND = "database/lock-basics"           # asked 2× → also repeated
ONCE = "spring/mvc-controller-basics"     # asked once → excluded

NOW = 1_700_000_000.0

EXPECTED_KEYS = {
    "learner_login", "status", "repeated_concepts", "mode_mix",
    "quality_trend", "mastery_distribution", "counts", "generated_at",
}


def _ask(repo, mode, concepts, ts=NOW):
    return json.dumps({
        "event_id": f"ask-{ts}-{mode}-{concepts[0] if concepts else 'x'}",
        "ts": ts,
        "event_type": "rag_ask",
        "mode": "learning",
        "learner_id": LEARNER,
        "repo": repo,
        "payload": {
            "prompt": "질문",
            "repo": repo,
            "router_mode": mode,
            "router_reason": "test",
            "top_concept_ids": concepts,
            "latency_ms": 100.0,
        },
    }, ensure_ascii=False)


def _write_history(state_root: Path, lines: list[str]) -> None:
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    (d / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_quality(state_root: Path, rows: list[dict]) -> None:
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    (d / "response-quality.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


def _seed_history(state_root: Path) -> None:
    """STICKY asked 3× (A,A,B), SECOND asked 2×, ONCE asked 1× (excluded).
    Includes blank + garbled lines that must be skipped defensively."""
    lines = [
        _ask(A, "cs_qa", [STICKY, SECOND]),
        "",                                   # blank — skipped
        _ask(A, "cs_qa", [STICKY]),
        "{ this is not json",                 # garbled — skipped
        _ask(B, "coaching", [STICKY]),
        _ask(B, "coaching", [SECOND]),
        _ask(A, "code_review", [ONCE]),
    ]
    _write_history(state_root, lines)


def _patch_mastery(monkeypatch, counts: dict, total: int) -> None:
    from core import mastery
    monkeypatch.setattr(
        mastery, "summary",
        lambda state_root=None: {"counts": counts, "total_tracked": total},
    )


# --- tests -----------------------------------------------------------------

def test_missing_history(tmp_path: Path) -> None:
    rep = analyze(LEARNER, tmp_path, now=NOW)
    assert rep["status"] == "missing_history"
    assert rep["repeated_concepts"] == []
    assert rep["mode_mix"] == []
    assert rep["counts"]["rag_ask_events"] == 0


def test_repeated_concepts_counts_and_excludes_once(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    assert rep["status"] == "ok"
    by_id = {c["concept_id"]: c for c in rep["repeated_concepts"]}
    assert by_id[STICKY]["times_asked"] == 3        # asked 3×
    assert by_id[SECOND]["times_asked"] == 2        # asked 2× → included
    assert ONCE not in by_id                         # asked once → excluded
    # descending by count, STICKY (3) ahead of SECOND (2)
    order = [c["concept_id"] for c in rep["repeated_concepts"]]
    assert order.index(STICKY) < order.index(SECOND)


def test_repeated_concepts_repos_unique_sorted(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    by_id = {c["concept_id"]: c for c in rep["repeated_concepts"]}
    # STICKY asked in A (twice) + B (once) → unique repos, sorted ascending
    assert by_id[STICKY]["repos"] == sorted({A, B})


def test_repeated_concepts_no_repo_label(tmp_path: Path) -> None:
    lines = [_ask(None, "cs_qa", [STICKY]), _ask(None, "cs_qa", [STICKY])]
    _write_history(tmp_path, lines)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    by_id = {c["concept_id"]: c for c in rep["repeated_concepts"]}
    assert by_id[STICKY]["repos"] == ["(no repo)"]


def test_mode_mix_pct_sums_and_descending(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    mm = rep["mode_mix"]
    # 5 rag_ask events: cs_qa 2, coaching 2, code_review 1
    counts = {r["mode"]: r["n"] for r in mm}
    assert counts == {"cs_qa": 2, "coaching": 2, "code_review": 1}
    assert sum(r["n"] for r in mm) == 5
    assert abs(sum(r["pct"] for r in mm) - 100.0) < 0.5
    ns = [r["n"] for r in mm]
    assert ns == sorted(ns, reverse=True)            # descending n


def test_quality_trend_flag_aggregation(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    _write_quality(tmp_path, [
        {"quality_flags": ["extra_citation"], "contract_flags": []},
        {"quality_flags": ["extra_citation"],
         "contract_flags": ["missing_mode_header"]},
        {"quality_flags": [], "contract_flags": ["missing_mode_header"]},
    ])
    rep = analyze(LEARNER, tmp_path, now=NOW)
    qt = rep["quality_trend"]
    assert qt["total_quality_events"] == 3
    fc = {r["flag"]: r["n"] for r in qt["flag_counts"]}
    assert fc == {"extra_citation": 2, "missing_mode_header": 2}
    # descending n; ties broken alpha → flag list is ordered
    ns = [r["n"] for r in qt["flag_counts"]]
    assert ns == sorted(ns, reverse=True)


def test_quality_trend_missing_file(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    assert rep["quality_trend"] == {"total_quality_events": 0, "flag_counts": []}


def test_mastery_distribution_passthrough(tmp_path: Path, monkeypatch) -> None:
    _seed_history(tmp_path)
    _patch_mastery(monkeypatch,
                   {"attempted": 5, "familiar": 3, "proficient": 2,
                    "mastered": 1}, 11)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    assert rep["mastery_distribution"] == {
        "attempted": 5, "familiar": 3, "proficient": 2, "mastered": 1,
        "total": 11,
    }


def test_counts_block(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    c = rep["counts"]
    assert c["rag_ask_events"] == 5
    # distinct concepts asked: STICKY, SECOND, ONCE = 3
    assert c["distinct_concepts_asked"] == 3
    # repeated (≥2): STICKY, SECOND = 2
    assert c["repeated_concepts"] == 2


def test_save_round_trip_and_key_parity(tmp_path: Path, monkeypatch) -> None:
    _seed_history(tmp_path)
    _patch_mastery(monkeypatch,
                   {"attempted": 1, "familiar": 0, "proficient": 0,
                    "mastered": 0}, 1)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    assert set(rep.keys()) == EXPECTED_KEYS          # 8-key parity
    assert len(EXPECTED_KEYS) == 8
    out = save_meta_analytics(rep, tmp_path)
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded == rep                              # round-trip


def test_generated_at_is_iso(tmp_path: Path) -> None:
    _seed_history(tmp_path)
    rep = analyze(LEARNER, tmp_path, now=NOW)
    from datetime import datetime
    # parses as ISO8601 without raising
    datetime.fromisoformat(rep["generated_at"])
