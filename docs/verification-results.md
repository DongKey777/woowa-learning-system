# Verification results — latest Y14 corpus closure + historical phase results

최신 측정 날짜: 2026-05-28
브랜치: `main`
기준: Y14 batch 1-7 corpus closure after remote dense index rebuild and release v1.0.2 sync. Learner state는 2026-05-28 reset 완료 (`events_total=0`) 후 실제 사용 데이터로 재누적한다.

## 1. 한 줄 요약

Y14 corpus closure는 corpus **3339 concepts**, `concept_graph.json` **6172 prerequisite edges**, broken edge **0** 상태로 닫았다. Batch 1-7 qrels prompt/reformulated 14세트는 strict top1/top5/MRR/NDCG **1.000**, forbidden **0**, latency p95 최대 **3.6ms**다. Remote dense index는 H100 secure에서 빌드했고, archive SHA256은 `d8da5782c6fdceeec34e541a30e511bf2f8d168c01dab4e47dfefcde641921dc`, Lance size는 **13.40MB**, archive size는 **18.7MB**다. Corpus readiness는 **ready=true / rebuild_needed=false**이고, full pytest는 **523 passed**다.

## 1.1 Latest Y14 Snapshot

| 축 | 최신 결과 |
|---|---:|
| Corpus concepts / graph | **3339 concepts / 6172 prereq edges / broken 0** |
| Y14 qrels prompt/reformulated | **14/14 sets top1=1.000, top5=1.000, forbidden=0, max p95=3.6ms** |
| rag_quality top1 / NDCG@5 / p95 | **1.000 / 1.000 / 1.6ms** |
| pytest | **523 passed** (run via `WOOWA_SESSION_MODE=development python3 -m pytest tests/ -q`) |
| corpus rebuild readiness | **ready=true, rebuild_needed=false, 0 wrong exact owners** |
| index archive | **v1.0.2, 18.7MB, sidecars=true, SHA256 d8da5782...** |
| remote build | **H100 secure, encode 9.2s, Lance 13.40MB** |

## 1.2 Latest Y13 Release Snapshot

| 축 | 최신 결과 |
|---|---:|
| Release acceptance | **96/96 RELEASE READY** |
| Y13 gates | **47/47** |
| runtime LOC | **9502 / 9600** |
| qrels strict top1 / learner top1 / NDCG@5 / p95 | **1.000 / 1.000 / 0.987 / 278.6ms** |
| full scenario p50/p95 / evidence | **48.8ms / 71.5ms / 96.0%** |
| exact shortcut p50/p95 / cases | **0.009ms / 0.017ms / 8 synthetic cases** |
| F11 Stage 4 veto prompt | **P4 pass, 110.8ms** |
| override keywords p95 | **199.9ms** |
| stress test p50/p95 | **2.8ms / 6.4ms** |
| warm socket p50/p95 | **4.1ms / 6.3ms** |
| warm CLI p50/p95 | **43.9ms / 47.6ms** |
| first ask after ready p50/p95 | **187.2ms / 196.0ms** |
| first-answer total p50/p95 | **15245.0ms / 15247.7ms** |
| historical prewarm total-to-first-answer baseline | **43933.6ms** |

Y13-K5에서 AutoTokenizer resolution을 `PreTrainedTokenizerFast(tokenizer_file=...)` fast path로 대체했다. AutoTokenizer rollback은 `WOOWA_ENCODER_TOKENIZER_BACKEND=auto`이며, parity report는 input ids/masks 동일 + vector cosine min/mean **1.0/1.0**, max_abs_diff **0.0**이다. Y13-K6에서는 qrels strict 지표와 learner-relevant 지표를 분리하고, AF_UNIX line-delimited protocol에서 불필요한 client `shutdown(SHUT_WR)`를 제거했다. Y13-K7~K18에서는 lexical/title promotion, executable `bin/ask` fast path, search-result cache, override keyword routing, F11 Stage 4 veto prompt, exact normalization, corpus ownership 보강, remote-build provenance guard, latency hardening, corpus readiness gate, stale gold 정정, index archive verification을 순차 적용했다. 최종 qrels strict top1/learner top1/MRR은 **1.000/1.000/1.000**, NDCG@5는 **0.987**, p50/p95는 **176.1/278.6ms**다. 최신 14개 일반 시나리오 p50/p95는 **48.8/71.5ms**다.

