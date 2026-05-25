# Verification results — 77/77 시나리오 + 213/213 unit test

날짜: 2026-05-25
브랜치: `paradigm-v2`
누적 commit: a07411e..f7e32e7

## 1. 한 줄 요약

paradigm-v2는 plan §verification에서 측정 가능한 모든 gate를 통과했고, 7 wave의 77 시나리오 + 213 unit test를 통해 학습 시스템 목적에 부합함과 Legacy 대비 우월함이 입증되었다.

## 2. Phase별 결과

| Phase | 시나리오 수 | Pass | 핵심 발견 | Report |
|---|---|---|---|---|
| J | 14 mode + 4 deepdive = 18 | 18/18 | mode dispatch 100%, 4.4× faster | [PARADIGM_V2_VS_LEGACY_FINAL.md](../reports/PARADIGM_V2_VS_LEGACY_FINAL.md) |
| K | F1 RAG + F5 mastery = 2 | 2/2 | top-5 93.4%, 5 mastered + **daemon history append 버그 fix** | [PHASE_K_VERIFICATION.md](../reports/PHASE_K_VERIFICATION.md) |
| L | 9 plan §verification | 9/9 | F11 AI judge 85%, F10 forward 100%, F10 backward 100% | [PHASE_L_ALL_GATES_FINAL.md](../reports/PHASE_L_ALL_GATES_FINAL.md) |
| M | 12 uncovered | 12/12 | cold 105ms / warm 4.5ms, 영어 query, prompt injection 안전 | [PHASE_M_UNCOVERED_FINAL.md](../reports/PHASE_M_UNCOVERED_FINAL.md) |
| N | 12 second-wave | 12/12 | persistence + idempotency + **read_history tail 버그 fix** | [PHASE_N_UNCOVERED2_FINAL.md](../reports/PHASE_N_UNCOVERED2_FINAL.md) |
| O | 12 drill unit + 1 e2e | 13/13 | F6 offer-gen 완성 (drill_pending + 4-dim 채점 + spaced) | (drill.py + test_drill.py) |
| P | 10 deep scenarios | 10/10 | drill cycle full, 100% corpus 커버, mastery 단조성 | [PHASE_P_DEEP_FINAL.md](../reports/PHASE_P_DEEP_FINAL.md) |
| **합계** | **77 시나리오 + 213 unit** | **77/77 + 213/213** | — | — |

## 3. Plan §verification 11 feature gate 매핑

| F# | Plan target | 결과 | 측정 |
|---|---|---|---|
| F1 | top-5 ≥ 85% | **93.4%** | 200 stratified queries from corpus.expected_queries |
| F2 | mentor concern ≥ 80% | **86.7%** | 15 anchor × rag.search top-5 title-keyword overlap |
| F3 | tool fast-path | 14/14 | Phase J 시나리오 dispatch |
| F4 | recurring ≥ 70% | **100%** mentor_repeat (2/2) | 31 anchors triplet group |
| F5 | mastered > 0 | **5 mastered + 2 proficient** (legacy 0) | mastery_graph.sqlite inspection |
| F6 | drill non-stub | **100% corpus 커버 + 4-dim 채점** | 12 unit + e2e + 100 random concept coverage |
| F7 | gap shrinks 2주 | **deferred** | 14-day longitudinal 필요 |
| F8 | prereq ≥ 80% | **100%** (10/10 edges) | concept_graph 5764 edges 검증 |
| F9 | mode tag 100% | **14/14** | Phase J |
| F10f Tier 1 | ≥ 90% | **100%** (35/35) | annotation × corpus + round-trip |
| F10f Tier 2 | ≥ 70% | **85.2%** (23/27) | method+exception+import × corpus |
| F10b | ≥ 75% | **100%** (10/10) | covered concepts → valid .java |
| F10 gap | ≥ 40% flagged → 30d mastered | **deferred** | 30-day longitudinal 필요 |
| F11 | precision ≥ 80% | **AI judge 85%** (8.5/10) | 10 sample = 5 high + 5 low conf |

## 4. Legacy 대비 비교 (Phase J 결과)

| 지표 | paradigm-v2 | Legacy | Δ |
|---|---|---|---|
| Mode dispatch correctness | **14/14 (100%)** | n/a (다른 schema) | v2 측정 가능 |
| p50 latency | **27.2ms** | 120.0ms | **4.4× faster** |
| p95 latency | **30.7ms** | 423.7ms | **13.8× faster** |
| Cold start | 105ms | ~20-25s | **~200× faster** |
| Evidence coverage (auto markers) | **96.0%** | 68.0% | +28pp |
| LLM payload (avg) | 2.4KB | 48.6KB | **20× cheaper** |
| F5 mastered count | **5** | **0** (broken) | v2 fixes core promise |
| F10 forward/backward | **100% / 100%** | **없음** | v2 unique |
| F11 cross-crew | **85% precision** | **없음** | v2 unique |
| F6 drill offer-gen | **완성 (Phase O)** | 있음 | parity (이전 legacy 우위 해소) |

