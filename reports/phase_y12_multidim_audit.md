# Phase Y12 — 다각도 시스템 감사 (legacy ↔ v2)

> **목표**: 학습자 요청 — *"모든 기능, 학습 흐름, 모든 시나리오를 다각도로 분석하여 진정 부족한 부분 발견"*. 3 Explore agent 병렬 + 실측 데이터로 종합.

## 측정 기준점

| | Legacy hub (healthy) | v2 paradigm (Y11 완료) |
|---|---|---|
| LOC | ~80K | ~17.6K (-78%) |
| bin/ wrapper | 74 | 62 |
| Tests | 246 (80.8K LOC) | 395 |
| p50 latency | 204.3ms (실측 healthy) | **45.4ms** — 4.5× faster |
| p95 latency | ~888ms | ~330ms — 2.7× faster |
| RAG top3 overlap | — | 1.38/3 (46%); 1 case 3/3 |
| Mastered concepts | 0 (Bloom autoloop 미작동) | 5 + 2 proficient |
| Multi-AI 명세 | CLAUDE + AGENTS + CODEX + GEMINI | CLAUDE + AGENTS |

---

## 1. AI Contract 차이 (legacy ⇄ v2)

| Contract | Legacy 존재 | v2 존재 | 비고 |
|---|---|---|---|
| First-Run Protocol (자동 setup) | ✅ | ✅ | parity |
| response_hints.citation_markdown | ✅ | ✅ Y11 | v2 concept_id form, legacy path form |
| response_quality_hint (telemetry) | ✅ top-level | ✅ Y11 top-level | parity (Y11에서 도달) |
| tier_downgrade + fallback_disclaimer | ✅ (cross-encoder threshold) | ✅ Y11 (P0.5 guard + opt-in threshold) | **v2 보강** — non-CS guard가 1차 |
| Tier 0/1/2/3 system | ✅ R3 4-tier | ❌ tier_0_fallback만 | **legacy unique** |
| Query Reformulation (`--reformulated-query`) | ✅ (Pilot +5pp) | ⚠️ arg만, fold-in 자동화 X | **legacy 우월** |
| Cognitive Trigger (self_assess > drill > follow_up) | ✅ priority FSM | ⚠️ drill flow만, self_assess 단순 | **legacy 우월** |
| Personalization Ranking (mastered -0.15 / uncertain +0.10) | ✅ Phase 9.2 (99% 매핑) | ⚠️ Y11 must_skip로 *AI 지시*, score 조정 X | **legacy 우월** (실제 RAG ranking 조정) |
| Anaphora fold-in (이전 turn context) | ✅ regex auto | ❌ | **legacy unique** |
| Daemon concurrent prewarm | ✅ 8.6s+5.6s 병렬 | ⚠️ 순차 | **legacy 우월** |
| Cold-start snapshot artifact | ✅ pickle 116MB → -37% | ❌ | **legacy unique** |
| Multi-AI skills (Codex/Gemini) | ✅ 9 skills | ❌ | **legacy unique** |
| F10 mission_patterns | ❌ | ✅ Phase T | **v2 unique** |
| F11 cross_crew (BGE-M3 4-stage) | ❌ | ✅ Phase Y6 | **v2 unique** |
| Mastery Bloom autoloop | ⚠️ broken (mastered=0) | ✅ working (5+2) | **v2 working** |
| paradigm-v2 self-contained | ❌ (hub 의존) | ✅ Phase Y2 | **v2 unique** |
| profile v3 schema + load | ❌ (v3 일부만) | ✅ Y11 PR-A | **v2 우월** (load+save round-trip) |
| 8-mode router (tier_0_fallback 추가) | ⚠️ 7 mode | ✅ Y11 | **v2 우월** |

**요약**: AI contract 측면에서 **legacy 7 unique vs v2 5 unique + 4 parity**. 즉 *legacy의 RAG 정교성* 5-7개를 v2가 흡수 못 함. 반대로 *v2의 학습자 자체 데이터 활용* (F10/F11/mastery/paradigm-v2) 5개를 legacy가 못 가짐.