## 1.3 Latest Fresh Onboarding Snapshot

| 축 | 최신 결과 |
|---|---:|
| Fresh clone live smoke | **bootstrap healthy, daemon alive, doctor 6/6, ask OK** |
| Fresh-state automation | **10/10 pass** |
| Phase U onboarding wrappers | **10/10 pass** |
| New mission readiness without anchors | **ready 4/4, cross_crew_not_applicable=true** |
| Existing mission readiness | **ready 4/4** |

## 2. Historical Phase Results

| Phase | 시나리오 수 | Pass | 핵심 발견 |
|---|---|---|---|
| J | 14 mode + 4 deepdive = 18 | 18/18 | mode dispatch 100%, 4.4× faster |
| K | F1 RAG + F5 mastery = 2 | 2/2 | top-5 93.4%, 5 mastered + **daemon history append 버그 fix** |
| L | 9 plan §verification | 9/9 | F11 AI judge 85%, F10 forward 100%, F10 backward 100% |
| M | 12 uncovered | 12/12 | historical in-process cold/warm probe; current daemon cold semantics are tracked by Y13 |
| N | 12 second-wave | 12/12 | persistence + idempotency + **read_history tail 버그 fix** |
| O | 12 drill unit + 1 e2e | 13/13 | F6 offer-gen 완성 (drill_pending + 4-dim 채점 + spaced) |
| P | 10 deep scenarios | 10/10 | drill cycle full, 100% corpus 커버, mastery 단조성 |
| **합계** | **77 시나리오 + 213 unit** | **77/77 + 213/213** | — |

## 3. Plan §verification 11 feature gate 매핑

| F# | Plan target | 결과 | 측정 |
|---|---|---|---|
| F1 | top-5 ≥ 85% | **93.4%** | 200 stratified queries from corpus.expected_queries |
| F2 | mentor concern ≥ 80% | **86.7%** | 15 anchor × rag.search top-5 title-keyword overlap |
| F3 | tool fast-path | 14/14 | Phase J 시나리오 dispatch |
| F4 | recurring ≥ 70% | **100%** mentor_repeat (2/2) | 31 anchors triplet group |
| F5 | mastered > 0 | **5 mastered + 2 proficient** (검증 fixture 기준) | mastery_graph.sqlite inspection |
| F6 | drill non-stub | **100% corpus 커버 + 4-dim 채점** | 12 unit + e2e + 100 random concept coverage |
| F7 | gap shrinks 2주 | **deferred** | 14-day longitudinal 필요 |
| F8 | prereq ≥ 80% | **100%** (10/10 sampled edges), broken edge 0 | current concept_graph 3339 nodes / 6172 prereq edges |
| F9 | mode tag 100% | **14/14** | Phase J |
| F10f Tier 1 | ≥ 90% | **100%** (35/35) | annotation × corpus + round-trip |
| F10f Tier 2 | ≥ 70% | **85.2%** (23/27) | method+exception+import × corpus |
| F10b | ≥ 75% | **100%** (10/10) | covered concepts → valid .java |
| F10 gap | ≥ 40% flagged → 30d mastered | **deferred** | 30-day longitudinal 필요 |
| F11 | precision ≥ 80% | **AI judge 85%** (8.5/10) | 10 sample = 5 high + 5 low conf |

## 4. Historical Baseline Comparison

최신 Y13 daemon 비교는 health-ready와 prewarm-ready를 섞지 않는다. 공정 비교는 stop→prewarm-ready→첫 답변 total 기준으로 본다.

| 지표 | woowa-learning-system | historical baseline | Δ |
|---|---:|---:|---:|
| first-answer total (fair probe) | **13562.3ms** | 43933.6ms | **3.24× faster** |
| first-answer total (canonical release) | **15247.7ms p95** | 43933.6ms | **2.9× faster** |
| warm CLI p95 | **47.6ms** | 431.7ms | **9.1× faster** |
| qrels production p95 | **278.6ms** | 1814.2ms | **6.5× faster** |
| qrels primary/strict top1 | **1.000** | 0.628 | **+37.2pp** |
| 14-scenario full comparison p50/p95 | **48.8ms / 71.5ms** | 70.7ms / 100.4ms | **1.4× / 1.4× faster** |
| override keyword dispatch | **4/4, p95 199.9ms** | same semantics confirmed | retrieval-noise fix |
| short exact concept queries | **8/8, p95 0.017ms** | 3-sample p95 546.6ms | **encoder-free + better top1 ownership** |

