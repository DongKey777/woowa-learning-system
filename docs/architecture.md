# Architecture — Woowa Learning System

## 1. 한 페이지 view

```
학습자 자연어 prompt
       ↓
bin/ask (socket client; warm CLI p95 ~30ms)
       ↓
core/daemon.py (AF_UNIX, single-thread, BGE-M3 warm in-process)
   1. core/router.py        → mode (cs_qa / coaching / tool_only / retro / drill / self_assess / f11_anchor)
   2. core/lazy_loader.py   → only artifacts the router asked for
   3. core/coach.py         → multi-agent labeled-section prompt
       ↓
state/learner/history.jsonl ← append rag_ask event (mode/router_mode/top_concept_ids)
       ↓
prompt markdown (avg 2.4KB, F11 up to 12KB)
       ↓
AI session (Claude / Codex / Gemini) → 학습자에게 한국어 답변
       ↓
bin/learn-event (학습자 code/drill/self-assess 이벤트) → core/feedback.py → core/mastery.py
       ↓
state/learner/mastery_graph.sqlite : attempted → familiar → proficient → mastered
```

## 2. 7-mode router (core/router.py)

| Mode | Trigger | Personas | Artifacts | Budget |
|---|---|---|---|---|
| `tool_only` | TOOL_TOKENS (gradle/git/docker/etc.) OR short greeting | — | — | 1500 |
| `cs_qa` | default — concept/CS question | MENTOR + SOCRATIC | concept_graph + mastery + rag_hits | 4500 |
| `coaching` | repo + coaching keyword (리뷰/리팩토링/내 코드/이 코드/쓰는 게/…) | MENTOR + REVIEWER + SOCRATIC | concept_graph + mission_patterns + mastery + rag_hits | 5500 |
| `retro` | 회고/반복/내 PR/정밀/사이클 키워드 | MENTOR + REVIEWER | mission_patterns + mastery | 5000 |
| `drill` | pending_drill + answer-shaped reply | SOCRATIC | mastery + drill_offer | 3000 |
| `self_assess` | pending_self_assessment + pure `^N점$` reply | — | mastery | 2000 |
| `f11_anchor` | F11_KEYWORDS (정밀 비교/다른 크루는/cross-crew/…) OR `PR N line` regex | REVIEWER + MENTOR | review_anchors + cross_crew_review_graph + mission_patterns | 12000 |

Phase J 측정 결과: **14/14 시나리오 mode dispatch 100% correct**.

## 3. Multi-agent (single-call, labeled section)

3 persona를 **하나의 prompt**에 labeled section으로 composition (cost ×1, perspective ×3):
- **[MENTOR]**: 원칙/best-practice. SOLID, IoC, Spring 컨테이너 의도. `mission_patterns + concept_graph`로 학습자가 사용 중인 패턴의 prereq 누락 지적.
- **[REVIEWER]**: 실제 reviewer 시각. `cross_crew_review_graph + review_anchors`로 비슷한 코드에 다른 reviewer 의견 비교.
- **[SOCRATIC]**: 답 직접 X, leading question으로 학습자 자가 사고 유도.

AI 세션이 mode에 따라 적합한 persona section만 활성. mode별 persona는 router 표 참조.

Phase N9 측정: coaching mode markdown에 3 persona 모두 surface (MENTOR + REVIEWER + SOCRATIC).

## 4. Bloom mastery autoloop (core/feedback.py + core/mastery.py)

### Evidence sources (weight)
- `pr_merge`: 1.0 (가장 강함)
- `mentor_accept`: 0.9
- `drill_score`: 0.7 × score
- `mission_use`: 0.3 (rag_ask/code_attempt/coach_run)
- `self_assess`: 0.2 (calibration only, mastery 직접 promote X)

### Promotion rules
- **attempted**: 1+ evidence
- **familiar**: ≥3 evidence + ≥2 distinct sources + 14일 internal
- **proficient**: `pr_merge` AND `mentor_accept` (strong source pair)
- **mastered**: `proficient` AND (`drill_score` ≥0.55 OR `self_assess` + 30일 retention)
- **demotion**: 없음 (monotonic — 한 번 promoted 후 weak drill 받아도 demote X, 설계상)

### Daemon integration (Phase K 핵심 fix)
- 매 `bin/ask` turn → daemon이 `rag_ask` event를 `history.jsonl`에 append (mode/router_mode/top_concept_ids 포함)
- 학습자 코드 작성/수정 turn → AI 세션이 `bin/learn-event --event-type code_attempt --silent` 호출
- drill answer turn → `bin/learn-event --event-type drill_answer --score s --silent` 호출
- 모든 이벤트가 `core/feedback.record_turn` → `core/mastery.promote` 자동 trigger

