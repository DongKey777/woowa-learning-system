"""scripts/mining — paradigm-v2 system improvement mining + analytics.

Replaces legacy: feedback-mine, response-quality-mine, routing-analyze,
learning-turn-audit, learning-path-graph-audit, reclassify-history,
cohort-eval, cohort-compare, golden, rag-eval, router-generalization-eval,
learner-log-rag-eval.

All wrappers stream JSONL (no SQL) — paradigm-v2 stores observability in
state/learner/{history,response-quality,feedback}.jsonl.
"""