아래 표는 Phase J 당시 historical comparison이다.

| 지표 | woowa-learning-system | historical baseline | Δ |
|---|---|---|---|
| Mode dispatch correctness | **14/14 (100%)** | n/a (다른 schema) | current system 측정 가능 |
| p50 latency | **27.2ms** | 120.0ms | **4.4× faster** |
| p95 latency | **30.7ms** | 423.7ms | **13.8× faster** |
| Cold start | historical 105ms in-process probe | ~20-25s | superseded by Y13 daemon report |
| Evidence coverage (auto markers) | **96.0%** | 68.0% | +28pp |
| Prompt payload (avg) | ~4.1K chars | 48.6KB | **~11.8× smaller** |
| F5 mastered count | **5** | **0** | autoloop 검증 fixture |
| F10 forward/backward | **100% / 100%** | **없음** | current system unique |
| F11 cross-crew | **85% precision + Stage 4 veto prompt wired** | **없음** | current system unique |
| F6 drill offer-gen | **완성 (Phase O)** | 있음 | feature complete |

**현재 verdict**: qrels production, daemon usable-ready, exact shortcut, profile rebuild, mining, cognitive trigger, override keywords, 14개 일반 `bin/ask` full-scenario는 독립 시스템 기준 release-ready다. full-scenario p95 gate는 모든 run sample의 nearest-rank percentile, 절대 guardrail, p95 speedup, historical p95 이하 조건을 함께 강제한다.

## 5. 측정 과정에서 발견된 production 버그 2건

### Bug 1 (Phase K) — daemon ask handler가 `append_history_event` 호출 안 함
- 영향: 모든 학습자 `bin/ask` turn이 `history.jsonl`에 zero trace
- 결과: F5 mastery autoloop이 실제 daily 누적 안 됨 (당시 5 mastered는 offline replay)
- Fix: daemon `ask` 핸들러에서 매 turn `rag_ask` event 자동 append (mode/router_mode/top_concept_ids)
- 검증: 2 ask → history 10002→10004

### Bug 2 (Phase N) — `read_history(tail)` chunk size 부족
- 영향: `recent_history` block이 의도된 20 events 중 18개만 surface
- 결과: AI 세션이 학습자 직전 맥락 일부 누락
- Fix: chunk size 500B→1500B/event, 부족 시 iterative grow (1500→3000→6000→12000)
- 검증: tail=20 == full[-20:] 일치

두 버그 모두 noisy 하지 않아 평소 측정 안 됐을 것. 측정 cycle이 production 안정성에 직접 기여.

## 6. 보류 (longitudinal data 필요)

- **F7 gap shrinks 2주** — 학습자 weak concept이 14일 후 strengthen되는지 측정. 시간 외 측정 불가.
- **F10 gap flagged → 30d mastered** — gap detect된 concept이 30일 내 mastered로 진행되는지 측정. 시간 외 측정 불가.

두 gate 모두 학습자 daily 사용 시작 후 자연 누적 데이터로 측정. 실제 사용 데이터가 쌓인 뒤 14-30일 시점에서 별도 cycle.

## 7. Documented gaps (non-blockers)

- **learner_id state isolation** (Phase M S11): `profile.json` 공유 (single-learner 설계). 멀티 학습자 운영이면 per-learner state dir 필요.
- **Daemon single-thread** (Phase M S7): single-learner 설계 의도. concurrent parallel 요청은 target 아님.

두 항목 모두 plan 범위 외 / 학습자가 요청 시 follow-on.

## 8. 다음 cycle 후보

1. F7 / F10 gap longitudinal — 학습자 daily 14-30일 후 측정
2. F11 answer-quality sampling after Stage 4 veto prompt
3. F11 + coaching dual mode (Phase P P4)에서 더 풍부한 narrative 생성

