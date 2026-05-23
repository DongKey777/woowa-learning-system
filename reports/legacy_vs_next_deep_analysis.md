# Legacy vs Next — 근본 설계 비교 분석

작성: 2026-05-24
대상: 사용자 (학습 시스템 owner) — Phase 9 migration 의사결정 input

## 결론 (먼저)

**근본적으로 "더 나은가"는 단일 답 없는 multi-axis trade-off다.**

- **RAG retrieval 절대 quality (top5 fixture pass)**: Legacy 우위 (92.7% vs 75.5%, **-17.2pp**)
- **Code simplicity + maintainability + evolution speed**: Next 압도적 우위 (1933 LOC vs ~80K, **-97.6%**)
- **Learner UX (entry point 수, daemon, state 복잡도)**: Next 우위 (1 vs 6)
- **Architectural risk (cohort coupling, fragility)**: Legacy 위험 누적 입증, Next 미입증

**핵심 질문**: "이 시스템의 진짜 미션이 무엇인가?"

- 미션 = **CS RAG 검색 정확도 최적화** → Legacy 유지가 합리
- 미션 = **학습자가 효율적으로 코칭 + 학습할 수 있는 통합 hub** → Next + corpus curation cycle이 합리

내 권장: **Hybrid path** (option B partial absorb) + corpus curation 1-2 cycle로 quality gap 절반 메꾸기 시도. 자세한 근거는 §5.

---

## 1. Architectural fundamental difference

### Legacy (`woowa-learning-hub`)

```
학습자 → 6 entry (bin/rag-ask, bin/coach-run, bin/learn-drill, ...) 
       → workbench routers + lexicon (CS_DOMAIN_TOKENS 500+ tokens, 5 separate files)
       → r3 retrieval pipeline (~67K LOC):
            ├── BGE-M3 dense (1024-d)
            ├── BGE sparse postings (Lance + JSON sidecar 173MB)
            ├── FTS (Lance full-text)
            ├── ColBERT (optional 17GB)
            ├── Lexical sidecar (Kiwi tokenization, 173MB JSON)
            ├── RRF fusion + adaptive rerank gate
            ├── Cross-encoder bge-reranker-v2-m3 (always-on)
            └── personalization in-pipeline (cycle 9.2)
       → response_contract.py (556 LOC) → markdown
       → daemon mode (state/rag-daemon.json, fingerprint refresh)
       
누적 코퍼스: 37,860 chunks × 3,211 v3 concepts
코퍼스 크기: 260MB (knowledge/cs/contents markdown)
인덱스 크기: 192MB (multi-modal Lance + sidecar)
```

### Next (`woowa-learning-system`)

```
학습자 → 1 entry (bin/ask) 
       → core/intent.py (71 LOC, TOOL_TOKENS만)
       → core/context.py (mode별 collector)
            ├── cs_qa: rag.search + rag.personalization
            ├── coaching: state + archive + peer_pr + rag
            ├── drill / retro / self_assess
       → rag.search (115 LOC):
            ├── BGE-M3 dense (1024-d) cosine
            ├── confusable_with relations walk (1-hop, score decay 0.7)
            └── cross-encoder rerank (옵션, always-on)
       → core.prompt + core.response (markdown shape)

코퍼스: 3,199 v3 concepts (1 JSON = 1 concept)
코퍼스 크기: 39MB
인덱스 크기: 12.84MB (dense only Lance)
```

---

## 2. Design Decision 측정 결과 (D1-D20)

각 design decision의 *측정된 결과*:

| ID | Decision | 측정 결과 | 평가 |
|---|---|---|---|
| D1 | 별도 repo | ✓ Parallel 유지, legacy 무중단 | ✅ 성공 |
| D2 | Markdown → JSON concept | ✓ 3199 docs 100% schema validate, 0 loss | ✅ 성공 |
| **D3** | **Chunk → Concept** | **top5 -17.2pp (75.5% vs 92.7%)** | ⚠️ **부분 falsified** |
| **D4** | **Sparse + lexical sidecar 폐기** | **D3와 결합 효과 분리 어려움; 같이 회귀** | ⚠️ **부분 falsified** |
| D5 | Intent-conditional rerank skip | 미테스트 (현재 always-on) | ⚠️ 미측정 |
| D6 | Router TOOL_TOKENS only | 100% intent dispatch (50 sample) | ✅ 성공 |
| D7 | AI semantic judge | 미실행 (turn-by-turn AI batch 필요) | ⚠️ 미측정 |
| D8 | Personalization 분리 | 학습자 profile 없어서 effect 측정 불가 | ⚠️ 미측정 |
| D9 | AI session 3 roles | 부분 ✓ (intent dispatch) | ⚠️ 부분 |
| D10 | BGE-M3 동일 | ✓ 동일 모델 | ✅ |
| D11 | RunPod build | ✓ 8.5분 / ~$5 (legacy 25분 / $5) | ✅ 성공 |
| D12 | mode=learning 필터 | ✓ tested | ✅ |
| D13 | Unified entry | ✓ 100% intent dispatch | ✅ 성공 |
| D14 | AI session orchestrator | 부분 ✓ (intent + prompt; live AI judge 미실행) | ⚠️ 부분 |
| D15 | Minimal JSON state | ✓ 117 tests | ✅ |
| D16 | AI drill scheduling | 미실행 (Phase 6.5 stub만) | ⚠️ 미측정 |
| D17 | AI cognitive trigger | 미실행 | ⚠️ 미측정 |
| D18 | UX markdown 유지 | ✓ template로 fill | ✅ |
| D19 | Peer PR — AI diff analyzer | ✓ archive query 작동 | ✅ |
| D20 | PR retrospective — AI mining | ✓ archive load 작동 | ✅ |

