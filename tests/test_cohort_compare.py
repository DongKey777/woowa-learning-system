"""Tests for bin/cohort-compare."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = REPO_ROOT / "bin" / "cohort-compare"
    loader = importlib.machinery.SourceFileLoader("cohort_compare_bin", str(path))
    spec = importlib.util.spec_from_loader("cohort_compare_bin", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compare_accepts_qrels_metrics_without_regression(tmp_path: Path, capsys) -> None:
    module = _load_module()
    control = tmp_path / "control.json"
    candidate = tmp_path / "candidate.json"
    control.write_text(json.dumps({
        "summary": {
            "top1_match_rate": 0.75,
            "ndcg_at_5": 0.84,
            "mrr": 0.80,
            "latency_p50_ms": 120.0,
            "latency_p95_ms": 200.0,
        }
    }), encoding="utf-8")
    candidate.write_text(json.dumps({
        "summary": {
            "top1_match_rate": 0.76,
            "ndcg_at_5": 0.85,
            "mrr": 0.81,
            "latency_p50_ms": 118.0,
            "latency_p95_ms": 205.0,
        }
    }), encoding="utf-8")

    assert module.main([
        "--control", str(control),
        "--candidate", str(candidate),
        "--fail-on-drift",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["top1_drift_pp"] == 1.0
    assert out["ndcg_at_5_drift_pp"] == 1.0


def test_compare_fails_on_qrels_metric_regression(tmp_path: Path) -> None:
    module = _load_module()
    control = tmp_path / "control.json"
    candidate = tmp_path / "candidate.json"
    control.write_text(json.dumps({
        "summary": {
            "top1_match_rate": 0.75,
            "ndcg_at_5": 0.84,
            "latency_p95_ms": 200.0,
        }
    }), encoding="utf-8")
    candidate.write_text(json.dumps({
        "summary": {
            "top1_match_rate": 0.73,
            "ndcg_at_5": 0.84,
            "latency_p95_ms": 205.0,
        }
    }), encoding="utf-8")

    assert module.main([
        "--control", str(control),
        "--candidate", str(candidate),
        "--fail-on-drift",
    ]) == 1