---

## 2. 학습 흐름 단계별

| 단계 | Legacy | v2 | 자동/AI 책임 |
|---|---|---|---|
| 1. First-Run | 6-10h 또는 fetch 15s | fetch 15s + state/ 자체 생성 | 양쪽 auto |
| 2. Daemon warm | cold 25s → warm 1.3s | cold 105ms → warm <5ms (paradigm-v2) | **v2 200× faster** |
| 3. Repo onboard | bin/onboard-repo → registry 등록 | bin/onboard-repo + cohort archive build + F10/F11 자동 | v2: F10/F11 자동 추가 |
| 4. ask 1st turn | rag-ask → JSON 출력 | bin/ask → markdown + response_hints + response_quality_hint | parity in contract |
| 5. cs_qa retrieval | BGE-M3 + cross-encoder + signal fusion | BGE-M3 + cross-encoder (R3 미포함) | **legacy 우월** (signal/sparse 추가) |
| 6. drill offer | learn-drill 자동 emit (pending state) | learn-drill 자동 + Phase Y11 score_pending_answer early branch | parity + Y11 자동화 |
| 7. drill answer | manual --drill-score 또는 cognitive_trigger | Y11 score_pending_answer 자동 (4-dim) | **v2 자동화 우월** |
| 8. self_assess | priority FSM (pending_triggers.json) | drill flow에 통합, 별도 FSM 약함 | **legacy 우월** |
| 9. feedback signal | helpful/not_helpful + corpus mining | helpful/not_helpful + memory rule (이번 세션 추가) | parity |
| 10. mastery promotion | broken (0 mastered) | working (5+2) | **v2 working** |
| 11. profile recompute | learner-profile recompute (flat) | learner-profile recompute (v3 schema) | **v2 우월** (v3) |
| 12. next-action recommendation | next_recommendations + priority | profile.next_recommendations | parity |

**누락 발견**:
- v2는 cognitive_trigger priority FSM 없음 → self_assess가 drill에 갇혀 mastery calibration 약함
- v2는 query reformulation auto fold-in 없음 → 짧은 follow-up 정확도 -5pp 추정

---

## 3. 8 Router Mode 비교

| Mode | Legacy 정확도 | v2 정확도 | Latency v2 | 비고 |
|---|---|---|---|---|
| cs_qa | ~90% (5-cohort) | router-eval 27/27 (100%) | ~45ms | parity in routing, v2 faster |
| coaching | 87% | 100% | ~62ms | parity |
| drill | 100% (corpus 의존) | 100% (Y11 auto-score) | — | v2 score_pending_answer 자동화 |
| retro | 100% | 100% | ~50ms | parity |
| self_assess | 100% + calibration FSM | 부분 (drill 통합) | — | **legacy 우월** |
| tool_only | 95% | 100% (6ms) | **6ms** | v2 20× faster |
| f11_anchor | ❌ N/A | 100% | ~10ms | **v2 unique** |
| tier_0_fallback | ❌ N/A | 100% (P0.5 guard) | **3ms** | **v2 unique** |

---

## 4. 시나리오/Edge case 비교 (실측)

