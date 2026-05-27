"""Regression tests for latency percentile helpers used by release gates."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_p95_uses_nearest_rank_for_small_latency_samples() -> None:
    samples = [1.0, 2.0, 100.0]
    for rel_path in [
        "tests/benchmarks/daemon_latency.py",
        "tests/benchmarks/concurrent_ask.py",
        "tests/benchmarks/reformulated_query_path.py",
        "tests/benchmarks/personalization_active_path.py",
    ]:
        module = _load_module(rel_path)
        assert module._percentile(samples, 0.95) == 100.0


def test_full_scenario_summary_uses_all_run_samples_for_p95() -> None:
    module = _load_module("tests/benchmarks/full_scenario_comparison.py")

    assert module._percentile([1.0, 2.0, 3.0], 0.95) == 3.0
    assert module._percentile([1.0] * 19 + [100.0], 0.95) == 1.0
    assert module._percentile([1.0] * 18 + [90.0, 100.0], 0.95) == 90.0


def test_daemon_latency_legacy_comparison_uses_total_first_answer() -> None:
    module = _load_module("tests/benchmarks/daemon_latency.py")
    report = {
        "layers": {
            "first_answer_total": {"p95_ms": 1000.0},
        },
        "legacy": {
            "prewarm_scenario": {
                "layers": {
                    "total_to_first_answer_after_prewarm": {"p95_ms": 5000.0},
                },
            },
            "health_scenario": {
                "layers": {
                    "total_to_first_answer_after_health": {"p95_ms": 3000.0},
                },
            },
        },
    }

    comparison = module._legacy_comparison(report)

    assert comparison["total_first_answer_vs_legacy_prewarm_p95_speedup"] == 5.0
    assert comparison["total_first_answer_vs_legacy_health_p95_speedup"] == 3.0


def test_daemon_latency_reads_startup_timings_from_log(tmp_path: Path) -> None:
    module = _load_module("tests/benchmarks/daemon_latency.py")
    log_path = tmp_path / "daemon.log"
    log_path.write_text(
        "noise\n"
        "[daemon] startup_timings {\"encoder_prewarm_ms\": 123.4, \"pid\": 42}\n"
        "[daemon] ready\n",
        encoding="utf-8",
    )

    assert module._read_startup_timings(log_path) == {
        "encoder_prewarm_ms": 123.4,
        "pid": 42,
    }
