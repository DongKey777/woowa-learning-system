"""Regression tests for latency percentile helpers used by release gates."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(rel_path: str):
    path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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
