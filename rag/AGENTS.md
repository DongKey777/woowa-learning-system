# RAG KNOWLEDGE BASE

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

## Overview

`rag/` owns corpus loading, embedding, Lance index build/load, retrieval, personalization, reranking, and RAG evaluation helpers.

## Where To Look

| Task | Location | Notes |
|---|---|---|
| Corpus load | `corpus_loader.py` | schema validation and snapshots |
| Embeddings | `encoder.py` | BGE-M3 via transformers/sentence-transformers |
| Index build/load | `index.py` | Lance tables, drift checks, reports |
| Search | `search.py` | exact shortcut, fallback, cached query path |
| Personalization | `personalization.py`, `lexical.py`, `reranker.py` | post-retrieval ranking adjustments |
| Evaluation | `eval.py` | judge prompt and ranking metrics |

## Conventions

- Learner path fetches prebuilt index; maintainer path builds remotely or locally with explicit intent.
- Drift checks are diagnostic; they must not block daemon startup.
- Search should degrade gracefully when index/artifacts are missing.
- Keep model/cache behavior compatible with offline `HF_HUB_OFFLINE=1` environments.
- Preserve index release SHA verification and manifest semantics.

## Anti-Patterns

- Do not add `FlagEmbedding` as learner dependency; RunPod build may use it separately.
- Do not make daemon startup depend on slow corpus/index rebuilds.
- Do not hand-edit generated Lance index state.
- Do not bypass exact-match/qrels regression behavior to improve a benchmark superficially.

## Commands

```bash
bin/index-fetch
bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90
WOOWA_SESSION_MODE=development python3 tests/benchmarks/rag_quality_regression.py
python3 -m pytest tests/test_search.py tests/test_index_build.py -q
```