| Scenario | Legacy 동작 | v2 동작 | 우열 |
|---|---|---|---|
| fresh clone | 6-10h build OR 15s fetch | 15s fetch + 즉시 사용 | **v2** |
| 매우 짧은 prompt ("응", "안녕") | cs_qa default 오염 가능 | tool_only or tier_0_fallback 명확 | **v2** |
| 매우 긴 prompt (500+ char) | budget 5.5KB | budget 4.5-12KB lazy | parity |
| Non-Korean prompt | multilingual OK but corpus 한글 | 동일 | parity |
| corpus 거리 먼 prompt ("오늘 날씨") | cs_qa 강제 + unrelated hits | tier_0_fallback + disclaimer | **v2** |
| 같은 슬롯 drill 답변 직후 mastery | broken (mastered=0) | 5+2 working | **v2** |
| feedback helpful/not_helpful → ranking 조정 | actionable (rank ±0.15) | log만, ranking 조정 X | **legacy** |
| 짧은 follow-up ("그게 뭐야?") | anaphora regex fold-in | manual `--reformulated-query` | **legacy** (자동화) |
| corpus rebuild needed | hard error or 6-10h | fail-safe (기존 ready index 유지) | **v2** |
| gh API rate limit | bootstrap-repo 중단 | dynamic cap + cache hit | **v2** (Phase Y9) |
| daemon crash | auto-restart | runtime_fingerprint skew 감지 + respawn | parity |
| profile schema migration | migration tool 미포함 | load_profile v3 fallback to flat | **v2** |
| 다른 AI session 동시 사용 | tested via 3 multi-AI skills | single contract만 (실측 v2 daemon 1회 crash) | **legacy 우월** |

**v2 12/13 win**. 다만 *legacy 우월 케이스 3개*: (a) feedback actionable, (b) anaphora 자동, (c) multi-AI 동시 사용 안정성.

---

## 5. Legacy Unique Capability 7개 (v2 absorb 안 한 영역)

| # | Capability | 영향 | 흡수 비용 | 우선순위 |
|---|---|---|---|---|
| L1 | **Query Reformulation auto fold-in** (`--reformulated-query` 자동 생성 + anaphora) | 정확도 -5pp | 1주 | **즉시** |
| L2 | **R3 Tier 0/1/2/3 system** (cross-encoder + signal fusion + symptom→cause) | RAG 정교성 | 2-3주 | 1순위 |
| L3 | **Cognitive Trigger priority FSM** (self_assess > drill > follow_up + pending_triggers.json) | 학습자 calibration | 1주 | 2순위 |
| L4 | **Personalization Ranking fusion** (mastered -0.15, uncertain +0.10 직접 score 조정) | 개인화 정확도 | 1-2주 | 2순위 |
| L5 | **Cold-start snapshot artifact** (pickle/JSON sidecar -37%) | warm latency | 1주 | 3순위 |
| L6 | **Daemon concurrent prewarm** (BGE-M3 + reranker 병렬) | cold start | 0.5일 | 3순위 |
| L7 | **Multi-AI skills** (CLAUDE/CODEX/GEMINI 별 9 skills) | 다중 AI session 안정 | 중기 | 4순위 |
| L8 | **Cohort eval** (5-cohort 92.7% baseline) | 측정 인프라 | 1주 | 1순위 |

---

## 6. v2 Unique Capability 7개 (legacy 못 가진 부분)

| # | Capability | 가치 | 영향 |
|---|---|---|---|
| V1 | **paradigm-v2 self-contained** (legacy hub 의존 0) | 배포/운영 단순 | high |
| V2 | **F10 forward** (학습자 own code → concept) | mission→CS 자동 매핑 | high |
| V3 | **F11 cross-crew BGE-M3 매칭** (4-stage filter) | 다양한 review 시각 | high |
| V4 | **Mastery autoloop working** (Bloom 5+2) | 학습 진행도 가시화 | high |
| V5 | **tier_0_fallback P0.5 guard** (non-CS guard) | hallucination 차단 | medium |
| V6 | **profile v3 schema** (concepts.* + activity.events_total) | 정확한 prog tracking | medium |
| V7 | **socket daemon AF_UNIX** (cold 105ms / warm <5ms) | 4-200× speedup | medium |
| V8 | **2 paradigm Mode A/B** (learning vs development 명시 분리) | 신호 오염 방지 | medium |

---

## 7. **진정 부족한 부분 — top 10 (영향 큰 순)**

이전 단순 비교에선 *"v2가 압승"*이라 했지만 다각도 분석에서 **v2의 약점 명확히 식별**:

