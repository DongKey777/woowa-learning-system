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
    assert total <= 4000, f"runtime LOC {total} exceeds paradigm-v2 budget 4000"


def test_per_module_loc_breakdown_within_plan() -> None:
    """Plan §D-I — per-module budget after paradigm-v2 expansion."""
    breakdown = {d: _count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS}
    assert breakdown["rag"] <= 800, f"rag {breakdown['rag']} > 800"
    assert breakdown["core"] <= 2200, f"core {breakdown['core']} > 2200 (paradigm-v2 + Phase H daemon ask action)"
    assert breakdown["curation"] <= 350, f"curation {breakdown['curation']} > 350"
    assert breakdown["mission"] <= 500, f"mission {breakdown['mission']} > 500"
    assert breakdown["anchors"] <= 500, f"anchors {breakdown['anchors']} > 500"


def test_entry_point_count() -> None:
    """Plan D13: 1 learner entry (`ask`) + maintenance entries."""
    entries = sorted(p.name for p in ENTRY_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    learner_facing = {"ask"}
    maintenance = {"corpus-build", "corpus-curate", "eval-compare", "learn-event",
                   "graph-build", "phase9-gate",
                   "mission-patterns-build", "cross-crew-build"}
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
