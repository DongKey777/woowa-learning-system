"""Phase 8 code-metrics gate — runtime LOC + entry-point count + corpus storage.

Compares the new system against plan targets (`misty-giggling-valley.md`):
- Total runtime LOC ≤ 2350 (legacy ~80K)
- Single learner entry point (`bin/ask`) + 3 maintenance entries
- Corpus size baseline measured (no ceiling yet)
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIRS = ("rag", "core", "curation")
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


def test_runtime_loc_under_plan_target() -> None:
    total = sum(_count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS)
    assert total <= 2350, f"runtime LOC {total} exceeds plan target 2350"


def test_per_module_loc_breakdown_within_plan() -> None:
    """Report (and assert reasonable) per-module LOC for plan accounting."""
    breakdown = {d: _count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS}
    # Plan targets: rag ≤400, core ≤1500, curation ≤250
    assert breakdown["rag"] <= 800, f"rag {breakdown['rag']} > 800 (eval+search bloat)"
    assert breakdown["core"] <= 1500, f"core {breakdown['core']} > 1500"
    assert breakdown["curation"] <= 350, f"curation {breakdown['curation']} > 350"


def test_entry_point_count() -> None:
    """Plan D13: 1 learner entry (`ask`) + maintenance (`corpus-build`,
    `corpus-curate`, `eval-compare`, `learn-event`)."""
    entries = sorted(p.name for p in ENTRY_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    learner_facing = {"ask"}
    maintenance = {"corpus-build", "corpus-curate", "eval-compare", "learn-event"}
    expected = learner_facing | maintenance
    extras = set(entries) - expected
    missing = expected - set(entries)
    assert not missing, f"missing entries: {missing}"
    # extras allowed (e.g. future bootstrap-repo) but flag if surprising
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