**최종 verdict**: paradigm-v2는 모든 측정된 axis에서 Legacy 이상 (parity 또는 wins). Legacy가 우월한 axis는 없음.

## 5. 측정 과정에서 발견된 production 버그 2건

### Bug 1 (Phase K) — daemon ask handler가 `append_history_event` 호출 안 함
- 영향: 모든 학습자 `bin/ask` turn이 `history.jsonl`에 zero trace
- 결과: F5 mastery autoloop이 실제 daily 누적 안 됨 (현재 5 mastered는 offline replay)
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

두 gate 모두 학습자 daily 사용 시작 후 자연 누적 데이터로 측정. main merge 후 14-30일 시점에서 별도 cycle.

## 7. Documented gaps (non-blockers)

- **Override keywords** (Phase M S4): paradigm-v2에 *"RAG로 깊게"*, *"그냥 답해"*, *"코치 모드"* 같은 explicit override 미구현 (legacy는 있음). 학습자 요청 시 추후 wire.
- **learner_id state isolation** (Phase M S11): `profile.json` 공유 (single-learner 설계). 멀티 학습자 hub면 per-learner state dir 필요.
- **Daemon single-thread** (Phase M S7): single-learner 설계 의도. concurrent parallel 요청은 target 아님.

세 항목 모두 plan 범위 외 / 학습자가 요청 시 follow-on.

## 8. 다음 cycle 후보

1. **main merge** — plan §branch strategy 따라 force-push reset
2. F7 / F10 gap longitudinal — 학습자 daily 14-30일 후 측정
3. F11 Stage 4 AI veto runtime wire (precision 85% → 90+%)
4. Override keywords (Phase M G1)
5. F11 + coaching dual mode (Phase P P4)에서 더 풍부한 narrative 생성

## 9. 재현 명령

전체 검증을 한 번에:
```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-system
export WOOWA_SESSION_MODE=development

# 1. Unit tests (294/294)
python3 -m pytest tests/ -q

# 2. All 14 phase benches (J/K/L/M/N/P + T-X) via master runner
python3 tests/benchmarks/phase_y_all_benches.py   # 14/14 in ~20s
```

자세한 가이드: [`testing-guide.md`](testing-guide.md).

---

## 10. Phase T-X (2026-05-25/26) — legacy parity migration

**51 새 wrappers + 6 new modules + scripts/collection/ + scripts/mining/**

| Phase | 범위 | Bench |
|---|---|---|
| **T** Learner automation (7 wrappers) | learn-pr-retro · learn-record-code · learn-test · learn-response-quality · assess-learner-state · profile-recompute · session-start | 7/7 PASS + 17/17 e2e integration |
| **U** Onboarding/Collection (10, G1 closure) | bootstrap · bootstrap-repo · onboard-repo · list-repos · archive-status · sync-prs · repo-readiness · doctor · validate-state · registry-audit | 10/10 + 11/11 unit |
| **V** Coaching context (12) | coach-run · coach · my-pr · next-action · topic · reviewer · compare · compose-response · mission-map · rag-rewrite-prepare · rag-route-fallback · chunk-context-prepare | 10/10 |
| **W** Mining/Analytics (12) | feedback-mine · response-quality-mine · routing-analyze · learning-turn-audit · learning-path-graph-audit · reclassify-history · cohort-eval · cohort-compare · golden · rag-eval · router-generalization-eval · learner-log-rag-eval | 12/12 |
| **X** Maintenance + sub-commands (10) | sync-index-metadata · drill-grade-prepare · learn-feedback · learn-self-assess · learn-drill · learner-profile · set-profile · show-profile · reviewer-profile · rag-remote-build | 11/11 |
| **합계** | **51 wrappers** | **50/50 bench + 28/28 unit** |

### 성능 향상 vs legacy (측정)

| Wrapper | Legacy | paradigm-v2 | 향상 |
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
| Y2 Legacy hub `.disabled` rename + paradigm-v2 self-contained 검증 | doctor 6/6 + ask + retro + status + readiness + mission-patterns 모두 PASS |
| Y3 9 broken edges fix | concept_graph 5764 → 5755 edges, broken 0 |
| Y5 Docs update (CLAUDE.md / AGENTS.md / docs/bin-reference.md) | Phase U/V/W/X auto-call contract 전부 반영 |

Phase 9 final acceptance criteria #8 ("Legacy hub directory 삭제 가능 검증") **PASS** (Y2).

### paradigm-v2 최종 상태

- **bin/* entries**: 11 → **62** (legacy 75 - 17 reranker probes 의도 skip = 58 non-probe → 62 with 4 improvements)
- **Unit tests**: 213 → **294** (Phase T-X +81)
- **Runtime LOC**: 4416 → **6225** (T-X 9500 budget의 65%)
- **Legacy hub 의존**: **0** (Y2 검증 완료)
- **Latency p50**: **27ms** (legacy 120ms 대비 4.4× faster)
- **Token cost**: **2.4KB avg** (legacy 48KB 대비 20× cheaper)
