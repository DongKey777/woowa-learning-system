# Learning Data Quality Phase 3 Acceptance

Date: 2026-05-28
Branch base: `main` / `origin/corpus/y14-expansion` unified at `bec784fc883829151ff052075afb76db3dd04eb1`

## Scope

This acceptance pass closes the learning-data collection/processing defects found in
`reports/learning_data_quality_improvement_proposal.md`:

- canonical learner identity is resolved before telemetry writes
- daemon `rag_ask` history records top-level `learner_id` and `repo`
- explicit learner feedback is stored under `state/learner/feedback.jsonl`
- mining/audit wrappers exclude development, test, fixture, and demo rows
- canonical backfill is backup-first and repeatable

## Implemented

- `bin/ask` resolves `state/learner/identity.json` in both shell fast path and Python fallback.
- Phase T/X learner-data wrappers resolve the canonical identity when `--learner-id` is omitted.
- `core/daemon.py` canonicalizes default learner IDs both in client `ask()` and server socket handling.
- `core/daemon.py` writes `learner_id` and `repo` as first-class `rag_ask` event fields.
- `bin/learn-feedback` writes to `state/learner/feedback.jsonl`; `bin/feedback-mine` reads the same path.
- `scripts/mining/jsonl_iter.py` supports `exclude_modes` and fixture/demo row detection.
- W mining/audit wrappers now report backfilled/original/skipped fixture counts.
- `scripts/migration/canonical_id_backfill.py` canonicalizes production telemetry with timestamped backups.

## State Acceptance

- Backfill applied:
  - `state/_backups/canonical-20260528-100649/`
  - `state/_backups/canonical-20260528-101436/`
- Final dry-run:
  - `changed_total`: 0
  - `skipped_unknown_total`: 0
  - `skipped_non_production_n`: 4 (`learner-A/B` development isolation rows)
- Current learner history:
  - `DongKey777`: 4907 rows
  - development isolation rows: `learner-A` 2, `learner-B` 2
- `bin/validate-state --strict`: pass
- daemon ping: alive
- direct socket smoke with `learner_id=default`: recorded as `DongKey777`

## Verification

| Check | Result |
|---|---:|
| `pytest tests/ -q` | 491 passed |
| `rag_quality_regression.py` | top1/top5/ndcg/mrr/recall all 1.0 |
| RAG retrieval latency | p50 0.2ms / p95 0.3ms |
| `gate_measurements.py` | 9/9 pass |
| `uncovered_scenarios.py` | 12/12 pass |
| `uncovered_scenarios_phase_n.py` | 12/12 pass |
| `deep_scenarios_phase_p.py` | 10/10 pass |
| Phase W wrappers | 12/12 pass |
| Phase X wrappers | 11/11 pass |
| Full scenario comparison | v2 p50 32.0ms / p95 40.0ms |

Full comparison after the final daemon restart:

- mode dispatch: 14/14
- evidence coverage: v2 96.0% vs legacy 64.0%
- latency: v2 p50 32.0ms vs legacy 41.0ms, v2 p95 40.0ms vs legacy 50.0ms
- output size: v2 average 4117 chars vs legacy 7332 chars

## Remaining Notes

- Production `response-quality.jsonl` and `feedback.jsonl` are currently empty; this is expected until future learning turns call the wrappers.
- The four non-canonical learner IDs are deliberate development isolation rows and are excluded by mining.
- Future direct socket callers that send `learner_id=default` are now canonicalized in the daemon before persistence.
