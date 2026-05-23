# woowa-learning-system

7 cycle 누적된 데이터/지식을 토대로 처음부터 재설계한 RAG 시스템.

Legacy: `DongKey777/woowa-learning-hub` (parallel 유지, 압도 시 삭제)

## Status

Phase 0 — corpus migration scaffolding.

## Development principles

매 commit에 4 원칙 자체점검 (`CLAUDE.md` 참고): Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution.

## Architecture

- **Corpus**: JSON Concept-as-Entity (`corpus/concepts/<category>/<id>.json`)
- **Index**: 3211 concepts × BGE-M3 dense
- **Router**: TOOL_TOKENS fast-path만
- **Eval**: AI session semantic judge
- **Target LOC**: ≤1100 runtime

Plan: `/Users/idonghun/.claude/plans/misty-giggling-valley.md`