### 현재 상태
- 5 mastered + 2 proficient (Spring core: bean-di, ioc-di, mvc-controller, configurationproperties, transactional-self-invocation, jdbc-jpa-mybatis, transaction-isolation)

## 5. F10 forward — mission code → concept

### Tier 1 (annotation regex, deterministic)
`mission/extract.py` `ANNOTATION_TO_CONCEPT` 35 매핑:
- `@Transactional` → `spring/transactional-basics`
- `@RestController` → `spring/mvc-controller-basics`
- `@Service` → `spring/ioc-di-basics`
- ... (전체 35개)

Phase L 측정: **35/35 (100%) corpus 매핑 정확 + 35/35 (100%) round-trip extract**.

### Tier 2 (method + exception + import-triggered)
`METHOD_TO_CONCEPT` (18) + `EXCEPTION_TO_CONCEPT` (5) + `IMPORT_TRIGGERED_METHODS` (4 import × N method):
- `JdbcTemplate.query` → `database/jdbc-jpa-mybatis-basics`
- `DataIntegrityViolationException` → `database/jdbc-jpa-mybatis-basics`
- ...

Phase L 측정: **23/27 (85.2%)** corpus 매핑 정확.

### 사용
```bash
bin/mission-patterns-build --repo <repo>
# → state/repos/<repo>/mission_patterns.json
```
- learner own PR Java patch_text → 자동 추출
- coaching mode 시 prompt에 surface

## 6. F11 cross-crew

### 4-stage filter (`anchors/match.py`)
1. **Stage 1 path**: anchor.path와 동일 filename을 가진 cross-crew review_comments fetch (SQL LIKE)
2. **Stage 2 jaccard**: code hunk normalized-token Jaccard ≥0.4
3. **Stage 3 embedding**: BGE-M3 cosine → top-10
4. **Stage 4 AI veto** (runtime, query time): F11 prompt가 top matches를 쓰기 전 same-code-intent 여부를 거르게 함

### 사전 빌드
```bash
bin/cross-crew-build --repo <repo>
# → state/repos/<repo>/cross_crew_review_graph.parquet
```

Phase L 측정:
- **AI judge precision 85%** (10 sample = 5 high + 5 low conf)
- Stage 4 runtime veto prompt is now wired into the F11 answer path; next precision lift should be measured with post-answer sampling

## 7. Daemon (core/daemon.py)

- **AF_UNIX socket** + line-delimited JSON protocol
- **Single-thread** — single-learner design choice (concurrency 의도적 X)
- **BGE-M3 + corpus + lazy_loader 모두 메모리에 keep**
- Current Y13 semantics:
  - encoder backend: direct `transformers` CLS pooling by default; local snapshot first; fp16 on MPS/CUDA; rollback with `WOOWA_ENCODER_BACKEND=sentence-transformers` or `WOOWA_ENCODER_DTYPE=float32`
  - tokenizer backend: `PreTrainedTokenizerFast(tokenizer_file=...)` by default to skip AutoTokenizer resolution; rollback with `WOOWA_ENCODER_TOKENIZER_BACKEND=auto`
  - encoder startup scheduling: tokenizer/model parallel load + daemon encoder import priming; rollback with `WOOWA_ENCODER_PARALLEL_LOAD=0` or `WOOWA_DAEMON_ENCODER_IMPORT_PRIME=0`
  - socket clients use newline-delimited requests only; client `shutdown(SHUT_WR)` is intentionally avoided because the server stops reading at newline and closes after response
  - executable `bin/ask` uses a shell + AF_UNIX `nc` fast path for warm daemon text/JSON output; direct `python3 bin/ask` remains compatible and falls back to the Python client
  - manual override keywords are first-class: `그냥 답해` skips RAG, `RAG로 깊게` forces CS RAG, `코치 모드` forces coaching; override tokens are stripped from retrieval query before search
  - search-result cache stores post-rerank unpersonalized hits for default encoder calls; stable lexical rerank cache keys keep repeated AI-session prompts on the cache path while profile adjustment remains per request
  - warm socket p50/p95: **4.1ms / 6.3ms**
  - warm CLI p50/p95: **43.9ms / 47.6ms**
  - first ask after ready p50/p95: **187.2ms / 196.0ms**
  - first-answer total(stop→prewarm-ready→첫 `bin/ask`) p50/p95: **15245.0ms / 15247.7ms**
  - startup phase timing shows encoder import/materialization still dominates cold path, but first-ready p95 remains **15067.0ms**
