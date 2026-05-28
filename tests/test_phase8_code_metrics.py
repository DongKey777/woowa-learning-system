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
    # Y13-D adds runtime corpus snapshots and latency sidecar plumbing under
    # rag/. Y13-H adds exact-query shortcut lookup. Y13-K/K3 add the direct
    # transformers encoder backend plus cold-start import/load scheduling
    # controls. Y14 adds a small no-index lexical fallback for first-run/test
    # recovery without changing the dense-index hot path. Keep this module
    # budget explicit while the total runtime LOC gate remains primary.
    assert breakdown["rag"] <= 1240, f"rag {breakdown['rag']} > 1240"
    # core: 2500 → 6500 ceiling (Phase T-X/Y13 new modules: pr_retro, code_event,
    # junit_ingest, response_quality, learner_state, profile, session,
    # bootstrap, onboard, readiness, doctor, state_validate, registry_audit,
    # mission_map, rag_rewrite, route_fallback, profile_admin, reviewer_profile,
    # index_metadata, cognitive trigger FSM, auto reformulation, unified profile
    # rebuild, etc. Y14-B4 adds schema-object learner query fallback and
    # stale-dense-index lexical promotion. Response capture token optimization
    # adds path/summary capture metadata plus content-addressed body sidecars.
    assert breakdown["core"] <= 6600, f"core {breakdown['core']} > 6600 (Phase T-X/Y13/Y14 budget)"
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
                   "index-fetch", "index-pack",
                   # Phase T new wrappers
                   "learn-pr-retro", "learn-record-code", "learn-test",
                   "learn-response-quality", "assess-learner-state",
                   "profile-recompute", "session-start",
                   # Phase U new wrappers
                   "bootstrap", "bootstrap-repo", "onboard-repo", "list-repos",
                   "archive-status", "sync-prs", "repo-readiness", "doctor",
                   "validate-state", "registry-audit",
                   # Phase V new wrappers (coaching context)
                   "coach-run", "coach", "my-pr", "next-action", "topic",
                   "reviewer", "compare", "compose-response", "mission-map",
                   "rag-rewrite-prepare", "rag-route-fallback",
                   "chunk-context-prepare",
                   # Phase W new wrappers (mining/analytics)
                   "feedback-mine", "response-quality-mine", "routing-analyze",
                   "learning-turn-audit", "learning-path-graph-audit",
                   "reclassify-history", "cohort-eval", "cohort-compare",
                   "golden", "rag-eval", "router-generalization-eval",
                   "learner-log-rag-eval",
                   # Phase X new wrappers (maintenance + sub-commands)
                   "sync-index-metadata", "drill-grade-prepare", "learn-feedback",
                   "learn-self-assess", "learn-drill", "learner-profile",
                   "set-profile", "show-profile", "reviewer-profile",
                   "rag-remote-build",
                   # Phase Y6 onboarding chain fix (anchors-build wrapper)
                   "anchors-build"}
    expected = learner_facing | maintenance
    extras = set(entries) - expected
    missing = expected - set(entries)
    assert not missing, f"missing entries: {missing}"
    assert len(extras) <= 2, f"too many extras: {extras}"


def test_corpus_concept_count_matches_phase0() -> None:
    """Corpus expansion must not regress below the Phase 0b baseline."""
    n = sum(1 for _ in CORPUS_DIR.rglob("*.json"))
    assert n >= 3199, f"expected at least 3199 concepts, got {n}"


def test_corpus_size_under_60mb_baseline() -> None:
    """Snapshot corpus size for Phase 9 dominate gate (legacy 260MB+)."""
    total = sum(p.stat().st_size for p in CORPUS_DIR.rglob("*.json"))
    mb = total / 1024 / 1024
    assert mb < 60, f"corpus {mb:.1f}MB > baseline 60MB"
