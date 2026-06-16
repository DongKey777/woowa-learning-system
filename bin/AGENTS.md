# BIN KNOWLEDGE BASE

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

## Overview

`bin/` is the public command surface. Most files are thin extensionless wrappers that anchor `REPO_ROOT`, add it to `sys.path`, parse CLI flags, then delegate to `core.*`, `rag.*`, `mission.*`, or another `bin/*` command.

## Where To Look

| Task | Location | Notes |
|---|---|---|
| Learner ask | `ask` | hybrid shell/Python daemon client; fastest path uses `nc -U` |
| Daemon lifecycle | `rag-daemon` | delegates to `core.daemon.main()` |
| First run | `setup`, `bootstrap`, `index-fetch` | venv, release index, daemon start |
| Legacy coach schema | `coach-run` | adapts daemon output into `coach-run.json` |
| Prompt aliases | `coach`, `topic`, `my-pr`, `compare`, `reviewer-profile` | wrappers, not new behavior |
| Repo onboarding | `onboard-repo`, `sync-prs`, `bootstrap-repo` | fixed artifact refresh order |
| Telemetry | `learn-*`, `capture-*`, `profile-recompute` | write learner state/history |
| Gates | `doctor`, `validate-state`, `repo-readiness`, `phase9-gate` | health and release checks |

## Wrapper Families

- Transport/runtime: `ask`, `rag-daemon`, `coach-run`.
- Onboarding/ops: `bootstrap`, `onboard-repo`, `sync-prs`, `index-fetch`, `doctor`.
- Telemetry/state: `learn-event`, `learn-record-code`, `learn-response-quality`, `capture-response`, `capture-repair`.
- Analytics builders: `*-build`, `*-eval`, `*-audit`, `*-mine`.
- Compatibility aliases: `coach`, `topic`, `my-pr`, `compare`, `reviewer-profile`.

## Conventions

- Keep wrappers thin. Move logic into importable modules when behavior grows.
- Preserve repo-relative paths; commands must work from arbitrary CWD.
- Keep `--silent` usable for automation.
- New learner-facing commands should fit `docs/bin-reference.md` and Mode A/B rules.
- Use `bin/setup --dev` before verification requiring pytest/pandas/pylance.

## Anti-Patterns

- Do not bypass release-index flow from learner commands; `bin/corpus-build` is maintainer-only.
- Do not change `sync-prs` artifact order casually; predict/build steps depend on earlier artifacts.
- Do not allow multiple response body sources in `learn-response-quality`.
- Do not turn wrapper aliases into divergent implementations.
- Do not hard-require daemon for commands that are documented as offline diagnostics.

## Verification

```bash
python3 -m pytest tests/test_phase8_code_metrics.py tests/test_response_ask.py -q
python3 tests/benchmarks/release_acceptance.py
```
