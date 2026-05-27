#!/usr/bin/env python3
"""Run Y13 qrels-based cohort evaluation and write a gate report."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QRELS = REPO_ROOT / "tests" / "fixtures" / "r3_qrels_real_v1.json"
REPORT = REPO_ROOT / "reports" / "cohort_y13_baseline.json"


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
        "--use-reformulated-query",
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
        "top5_match_rate": summary.get("top5_match_rate"),
        "ndcg_at_5": summary.get("ndcg_at_5"),
        "mrr": summary.get("mrr"),
        "learner_top1_match_rate": summary.get("learner_top1_match_rate"),
        "learner_top5_match_rate": summary.get("learner_top5_match_rate"),
        "learner_ndcg_at_5": summary.get("learner_ndcg_at_5"),
        "learner_mrr": summary.get("learner_mrr"),
        "latency_p50_ms": summary.get("latency_p50_ms"),
        "latency_p95_ms": summary.get("latency_p95_ms"),
        "errors_n": summary.get("errors_n"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