**점수**: ✅ 12 성공 / ⚠️ 8 부분 또는 미측정 / ❌ 0 명확 실패

**Critical**: D3 + D4 (RAG retrieval 핵심)가 부분 falsified. 이게 -17.2pp quality gap의 직접 원인.

---

## 3. Trade-off matrix

| 차원 | Legacy | Next | Winner |
|---|---|---|---|
| **RAG quality (top5 fixture)** | 92.7% | 75.5% | **Legacy +17.2pp** |
| **RAG quality (top1 fixture)** | (legacy 측정 없음, 추정 ~70%) | 51.5% | Legacy (추정) |
| **Warm latency p50** | 172ms | 282ms | **Legacy +64% faster** |
| **Warm latency p95** | ~400ms | 564ms | Legacy |
| **Cold first query** | 25s (legacy daemon-off) | 9s (M4) / ~5s (Pod) | **Next** |
| **Index size** | 192MB multi-modal | **12.84MB dense** | **Next -93%** |
| **Corpus size** | 260MB | **39MB** | **Next -85%** |
| **Runtime LOC** | ~80,000 | **1,933** | **Next -97.6%** |
| **Test count** | (legacy ~200+ tests) | 117 (full coverage) | Comparable |
| **Entry points** | 6 학습자-facing | **1 학습자-facing** | **Next** |
| **Daemon required** | Yes (fingerprint refresh 복잡) | **No** | **Next** |
| **Build time (RunPod)** | 25분 / $5 | **8.5분 / $5** | **Next -66% time** |
| **Schema enforcement** | Frontmatter parser (fragile, 5K LOC corpus_lint) | **JSON schema (96 LOC)** | **Next** |
| **Cohort coupling** | T cycle -20pp, K hand-update scale fail | 미입증 (architecture simpler) | **Next (가설)** |
| **Production maturity** | 7 cycles 검증된 baseline | 신규, learner 일상 사용 0일 | **Legacy** |
| **AI session integration** | manual dispatch + 4 cognitive types hardcoded | dict-shape prompt + AI decides | **Next** |
| **Learner-facing changes** | UX 변경 시 6 entry 동기화 | 1 entry markdown template만 수정 | **Next** |

**Legacy 우세 영역 (6)**: 절대 quality / warm latency / production maturity / cycle3 baseline 검증 / multi-modal coverage / drill·cognitive trigger 실측

**Next 우세 영역 (11)**: cold start / index size / corpus size / LOC / entry / daemon / build time / schema / cohort risk / AI integration / dev velocity

수적으로 Next 우세이지만 *Legacy의 우세 6은 학습자 직접 체감하는 것들* (검색 정확도, 응답 속도, 검증 데이터).

---

## 4. 근본 질문 — "이 시스템의 미션은?"

설계 결정은 *목적* 없이는 평가 불가능. 두 가능성:

### 미션 A: "최고 정확도의 CS RAG 검색 도구"

→ Legacy 우위가 명확. 7 cycle 데이터 + multi-modal + adaptive rerank가 학습자에게 정확한 답 제공.

→ Next는 -17.2pp quality 회귀 = **명백 회귀**. 채택 ❌.

### 미션 B: "Woowa mission 학습자가 코딩 + 학습을 효율적으로 수행하는 통합 hub"

→ RAG 정확도는 *하나의 구성요소*. 다른 요소:
- Mission PR 코칭 (peer compare, learner state assessment, mentor pattern)
- Drill scheduling + cognitive trigger
- Long-term mastery tracking
- Quick iteration (학습자가 매일 사용)

