"""Regression tests for qrels relevance mapping."""
from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_cohort_eval():
    loader = SourceFileLoader(
        "cohort_eval_bin",
        str(REPO_ROOT / "bin" / "cohort-eval"),
    )
    spec = importlib.util.spec_from_loader("cohort_eval_bin", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qrels_accepts_acceptable_paths_as_relevant_top1(tmp_path) -> None:
    cohort_eval = _load_cohort_eval()
    qrels = tmp_path / "qrels.json"
    qrels.write_text(json.dumps({
        "fixture_id": "unit",
        "schema_version": 1,
        "query_count": 1,
        "queries": [{
            "query_id": "unit:acceptable",
            "cohort_tag": "forbidden_neighbor",
            "prompt": "Spring Bean이 뭐야?",
            "expected_concepts": ["spring/bean-lifecycle-basics"],
            "primary_paths": ["contents/spring/spring-bean-lifecycle-basics.md"],
            "acceptable_paths": ["contents/spring/spring-bean-di-basics.md"],
        }],
    }), encoding="utf-8")

    def fake_search(_prompt, _top_k, _relations_expand, _state_root):
        return [{"concept_id": "spring/bean-di-basics"}], 1.0, None

    report = cohort_eval.evaluate_qrels(qrels, search_fn=fake_search)
    row = report["results"][0]
    summary = report["summary"]

    assert row["top1_match"] is False
    assert row["strict_top1_match"] is False
    assert row["learner_top1_match"] is True
    assert summary["top1_match_rate"] == 0.0
    assert summary["strict_top1_match_rate"] == 0.0
    assert summary["learner_top1_match_rate"] == 1.0