## 9. 재현 명령

전체 검증을 한 번에:
```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-system
export WOOWA_SESSION_MODE=development

# 1. Unit tests (latest 523 passed)
python3 -m pytest tests/ -q

# 2. All 14 phase benches (J/K/L/M/N/P + T-X) via master runner
python3 tests/benchmarks/phase_y_all_benches.py   # 14/14 in ~20s
```

자세한 가이드: [`testing-guide.md`](testing-guide.md).

---

## 10. Phase T-X (2026-05-25/26) — automation expansion

**52 새 wrappers + 6 new modules + scripts/collection/ + scripts/mining/**

| Phase | 범위 | Pass |
|---|---|---|
| **T** Learner automation (7 wrappers) | learn-pr-retro · learn-record-code · learn-test · learn-response-quality · assess-learner-state · profile-recompute · session-start | 7/7 PASS + 17/17 e2e integration |
| **U** Onboarding/Collection (10, G1 closure) | bootstrap · bootstrap-repo · onboard-repo · list-repos · archive-status · sync-prs · repo-readiness · doctor · validate-state · registry-audit | 10/10 + 11/11 unit |
| **V** Coaching context (12) | coach-run · coach · my-pr · next-action · topic · reviewer · compare · compose-response · mission-map · rag-rewrite-prepare · rag-route-fallback · chunk-context-prepare | 10/10 |
| **W** Mining/Analytics (12) | feedback-mine · response-quality-mine · routing-analyze · learning-turn-audit · learning-path-graph-audit · reclassify-history · cohort-eval · cohort-compare · golden · rag-eval · router-generalization-eval · learner-log-rag-eval | 12/12 |
| **X** Maintenance + sub-commands (11) | index-pack · sync-index-metadata · drill-grade-prepare · learn-feedback · learn-self-assess · learn-drill · learner-profile · set-profile · show-profile · reviewer-profile · rag-remote-build | 11/11 + archive gate |
| **합계** | **52 wrappers** | **50/50 bench + 28/28 unit + archive gate** |

### Historical baseline 대비 성능 향상 (측정)

| Wrapper | historical baseline | woowa-learning-system | 향상 |
|---|---|---|---|
| learn-pr-retro p50 | 3000ms | 1.2ms | **2500× faster** |
| learn-record-code p95 | 200ms | 1.19ms | **168× faster** |
| learn-test parse | 1200ms | 3.6ms | **333× faster** |
| assess-learner-state p50 | 60000ms | 113ms | **528× faster** |
| coach-run | 310-1300ms | 96ms | **3-13× faster** |

### Discovery (Phase W5)
- corpus/concept_graph.json — **9 broken prereq edges** 발견 + fix (Phase Y3)
- 306 level inversions 잔존 (non-blocking, 다음 corpus curation cycle)

### Phase Y (2026-05-26) — final integration + cleanup

| Step | Result |
|---|---|
| Y1 모든 phase bench 통합 실행 | 14/14 PASS (회귀 0) |
| Y2 predecessor directory `.disabled` rename + self-contained 검증 | doctor 6/6 + ask + retro + status + readiness + mission-patterns 모두 PASS |
| Y3 9 broken edges fix | concept_graph 5764 → 5755 edges, broken 0 |
| Y5 Docs update (CLAUDE.md / AGENTS.md / docs/bin-reference.md) | Phase U/V/W/X auto-call contract 전부 반영 |

Phase 9 final acceptance criteria #8 ("predecessor directory 삭제 가능 검증") **PASS** (Y2).

### woowa-learning-system Phase T-X 당시 상태

- **bin/* entries**: 11 → **64** (predecessor non-probe target 58 → 64 with 6 improvements)
- **Unit tests**: 213 → **294** (Phase T-X +81)
- **Runtime LOC**: 4416 → **6225** (T-X 9500 budget의 65%)
- **외부 predecessor 의존**: **0** (Y2 검증 완료)
- **Latency p50**: **27ms** (historical baseline 120ms 대비 4.4× faster)
- **Prompt payload**: **~4.1K chars avg** (historical baseline 48.6KB 대비 약 11.8× smaller)