→ Legacy는 정확도 high이지만 80K LOC + 6 entry + daemon = 학습 흐름 마찰. 매 cycle PR 회귀 위험.

→ Next는 1 entry + 1933 LOC + 단순 state = 학습자가 mode 의식 없이 자연어로 모든 것 처리. RAG는 약함, but 통합 hub로서는 우위.

→ 채택 ✓ if corpus curation으로 quality gap 회복 (-17pp → -5~10pp)

---

## 5. 권장 path

**제안**: **Option B (Partial absorb) + Quality recovery cycle**

### 5.1 즉시 행동 (Phase 9 partial absorb)

1. **`bin/ask`에 mode dispatcher 분기 추가** (~30 LOC):
   - `cs_qa` mode → legacy `bin/rag-ask` 호출 (quality 92.7% 보존)
   - `coaching` / `drill` / `retro` / `self_assess` / `tool_only` → new system (UX 우위 + LOC 우위)
2. Legacy hub의 5 learner-facing entry (`rag-ask`, `coach-run`, `learn-drill`, `learn-pr-retro`, `learn-self-assess`)를 `bin/ask` alias로 redirect
3. `bin/learn-response-quality`는 그대로 두 시스템 공유

→ 학습자: **1 entry (`bin/ask`)** 만 알면 됨. 내부 dispatch는 mode별 최적 시스템.
→ Quality: cs_qa는 92.7% (legacy) + coaching/drill/retro는 new
→ Total LOC: legacy 80K + new 1933 (단 학습자는 1 entry로 동일)

### 5.2 Quality recovery cycle (병렬)

Next 시스템의 cs_qa quality를 -17.2pp → -5~8pp로 회복 시도 (2-3 cycles):

1. **Corpus curation actual run** (Phase 7 실행):
   - 학습자 history.jsonl 9992 events → citation_mismatch 패턴 mining
   - AI session이 alias / expected_queries / symptoms 추가 제안
   - 각 cycle 5-15 corpus changes → 재측정
   - 예상: +5-8pp 회복 (alias enrichment의 직접 효과)
2. **AI judge 재측정**:
   - fixture rigidity 효과 분리
   - 진짜 retrieval miss 비율 확정
   - 예상: -17.2pp 중 5-7pp는 fixture rigidity (semantic equiv but path mismatch)
3. **D5 intent-conditional rerank 실제 활성화**:
   - 현재 always-on → paraphrase/definition skip
   - latency 282ms → ~150ms 회복 + top1 +3-5pp 회복 가능

3 cycle 후 next 시스템이 cs_qa에서도 legacy와 동등 (top5 ≥90%) → 최종 migration step 5 (legacy 삭제) 가능.

### 5.3 거부 분석 — 왜 다른 option은?

**Option A (weak component recovery / sparse 회복)**:
- Plan total redesign 의도 위반 (D4 partial revert)
- Sparse 회복은 +200 LOC + 173MB sidecar 복귀
- 회복 효과는 +5-10pp 예상이지만 architectural simplicity 상실
- 거부: 너무 큰 가격

**Option C (Revert 새 시스템 폐기)**:
- 14 commits + 117 tests + 1933 LOC + corpus migration 폐기
- Legacy 80K LOC 그대로 유지 = 학습자에게 6 entry / daemon / fragility 그대로
- 거부: 누적 work loss 너무 큼, partial absorb 가능한데 굳이

**Option D (Continue quality fix만)**:
- 학습자에게 -17.2pp quality 시스템을 production으로 쓰는 것
- Quality recovery 안 될 risk
- 거부: 학습자 보호 위반

---

## 6. 측정의 한계 — 추가 검증 권장

이 분석의 *unknown unknowns*:

1. **AI judge 미실행**: semantic equiv top5는 -10pp 정도 일 수 있음 (fixture rigidity)
2. **Coaching e2e 미측정**: legacy coach-run vs new bin/ask coaching 비교 안 됨
3. **Drill / cognitive trigger 미실행**: D16-D17 진짜 better인지 미확정
4. **Long-term**: 학습자 1주일 일상 사용 후의 누적 personalization signal 효과

권장: Partial absorb 진행 후 1주일 학습자 사용 → 추가 측정으로 D14-D20 검증 + 최종 migration decision.

---

## 부록 A: 측정 raw data

- `reports/phase8_measurement.md`: code metrics + intent dispatch 100%
- `reports/phase9_rag_eval.json`: 200-query RAG cohort eval (top5 75.5%, p50 282ms)
- `reports/migration_run.json`: Phase 0 corpus migration 3199/0
- `state/index/manifest.json`: 3199 concepts × 12.84MB
- 16 commits / 117 tests / 1933 LOC / 39MB corpus
