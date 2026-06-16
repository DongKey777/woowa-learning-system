# CORE KNOWLEDGE BASE

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

## Overview

`core/` is the runtime nucleus: daemon, routing, prompt composition, state persistence, learner telemetry, profile/mastery, and PR/live-review helpers.

## Where To Look

| Task | Location | Notes |
|---|---|---|
| Daemon lifecycle | `daemon.py` | AF_UNIX JSON server; `start-bg`, `ping`, `stop`, `status` |
| Route/mode decision | `router.py`, `intent.py`, `reformulate.py` | explicit mode before heuristic fallback |
| Prompt assembly | `coach.py`, `prompt.py`, `response.py` | citations and response hints live here |
| Lazy artifacts | `lazy_loader.py` | must honor `RouteDecision.lazy_artifacts` |
| State writes | `state.py` | atomic JSON and locked JSONL append helpers |
| Capture/quality | `response_capture.py`, `response_quality.py` | hook-first full-body capture + repair |
| Learner state | `profile.py`, `mastery.py`, `feedback.py`, `trigger.py` | Bloom/mastery progression |
| PR review data | `learner_state.py`, `pr_threads.py`, `pr_retro.py`, `peer_pr.py` | live GitHub vs archived SQLite split |
| Code/test events | `code_event.py`, `junit_ingest.py` | learner evidence ingestion |

## Conventions

- Keep changes module-specific; this package has broad fan-out through `bin/*`.
- Preserve `mode` and `mode_source` in all telemetry paths.
- State paths are repo-relative (`state/`, `corpus/`) unless explicitly overridden.
- `atomic_write_json` and `append_jsonl_locked` are the persistence norm.
- Broad exceptions often mean “fallback without blocking learner”; do not harden into user-visible failures unless the boundary is external input.
- Mtime-keyed caches are intentional cache invalidation; update tests when changing file dependency rules.
- Keep offline/archive modules separate from live GitHub modules.

## Anti-Patterns

- Do not eagerly load all lazy artifacts.
- Do not let telemetry, capture, drift checks, or profile recompute break the learner response path.
- Do not emit `참고:` for `tier_0_fallback`.
- Do not handwrite citation paths when `response_hints.citation_markdown` exists.
- Do not leak development-mode events into learner profile statistics.
- Do not introduce daemon self-recursion through search/capture paths.

## Verification

```bash
python3 -m pytest \
  tests/test_daemon.py tests/test_router.py tests/test_coach.py \
  tests/test_lazy_loader.py tests/test_response_capture.py \
  tests/test_response_quality.py tests/test_profile.py tests/test_session.py -q
```