- Phase N1: SQLite mastery 등 모든 state daemon restart 후 survive 검증
- Phase N12: 5 ask sequential = 5 well-formed event append (atomic)

### Actions
- `ping`: alive check
- `search`: low-level rag.search (debug/benchmark용)
- `ask`: full pipeline (router → lazy_load → coach → history append → return markdown)

### State files
- `state/rag-daemon.sock` — socket
- `state/rag-daemon.pid` — pid
- 시작: `bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90`

## 8. Performance (Y13)

| 지표 | 최신 값 |
|---|---:|
| warm socket p50/p95 | **4.1ms / 6.3ms** |
| warm CLI p50/p95 | **43.9ms / 47.6ms** |
| first ask after ready p50/p95 | **187.2ms / 196.0ms** |
| first-answer total p50/p95 | **15245.0ms / 15247.7ms** |
| qrels p50/p95 | **176.1ms / 278.6ms** |
| qrels strict top1 / MRR / NDCG@5 | **1.000 / 1.000 / 0.987** |
| 14-scenario bin/ask p50/p95 | **48.8ms / 71.5ms** |
| override keywords | **4/4, p95 199.9ms** |
| short exact concept queries | **8/8, p95 0.017ms** |
| release acceptance | **96/96 RELEASE READY** |

Historical Phase M in-process cold/warm numbers are superseded by Y13 daemon-layer reports in `reports/y13_latency_baseline.json`.

## 9. Storage decision tree

- **Append-heavy + queryable** → SQLite (`mastery_graph.sqlite`)
- **Read-mostly small** → JSON (`review_anchors.json`, `mission_patterns.json`, `drill_pending.json`, `drill_due.json`)
- **Large columnar** → Parquet (`cross_crew_review_graph.parquet`)
- **Immutable corpus** → JSON adjacency (`concept_graph.json` 3339 nodes / 6182 prereq edges / ~5MB)
- **Dense embed index** → Lance (`state/index/concept.lance` ~12MB release artifact; rebuild after each 200 staged corpus docs)
- **Append-only event log** → JSONL (`history.jsonl`)

## 10. LOC budget (plan §D-I)

Release acceptance canonical runtime LOC: **9496 / 9500**.

Corpus rebuild policy: run `tests/benchmarks/corpus_rebuild_readiness.py`
before any dense index rebuild. The gate validates strict schema load, title
uniqueness, stress-query exact coverage, qrels short-query wrong-owner absence,
runtime snapshot freshness, and lexical sidecar freshness. The readiness audit
tracks two hashes: `dense_corpus_sha256` for fields that affect embedding text
and `full_corpus_sha256` for the runtime snapshot, including relations and
metadata. `bin/corpus-build` writes both hashes into `state/index/manifest.json`
and emits matching `corpus_snapshot.json` / `lexical_fusion_sidecar.json` into
the selected `--index-dir`, so a remote release artifact is self-contained.
`bin/index-pack --verify-only` is part of release acceptance and checks that the
published archive contains both sidecars and the same dense/full corpus hashes
as the current index.

Core runtime package breakdown (Python files under package dirs): **8667 LOC**.
- `core/`: 6500 (router + lazy_loader + coach + daemon + mastery + feedback + drill + profile/session/onboarding/state + …)
- `rag/`: 1149 (encoder + search + index + corpus_loader + personalization + reranker)
- `mission/`: 395 (extract.py + graph.py)
- `anchors/`: 326 (extract.py + match.py)
- `curation/`: 297
- `scripts/`: 829

## 11. Development principles (4 원칙, commit 자체점검)

1. **Hypothesis-Driven Autonomy** — 모호 시 가설+측정+직접 판단. ask는 scope/policy 결정에만.
2. **Simplicity First** — 최소 코드. 추측 abstraction / 사용 안 하는 flexibility 금지.
3. **Surgical Changes** — 요청 범위만. adjacent code 개선 / 리팩토링 금지.
4. **Goal-Driven Execution** — verifiable success criteria 명시 후 loop. "make it work" 같은 weak criteria 금지.

Commit message에 self-check 4 항목 포함.

## 12. 참고

- Plan: `/Users/idonghun/.claude/plans/idempotent-snacking-puppy.md`
- Verification: [`verification-results.md`](verification-results.md)
- Testing: [`testing-guide.md`](testing-guide.md)
