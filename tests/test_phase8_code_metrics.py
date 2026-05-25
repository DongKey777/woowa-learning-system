"""Phase 8 code-metrics gate (paradigm-v2 updated budgets).

Plan misty-giggling-valley.md §D-I — 사용자 동의 자유 확장. paradigm-v2 added:
- mission/ (F10 forward + graph)
- anchors/ (F11 4-stage)
- core/{router,lazy_loader,coach,feedback,mastery}.py
Per-feature ≤500 LOC; total runtime ≤4000 (vs legacy 80K = -95%).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIRS = ("rag", "core", "curation", "mission", "anchors")
ENTRY_DIR = REPO_ROOT / "bin"
CORPUS_DIR = REPO_ROOT / "corpus" / "concepts"


def _count_python_loc(root: Path) -> int:
    total = 0
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        with p.open(encoding="utf-8") as f:
            total += sum(1 for _ in f)
    return total


def test_runtime_loc_under_paradigm_v2_budget() -> None:
    total = sum(_count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS)
    # Plan §T-X (2026-05-25): legacy parity migration relaxed budget to ≤9500
    # (legacy 80K LOC × 0.12 — 51 new wrappers + 19 new modules, still 8× smaller
    # than legacy while including every measurable capability).
    assert total <= 9500, f"runtime LOC {total} exceeds Phase T-X budget 9500"


def test_per_module_loc_breakdown_within_plan() -> None:
    """Plan §T-X — per-module budget tracks 51-wrapper expansion."""
    breakdown = {d: _count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS}
    assert breakdown["rag"] <= 800, f"rag {breakdown['rag']} > 800"
    # core: 2500 → 5000 ceiling (Phase T-X new modules: pr_retro, code_event,
    # junit_ingest, response_quality, learner_state, profile, session,
    # bootstrap, onboard, readiness, doctor, state_validate, registry_audit,
    # mission_map, rag_rewrite, route_fallback, profile_admin, reviewer_profile,
    # index_metadata, etc.)
    assert breakdown["core"] <= 5000, f"core {breakdown['core']} > 5000 (Phase T-X budget)"
    assert breakdown["curation"] <= 350, f"curation {breakdown['curation']} > 350"
    assert breakdown["mission"] <= 500, f"mission {breakdown['mission']} > 500"
    assert breakdown["anchors"] <= 500, f"anchors {breakdown['anchors']} > 500"


def test_entry_point_count() -> None:
    """Plan D13: 1 learner entry (`ask`) + maintenance entries."""
    entries = sorted(p.name for p in ENTRY_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    learner_facing = {"ask"}
    maintenance = {"corpus-build", "corpus-curate", "eval-compare", "learn-event",
                   "graph-build", "phase9-gate",
                   "mission-patterns-build", "cross-crew-build",
                   "index-fetch",
                   # Phase T-X new wrappers
                   "learn-pr-retro"}
    expected = learner_facing | maintenance
    extras = set(entries) - expected
    missing = expected - set(entries)
    assert not missing, f"missing entries: {missing}"
    assert len(extras) <= 2, f"too many extras: {extras}"


def test_corpus_concept_count_matches_phase0() -> None:
    """3199 v3 concept JSONs (Phase 0b spot-check)."""
    n = sum(1 for _ in CORPUS_DIR.rglob("*.json"))
    assert n == 3199, f"expected 3199 concepts, got {n}"


def test_corpus_size_under_60mb_baseline() -> None:
    """Snapshot corpus size for Phase 9 dominate gate (legacy 260MB+)."""
    total = sum(p.stat().st_size for p in CORPUS_DIR.rglob("*.json"))
    mb = total / 1024 / 1024
    assert mb < 60, f"corpus {mb:.1f}MB > baseline 60MB"
