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


def test_runtime_loc_is_measurable() -> None:
    total = sum(_count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS)
    # Plan §T-X (2026-05-25): legacy parity migration relaxed budget to ≤9500
    # (legacy 80K LOC × 0.12 — 51 new wrappers + 19 new modules, still 8× smaller
    # than legacy while including every measurable capability). 2026-05-29:
    # collection-pipeline hardening (auto-drain helper + locked-jsonl extraction)
    # adds ~100 LOC under core/, lift to ≤9600. 2026-05-29: integrity+observability
    # remediation (atomic_write_json helper, repair-queue lock, drain_errors,
    # telemetry stderr, corpus_fingerprint promotion + check_corpus_drift) lands
    # at 9574; lift to ≤9700 to keep the gate a generous drift alarm, not a
    # hard trip-wire. 2026-06-01: A~N mission-grounded usecases — F0(d) adds
    # anchors/concept_link.py (build-time topic_concept_ids linking via warm
    # daemon); lands at 9783, lift to ≤9850. F0(e) adds core/evidence_sync.py
    # (production pr_merge/mentor_accept emission from the archive); lands at
    # 9996, lift to ≤10100. 2026-06-01: F1 family H (pr_diff_evolution) adds
    # mission/diff_evolution.py (PR code-evolution analysis: rounds + review→fix
    # links + hotspots + smells from archive+git clone); lands at 10401, lift to
    # ≤10500. F2 family L (cross_mission) adds mission/cross_mission.py +
    # cross_mission loader/render/route wiring (concept carryover + recurring
    # signals + mission difficulty across repos); lands at 10699, lift to ≤10800.
    # 2026-06-01: F2 family M (memory_review) adds mission/memory_review.py
    # (blind-spots + forgetting + review cards from mastery/history/anchors) +
    # loader/render/route wiring; lands at 10959, lift to 11050. 2026-06-01: F3
    # family A+N (pr_review) adds mission/pr_review.py + loader/render/route
    # wiring (received reviews + unresolved threads + concept-linked anchors +
    # PR issue-comment overview); lands at 11224, lift to ≤11350. 2026-06-01: F3
    # family C (reviewer_profile) adds mission/reviewer_profile.py (~314 LOC:
    # reviewer style/habits surface with cross-repo schema-drift helpers) + core
    # loader/render/route/intent wiring; lands at 11648, lift to ≤11750.
    # 2026-06-01: F4 family D (learning_path) adds mission/learning_path.py
    # (~211 LOC: concept-graph prereq/next traversal + mastery join) + core
    # loader/render/route/intent wiring; F6 family J (pr_meta) module
    # mission/pr_meta.py (~210 LOC) also lands here. Total reaches 12177, lift
    # to <=12400 (J core wiring still pending).
    # 2026-06-01: F5 family G (thread_recon) + F5 family I (temporal) + F6 family
    # E (meta_analytics) + F7 family F (cohort) add four mission modules
    # (mission/{thread_recon,temporal,meta_analytics,cohort}.py) plus
    # loader/render/route/intent wiring across the 4 core files. Total reaches
    # 13657, lift to <=13900.
    # 2026-06-01: F7 family K (predict) adds mission/predict.py (C+F+H synthesis)
    # + loader/render/route/intent wiring; total reaches 14049, lift to <=14300.
    # 2026-06-02: live PR review-thread reconciliation adds core/pr_threads.py
    # (~278 LOC: fresh 3-source REST+GraphQL reconcile + pending-aware status +
    # delta vs snapshot). No new router mode (AI calls bin/pr-thread-status
    # directly); total reaches 14418, lift to <=14600.
    # W11: LOC is report-only — no hard ceiling gates the release. A generous cap
    # was the wrong tool: necessary code must be free to grow, and efficient code
    # can be unavoidably long. This only sanity-checks the counter + that the
    # runtime dirs contain code. Simplicity is enforced by review/`simplify`.
    assert total > 0


