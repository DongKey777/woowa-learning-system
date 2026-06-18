#!/usr/bin/env python3
"""Held-out authority RAG eval over real_learner_qrels_v1 (W9).

This is the AUTHORITY gate for retrieval quality. Unlike cohort_qrels_eval
(r3_qrels_real_v1, reformulated) and the rag_quality_regression bench (which
scores against each concept's own expected_queries — a self-referential signal
that rewards corpus authors phrasing queries the way they index), this runs the
50 real, held-out learner utterances in real_learner_qrels_v1 (0% overlap with
expected_queries, edit-frozen) through the production retrieval path with NO
query reformulation. Top-1 here is the closest proxy we have for "did the
learner get the right concept first try."

Writes reports/real_learner_qrels_baseline.json (full report, with `summary`);
release_acceptance._y13_gate_checks reads it and gates top1 >= floor.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QRELS = REPO_ROOT / "tests" / "fixtures" / "real_learner_qrels_v1.json"
REPORT = REPO_ROOT / "reports" / "real_learner_qrels_baseline.json"


def main() -> int:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "bin" / "cohort-eval"),
        "--qrels",
        str(QRELS),
        "--out",
        str(REPORT),
        "--top-k",
        "5",
        "--relations-expand",
        "5",
        # NO --use-reformulated-query: real learner utterances are scored as
        # actually typed. Reformulation here would mask raw-utterance retrieval.
    ]
    run = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if run.returncode != 0:
        print(run.stdout, end="")
        print(run.stderr, end="", file=sys.stderr)
        return run.returncode
    summary = json.loads(run.stdout)
    print(json.dumps({
        "report": str(REPORT.relative_to(REPO_ROOT)),
        "cohort_size": summary.get("cohort_size"),
        "ranking_evaluated": summary.get("ranking_evaluated"),
        "top1_match_rate": summary.get("top1_match_rate"),
        # learner_top1 = top1 in (expected ∪ primary_paths ∪ acceptable_paths) — the
        # qrels' designed "did the learner get an acceptable answer" metric. As the
        # corpus grows and legitimate sibling concepts multiply, strict top1 (expected
        # only) over-penalizes acceptable sibling-swaps; learner_top1 credits the
        # acceptable_paths the qrels authors deliberately included. This is the
        # AUTHORITY gate metric (see release_acceptance _y13_gate_checks).
        "learner_top1_match_rate": summary.get("learner_top1_match_rate"),
        "learner_top5_match_rate": summary.get("learner_top5_match_rate"),
        "top5_match_rate": summary.get("top5_match_rate"),
        "ndcg_at_5": summary.get("ndcg_at_5"),
        "mrr": summary.get("mrr"),
        "latency_p50_ms": summary.get("latency_p50_ms"),
        "latency_p95_ms": summary.get("latency_p95_ms"),
        "errors_n": summary.get("errors_n"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
