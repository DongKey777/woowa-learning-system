# MISSION PACKAGE KNOWLEDGE BASE

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

## Overview

`mission/` is system-side analytics for learner mission repos. It reads archives, learner state, and Java code snapshots; it writes derived artifacts under `state/repos/<repo>/`.

## Where To Look

| Task | Location | Notes |
|---|---|---|
| Java concept extraction | `extract.py` | produces `mission_patterns.json` |
| Concept graph gaps | `graph.py` | prereq and back-reference logic |
| PR review analysis | `pr_review.py`, `thread_recon.py`, `diff_evolution.py` | archive-backed review modes |
| Cross-mission trends | `cross_mission.py`, `memory_review.py`, `learning_path.py` | profile/mastery aware |
| Reviewer/cohort/meta | `reviewer_profile.py`, `cohort.py`, `pr_meta.py`, `temporal.py`, `meta_analytics.py`, `predict.py` | advanced Mode A artifacts |

## Conventions

- Treat this directory as Mode B system code, not learner workspace code.
- Read learner repos and archives; write only derived artifacts through documented builders.
- Artifact paths belong under `state/repos/<repo>/`; do not require tracked outputs.
- Keep GitHub/archive assumptions explicit; stale archive and live PR status are different sources.
- Prefer small deterministic heuristics over broad LLM-dependent behavior.

## Anti-Patterns

- Do not edit `missions/<repo>` Java files from this package.
- Do not assume a learner repo exists until onboarding/readiness checks pass.
- Do not silently treat missing archives as “no issues”; surface missing data in artifacts.
- Do not hand-edit `state/repos/<repo>/mission_patterns.json`; rebuild it.

## Commands

```bash
bin/mission-patterns-build --repo <repo>
bin/cross-crew-build --repo <repo>
python3 -m pytest tests/test_mission.py -q
```