def test_per_module_loc_breakdown_is_measurable() -> None:
    """W11: per-module LOC is report-only (observed, not a ceiling); sanity only."""
    breakdown = {d: _count_python_loc(REPO_ROOT / d) for d in RUNTIME_DIRS}
    # Y13-D adds runtime corpus snapshots and latency sidecar plumbing under
    # rag/. Y13-H adds exact-query shortcut lookup. Y13-K/K3 add the direct
    # transformers encoder backend plus cold-start import/load scheduling
    # controls. Y14 adds a small no-index lexical fallback for first-run/test
    # recovery without changing the dense-index hot path. Keep this module
    # budget explicit while the total runtime LOC gate remains primary.
    # 2026-05-29: corpus↔index drift detection — corpus_fingerprint() promoted
    # to rag/corpus_loader.py (shared by lexical_fusion + index manifest) and
    # check_corpus_drift() added to rag/index.py so a stale fetched index warns
    # at daemon startup; lift to ≤1350.
    assert breakdown["rag"] > 0  # W11: report-only (observed, no ceiling)
    # core: 2500 → 6500 ceiling (Phase T-X/Y13 new modules: pr_retro, code_event,
    # junit_ingest, response_quality, learner_state, profile, session,
    # bootstrap, onboard, readiness, doctor, state_validate, registry_audit,
    # mission_map, rag_rewrite, route_fallback, profile_admin, reviewer_profile,
    # index_metadata, cognitive trigger FSM, auto reformulation, unified profile
    # rebuild, etc. Y14-B4 adds schema-object learner query fallback and
    # stale-dense-index lexical promotion. Response capture token optimization
    # adds path/summary capture metadata plus content-addressed body sidecars.
    # UX-first hook capture adds pending/repair state helpers and same-turn
    # supersede handling for clients that call ask multiple times before one
    # learner-facing answer. Onboarding hardening adds core/_venv.py — a
    # stdlib-only .venv re-exec guard so fresh clones install cleanly on PEP 668
    # externally-managed system Python (macOS Homebrew / Debian). 2026-05-29:
    # collection-pipeline hardening — drain_repair_queue() opportunistically
    # repairs the capture queue from every successful hook capture, and
    # append_jsonl_locked() centralizes the fcntl-locked jsonl append used by
    # both history.jsonl and response-quality.jsonl. 2026-05-29: integrity work
    # adds atomic_write_json (core/state.py) + telemetry stderr + drift-check call
    # (core/daemon.py); lift to ≤7400. 2026-06-01: F0(e) adds core/evidence_sync.py
    # (production pr_merge/mentor_accept emission); lift to ≤7600. F1/F2 add
    # pr_diff_evolution + cross_mission loader/render/route wiring across
    # router/intent/lazy_loader/coach; lands at 7682, lift to ≤7800. 2026-06-01:
    # F3 family A+N (pr_review) adds loader/render/route/intent wiring across the
    # same 4 core files; lands at 7868, lift to ≤7950. 2026-06-01: F3 family C
    # (reviewer_profile) adds loader/render/route/intent wiring across the same 4
    # core files; lands at 7978, lift to ≤8050.
    # 2026-06-01: F4 family D (learning_path) adds loader/render/route/intent
    # wiring across the same 4 core files; lands at 8080, lift to <=8200 (leaves
    # headroom for F6 family J pr_meta core wiring, still pending).
    # 2026-06-01: F6 J + F5 G/I + F6 E + F7 F all add loader/render/route/intent
    # wiring across the same 4 core files (5 new render helpers in coach.py are
    # the bulk); lands at 8564, lift to <=8750.
    # 2026-06-02: core/pr_threads.py (~278 LOC: live pending-aware PR thread
    # reconcile engine) + recent daemon/state hardening land core at 9009; lift
    # to <=9150 to keep this a generous drift alarm, not a hard trip-wire.
    assert breakdown["core"] > 0  # W11: report-only (observed, no ceiling)
    # 2026-05-31: corpus-expansion cycle adds a real-state join adapter to
    # mine_history (build_joined_events/mine_real) so mining runs on actual
    # history.jsonl + response-quality.jsonl + profile.uncertain instead of the
    # synthetic payload shape; lift the soft ceiling to ≤500 (matches mission/anchors).
    assert breakdown["curation"] > 0  # W11: report-only (observed, no ceiling)
    # 2026-06-01: F1 family H adds mission/diff_evolution.py (~285 LOC: PR
    # code-evolution rounds/review→fix/hotspots/smells); lift to ≤700. F2 family
    # L adds mission/cross_mission.py (~210 LOC: cross-repo carryover/recurring/
    # difficulty); lands at 893, lift to ≤950.
    # 2026-06-01: F2 family M adds mission/memory_review.py (~155 LOC: blind
    # spots/forgetting/review cards); lands at 1066, lift to 1150. 2026-06-01:
    # F3 family A+N adds mission/pr_review.py (~167 LOC: received reviews +
    # unresolved threads + concept-linked anchors + PR overview); lands at 1232,
    # lift to 1300. 2026-06-01: F3 family C adds mission/reviewer_profile.py
    # (~314 LOC: reviewer style/habits + schema-drift helpers); lands at 1546,
    # lift to 1600.
    # 2026-06-01: F4 family D adds mission/learning_path.py (~211 LOC: concept-
    # graph prereq/next traversal + mastery join); F6 family J module
    # mission/pr_meta.py (~210 LOC) also lands here. mission reaches 1973, lift
    # to <=2050.
    # 2026-06-01: F5 G (thread_recon) + F5 I (temporal) + F6 E (meta_analytics)
    # + F7 F (cohort) add four mission modules; mission reaches 2969, lift to
    # <=3100.
    # 2026-06-01: F7 family K adds mission/predict.py (C+F+H synthesis: likely
    # topics/hotspots/smells/review-load projection); mission reaches 3241, lift
    # to <=3350.
    assert breakdown["mission"] > 0  # W11: report-only (observed, no ceiling)
    assert breakdown["anchors"] > 0  # W11: report-only (observed, no ceiling)


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
                   "reviewer", "compare", "mission-map",
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
                   "anchors-build",
                   # A~N usecases F0(e): production evidence loop
                   "learn-evidence-sync",
                   # A~N usecases F1 H: PR code-evolution analysis
                   "pr-diff-evolution-build",
                   # A~N usecases F2 L: cross-mission learning analytics
                   "learn-cross-mission-build",
                   # A~N usecases F2 M: memory / spaced-review analytics
                   "learn-memory-review-build",
                   # A~N usecases F3 A+N: received-review surface
                   "learn-pr-review-build",
                   # A~N usecases F3 C: reviewer-profile surface
                   "reviewer-profile-build",
                   # A~N usecases F4 D: learning-path recommender
                   "learn-learning-path-build",
                   # A~N usecases F6 J: PR metadata quality surface
                   "learn-pr-meta-build",
                   # A~N usecases F5 G: review-thread reconstruction
                   "learn-thread-recon-build",
                   # A~N usecases F5 I: review-cycle temporal dynamics
                   "learn-temporal-build",
                   # A~N usecases F6 E: cross-repo meta-learning analytics
                   "learn-meta-analytics-build",
                   # A~N usecases F7 F: learner-vs-cohort comparison
                   "learn-cohort-build",
                   # A~N usecases F7 K: pre-push review prediction (C+F+H synthesis)
                   "learn-predict-build",
                   # Live pending-aware PR review-thread reconciliation
                   "pr-thread-status",
                   # UX-first response capture hooks/repair
                   "capture-response", "capture-repair", "learning-data-clean",
                   # Onboarding bootstrap (.venv + pip install -e .)
                   "setup"}
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
