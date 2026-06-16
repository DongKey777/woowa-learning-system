# CORPUS KNOWLEDGE BASE

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

## Overview

`corpus/` is the source-of-truth concept content plus schema and derived graph files used by RAG and learning-path features.

## Where To Look

| Task | Location | Notes |
|---|---|---|
| Concept content | `concepts/**` | editable source JSON |
| Schema | `schemas/concept-v2.schema.json` | validates concept entries |
| Concept graph | `concept_graph.json` | derived; rebuild instead of hand-editing |
| Backups | `*.bak` | local/generated backup artifacts |

## Conventions

- Edit concept JSON and schemas deliberately; keep canonical IDs stable.
- Rebuild derived graph/index artifacts through `bin/*` commands.
- Learner machines should fetch release indexes rather than rebuilding corpus indexes.
- Corpus edits are Mode B development work.

## Anti-Patterns

- Do not hand-edit `concept_graph.json` for convenience.
- Do not commit `.bak` files as source changes.
- Do not run local corpus/index builds in learner setup flows.
- Do not change concept IDs without migration/backfill plan.

## Commands

```bash
bin/graph-build
bin/corpus-build          # maintainer-only local build
bin/index-pack            # release artifact packaging
WOOWA_SESSION_MODE=development python3 tests/benchmarks/rag_quality_regression.py
```