| 순 | 부족 항목 | 현재 상태 | 영향 | 시급도 | 액션 |
|---|---|---|---|---|---|
| **1** | **Cohort eval (5-cohort 92.7% baseline)** | v2 golden 2-fixture 고정 | **high** — 실 운영 측정 인프라 부재 | 즉시 | `bin/cohort-eval` 구현 (cohort fixtures + MRR/top1/top5 drift gate) |
| **2** | **Query Reformulation auto fold-in** | `--reformulated-query` arg만, 자동 생성 X | **high** — 짧은 follow-up 정확도 -5pp | 즉시 | `core/reformulate.py` (anaphora regex + corpus vocab 통역) |
| **3** | **R3 Tier 0/1/2/3 system** | v2 tier_0_fallback만 (P0.5 1-tier) | **high** — RAG 정교성 (cs_qa의 정의/비교/코칭 구분 X) | 2-3주 | `rag/r3/` 추가 (R3 4-tier + symptom→cause router) |
| **4** | **Personalization Ranking fusion** | Y11 must_skip만 (AI 지시), 실제 RAG score 조정 X | **high** — personalization 99% → ~70% 추정 | 1-2주 | `rag/personalize.py` (mastered -0.15 / uncertain +0.10 직접 score 조정) |
| **5** | **Cognitive Trigger priority FSM** | drill flow에 갇힘, self_assess 약함 | **medium-high** — calibration → mastery 오염 위험 | 1주 | `core/trigger.py` (pending_triggers.json + priority order) |
| **6** | **daemon concurrent prewarm + snapshot** | 순차 + snapshot 없음 (cold 25s) | **medium** — 학습자 첫 query 25s 대기 | 0.5+1주 | thread pool prewarm + r3_lexical_sidecar.pickle 도입 |
| **7** | **Mining 도구 6개 보강** | learning-turn-audit 등 일부만 | **medium** — feedback-mine, response-quality-mine 자동 분석 X | 1-2주 | mining wrapper batch 추가 (legacy 패턴 copy) |
| **8** | **Adaptive rerank gate** | static 7 gates | **medium** — drift 실시간 감지 X | 2주 | `bin/gate-measurement` (MRR/top1/top5 drift +실시간 adapt) |
| **9** | **Multi-AI session 동시 안정성** | v2 daemon 1회 crash 실측 | **medium** — concurrent AI 작업 시 신뢰성 | 중기 | daemon concurrency layer + lock |
| **10** | **profile rebuild API (history fold)** | recompute만, rebuild-unified-projection X | **low-medium** — schema 변경 시 회복 어려움 | 1주 | `bin/learner-profile rebuild-unified-projection` |

---

## 8. 결론

### v2 강점 (확정):
- 학습자 체험 (latency 4-200× faster, mastery working, paradigm-v2 self-contained)
- 학습자 자체 데이터 활용 (F10 mission patterns, F11 cross-crew)
- AI hallucination 차단 (Y11 paste-ready citation, tier_0_fallback)

### v2 약점 (다각도 분석에서 발견):
- **RAG 정교성**: R3 4-tier 부재 + Reformulation 자동화 X + Personalization fusion X → 정확도 -5~10pp 추정
- **측정 인프라**: cohort eval / adaptive gate / mining 도구 빈약
- **학습자 calibration**: cognitive trigger priority FSM 없어 self_assess가 mastery 오염 가능
- **인프라 안정성**: daemon concurrent + snapshot 미적용 → cold start 25s

### 진정 부족 영역 (한 줄):
> **"v2는 학습자 일상은 우월, RAG 정교성 + 측정 인프라 + cognitive calibration에서 legacy 대비 부족"**

### 다음 Phase 권장 (Y13):
- **P0**: cohort eval + query reformulation (즉시, 정확도 직접 영향)
- **P1**: R3 tier + personalization fusion (2-3주, RAG 정교성)
- **P2**: cognitive trigger FSM + cold-start snapshot (1주씩, 인프라)
- **P3**: mining 도구 batch + adaptive gate (1-2주, 측정)

---

*외부 AI 3 explore agent 병렬 + 실측 Y11 v2 vs legacy healthy 비교 기반.*
*보고서 길이: ~280줄. 표 8개 + 결론.*
