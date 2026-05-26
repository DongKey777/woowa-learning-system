"""Tests for bin/cohort-eval qrels mode."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = REPO_ROOT / "bin" / "cohort-eval"
    loader = importlib.machinery.SourceFileLoader("cohort_eval_bin", str(path))
    spec = importlib.util.spec_from_loader("cohort_eval_bin", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qrels_loader_accepts_legacy_real_fixture() -> None:
    module = _load_module()
    metadata, records = module._load_qrels(REPO_ROOT / "tests" / "fixtures" / "r3_qrels_real_v1.json")

    assert metadata["fixture_id"] == "r3_qrels_real_v1"
    assert metadata["query_count"] == 200
    assert len(records) == 200
    assert records[0]["cohort_tag"] == "paraphrase_human"
    assert records[0]["expected_concepts"] == ["spring/bean-di-basics"]


def test_evaluate_qrels_computes_ranking_latency_and_cohort_metrics(tmp_path: Path) -> None:
    module = _load_module()
    qrels = {
        "schema_version": 1,
        "fixture_id": "unit_qrels",
        "query_count": 3,
        "queries": [
            {
                "query_id": "q1",
                "prompt": "DI가 뭐야?",
                "language": "ko",
                "level": "beginner",
                "cohort_tag": "paraphrase_human",
                "primary_paths": ["contents/spring/spring-bean-di-basics.md"],
                "expected_concepts": ["spring/bean-di-basics"],
                "failure_focus": ["paraphrase_robustness"],
            },
            {
                "query_id": "q2",
                "prompt": "커넥션 풀",
                "language": "ko",
                "level": "beginner",
                "cohort_tag": "symptom_to_cause",
                "primary_paths": ["contents/database/connection-pool-basics.md"],
                "expected_concepts": ["database/connection-pool"],
                "failure_focus": [],
            },
            {
                "query_id": "q3",
                "prompt": "RVQ vs PQ",
                "language": "mixed",
                "level": "advanced",
                "cohort_tag": "corpus_gap_probe",
                "primary_paths": [],
                "expected_concepts": ["frontier/vector-quantization-research"],
                "failure_focus": ["honest_gap"],
            },
        ],
    }
    qrels_path = tmp_path / "qrels.json"
    qrels_path.write_text(json.dumps(qrels), encoding="utf-8")

    def fake_search(prompt: str, top_k: int, relations_expand: int, state_root: Path):
        del top_k, relations_expand, state_root
        if "DI" in prompt:
            return (
                [
                    {"concept_id": "spring/bean-di-basics", "score": 0.9},
                    {"concept_id": "spring/ioc-di-basics", "score": 0.8},
                ],
                10.0,
                None,
            )
        if "커넥션" in prompt:
            return (
                [
                    {"concept_id": "database/transaction-basics", "score": 0.7},
                    {"concept_id": "database/connection-pool", "score": 0.6},
                ],
                20.0,
                None,
            )
        return ([{"concept_id": "network/http-basics", "score": 0.5}], 30.0, None)

    report = module.evaluate_qrels(qrels_path, search_fn=fake_search)
    summary = report["summary"]

    assert summary["ranking_evaluated"] == 2
    assert summary["top1_match_rate"] == 0.5
    assert summary["top5_match_rate"] == 1.0
    assert summary["recall_at_5"] == 1.0
    assert summary["mrr"] == 0.75
    assert summary["learner_alignment_score"] == 1.0
    assert summary["latency_p50_ms"] == 20.0
    assert summary["latency_p95_ms"] == 29.0
    assert report["per_cohort"]["paraphrase_human"]["top1_match_rate"] == 1.0
    assert report["per_cohort"]["corpus_gap_probe"]["ranking_evaluated"] == 0
    assert report["results"][2]["failure_class"] == "corpus_gap"


def test_path_to_concept_candidates_maps_legacy_path() -> None:
    module = _load_module()
    ids = {"spring/bean-di-basics", "database/connection-pool"}

    assert module._path_to_concept_candidates(
        "contents/spring/spring-bean-di-basics.md",
        ids,
    ) == ["spring/bean-di-basics"]
    assert module._path_to_concept_candidates(
        "contents/database/connection-pool-basics.md",
        ids,
    ) == ["database/connection-pool"]
