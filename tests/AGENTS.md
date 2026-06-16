# TESTS KNOWLEDGE BASE

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

## Overview

`tests/` contains pytest unit/behavior tests and standalone benchmark/release-gate scripts. Benchmark scripts are executed directly with Python; they are not normal pytest tests.

## Structure

```text
tests/
├── test_*.py        # pytest modules
├── benchmarks/      # direct Python gates and scenario measurements
└── fixtures/        # qrels, golden queries, small JSON fixtures
```

## Where To Look

| Task | Location | Notes |
|---|---|---|
| Full unit suite | `test_*.py` | `python3 -m pytest tests/ -q` |
| Release gate | `benchmarks/release_acceptance.py` | aggregate gate with timeouts |
| Benchmark chain | `benchmarks/phase_y_all_benches.py` | meta runner |
| RAG quality | `benchmarks/rag_quality_regression.py` | qrels-backed regression |
| Latency | `benchmarks/daemon_latency.py` | cold probes are opt-in expensive |
| Fixtures | `fixtures/*.json` | qrels/golden input data |

## Conventions

- No shared `conftest.py`; fixtures are mostly local per module.
- Tests commonly build temp git repos, sqlite archives, JSONL logs, JUnit XML, sockets, and subprocess mocks.
- Some tests inspect source text for architecture budgets; this is intentional.
- Benchmarks write JSON reports under `reports/`.
- Benchmark runs generally need `WOOWA_SESSION_MODE=development`; some need daemon and mission artifacts.

## Commands

```bash
python3 -m pytest tests/ -q
python3 tests/benchmarks/release_acceptance.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/gate_measurements.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/full_scenario_comparison.py
```

## Anti-Patterns

- Do not treat benchmark scripts as pytest-collected tests.
- Do not delete failing tests to pass gates.
- Do not update generated reports unless the benchmark was actually run.
- Do not run learner-mode telemetry tests without isolating temp state.
