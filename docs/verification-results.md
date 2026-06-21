# Verification results — current operations + corpus/retrieval historical phase results

최신 측정 날짜: 2026-06-22
브랜치: `research/top1-top5` → `main`
기준: 2026-06-22에는 세션-side 검색 파이프라인(HyDE 쿼리재작성 + CC fusion + margin-gated rerank + top-K 내용주입)을 구현·검증했다. 이전 운영/코퍼스 결과는 아래 historical section으로 보존한다.

## 0. 최신 — 세션-side 풀 파이프라인 (HyDE+CC+rerank+내용주입) (2026-06-22)

### 0.1 한 줄 요약
dense-top-1 핀 제거(CC α=0.92) + 세션 HyDE 키워드 확장 + dense top-5 세션 rerank + top-K summary/body 주입.
게이트 회귀 0(golden 58/58, eval_v2 PASS, pytest 734), 프롬프트 −248자, latency 순증 ~0(daemon +1ms).

### 0.2 헤드라인 — 게이트 가시 vs 풀 파이프라인 실측
| 측정 경로 | top1 | top5 | 비고 |
|---|---|---|---|
| golden (CC effect) | 58/58 | — | kafka 라벨 1개 갱신(리밸런스 artifact) |
| eval_v2 T-A (권위) | 0.756→0.800 | — | CC +4.4pp |
| eval_v2 T-C | 0.430→0.477 | 0.678→0.697 | CC +4.7pp |
| cohort real_learner_qrels | 0.756→0.800 | →0.900 | 권위 +4.4pp |
| **full_pipeline_eval (521, 4-arm)** | **0.484→0.860** | **0.718→0.929** | arm0 rrf→arm3 full, **+38pp** |

레버 기여(full_pipeline top1): CC +4pp(게이트 가시) / HyDE +24pp / rerank +9pp. **게이트는 CC만 보므로 +4pp, 풀 파이프라인 실측은 +38pp**(HyDE·rerank가 세션 행동이라 게이트 비가시). 검증 방식: `tests/benchmarks/full_pipeline_eval.py` (§testing-guide 2.7).

### 0.3 정직한 calibration
521 tier는 naive floor 측정. 실 학습자 82%가 기술용어 → 실세계 평균 이득은 +4~+38pp 사이(쿼리 naive 정도 비례), 큰 이득은 naive/짧은/absent 쿼리 + citation 정확도/환각↓에 집중.

### 0.4 재현
`python3 tests/benchmarks/full_pipeline_eval.py` (daemon CC 기본). golden: `bin/golden verify`. eval_v2: `python3 tests/benchmarks/eval_v2.py`.

## 0-old. telemetry/archive maintenance verification (2026-06-09)

### 0.1 한 줄 요약

학습 시스템 운영 데이터의 정합성을 정리했다. `learn-response-quality` fallback이 full body를 저장하면 pending capture도 즉시 `captured`로 갱신하도록 했고, 기존 stale pending은 `capture-repair --sync-pending`으로 재동기화했다. `learn-record-code`는 `missions/<repo>/...` 경로에서 repo를 자동 추론하도록 보강했고, 기존 `code_attempt` 29건도 repo 누락을 backfill했다. `spring-roomescape-waiting` PR archive는 라이브 재동기화 후 stale interrupted run을 failed로 정리했다.

### 0.2 검증 결과

| Gate | 결과 |
|---|---:|
| Unit tests | **696 passed** (`.venv/bin/python -m pytest tests/ -q`) |
| Focused regression | **45 passed** (`test_code_event.py`, `test_response_capture.py`, `test_response_quality.py`) |
| `bin/repo-readiness --repo spring-roomescape-waiting` | **4/4 ready**, cross-crew anchors **31** |
| `bin/validate-state` | **ok** |
| `bin/doctor` | **6/6 healthy** |
| `bin/learning-turn-audit --last 50` | **0 issues** |

### 0.3 데이터 정합성 정리

| 영역 | 처리 결과 |
|---|---:|
| PR archive sync (`spring-roomescape-waiting`) | run 19 succeeded, **91 PRs**, **2190 review comments** |
| stale collection run cleanup | interrupted run 18 → `failed` 처리, SQLite integrity ok, journal 없음 |
| pending capture sync | checked **580**, synced **40**, skipped_missing_body **0** |
| pending capture status after sync | captured **166**, superseded **409**, pending **5** |
| `code_attempt` repo backfill | missing repo **29 → 0**, total **50** all mapped to `spring-roomescape-waiting` |
| derived artifacts rebuilt | profile, meta_analytics, learning_path, memory_review, cross_mission, pr_review, pr_meta, thread_recon, temporal, reviewer_profile |

### 0.4 남은 관찰

`bin/learning-turn-audit --last 200 --strict`에는 2026-05월의 오래된 품질 이슈 16건이 남아 있다. 최신 50턴 audit은 0건이고, 이번 정비 범위에서는 현재 운영 경로의 pending/full-body/code_attempt/archive 정합성만 대상으로 삼았다.

## 1. 이전 — Corpus 확장·심화 + dense index v1.0.3 (2026-05-31)

### 0.1 한 줄 요약

`auto/corpus-expand`에서 **14개 작성 사이클**(매 사이클 비-GPU 게이트 통과 후 commit)로 코퍼스를 확장·심화했다: 신규 개념 **11개**(network 4·language 3·security 2·system-design 1·design-pattern 1, 표준 CS 커리큘럼 누락 근거), 학습자 재질문 클러스터 **5개**(version-confusion·transaction-boundary·optimistic-vs-pessimistic·isolation·non-repeatable-read↔replica-lag) enrich, gap 해소 **4개**(MVCC engine-support·storage-engine/InnoDB·`@Configuration` vs `@Component`·H2 isolation), thin-body 심화 **20개**(body-only, dense-neutral), 코퍼스 전체 **summary 복구**(58% broken → 0). 누적 본문 **+58.4k자**. dense 재빌드 **1회**(RunPod A40, 3339 → 3350 concepts).

**accept authority(실 학습 history 기반 frozen qrels, 40 ranking-eligible)** 기준 top1 **0.725 → 0.750 (+2.5pp)**, NDCG@5 +0.5pp, MRR +2.3pp, forbidden 8 → 6, learner_alignment 0.96 유지, p95 +2.0%(예산 내) — `cohort-compare --fail-on-drift` **PASS**. r3 합성 cross-check(180 q)는 top1 −1.7pp로 drift gate를 건드리지만, top5 이탈 11건 중 **9건이 라벨 아티팩트**(동일주제 형제 개념이 top5에 잔존 — +11 신규 개념으로 근접-동률이 재정렬됨), **2건만 진짜 miss**(`mvcc-read-view-consistent-read-internals`, `mvc-controller-basics`)다. 게이트 all green: strict load 3350/0, graph broken=0·orphan=0, readiness 6/6, golden 2/2, router 31/31, **pytest 526 passed**.

### 0.2 헤드라인 델타 — 실데이터 frozen accept set (핵심, control = index v1.0.2)

| 지표 | v1.0.2 | v1.0.3 | Δ | 게이트 |
|---|---:|---:|---:|---|
| top1_match_rate | 0.725 | **0.750** | **+2.5pp** | ✅ (fail-on-drift PASS) |
| top5_match_rate | 0.95 | 0.95 | 0 | — |
| recall@5 | 0.785 | 0.769 | −1.6pp | — (fail field 아님) |
| NDCG@5 | 0.744 | 0.749 | +0.5pp | ✅ |
| MRR | 0.815 | 0.838 | +2.3pp | — |
| learner_alignment | 0.96 | 0.96 | 0 | — |
| forbidden_hit_count | 8 | 6 | **−2** | ✅ 비상승 |
| p95 ms (cold) | 178.2 | 181.7 | +2.0% | ✅ <10% |

(ranking-eligible 40/50. corpus-gap probe 5건 등은 ranking 제외. control은 cold-cache 재측정값 — 초기 cache-warm 측정의 p95 아티팩트 제거.)

### 0.3 교차검증 — r3 합성 qrels (180 q) + 회귀 진단

| 지표 | v1.0.2 | v1.0.3 | Δ |
|---|---:|---:|---:|
| top1_match_rate | 0.767 | 0.750 | −1.7pp |
| top5_match_rate | 0.95 | 0.889 | −6.1pp |
| recall@5 | 0.918 | 0.859 | −5.9pp |
| NDCG@5 | 0.845 | 0.804 | −4.1pp |
| MRR | 0.842 | 0.806 | −3.6pp |
| forbidden_hit_count | 2 | 2 | 0 |
| p95 ms (cold) | 57.7 | 60.1 | +4.2% |

`cohort-compare --fail-on-drift`: 실데이터 **PASS**, 합성 **FAIL**(top1·ndcg drift). 합성 하락의 근본 원인 — top5에서 이탈한 expected 개념 11건(신규 진입 0건)을 per-query로 분류하면:

- **라벨 아티팩트 9건**: candidate top5에 동일주제 형제 개념이 잔존한다. 예) `registry-primer…` → `registry-pattern`(더 정준), filter-chain bridge → 정준 `spring-security-filter-chain`, `replica-lag…` → `db-read-replica-write-primary-routing-primer`(#2). +11 신규 개념으로 dense 공간이 이동하며 **근접-동률 co-relevant 개념들의 순위가 뒤바뀐 것**이고, 단일 라벨 합성 qrels는 동등하게 타당한 형제 개념을 정답으로 인정하지 못한다.
- **진짜 miss 2건**: `mvcc-read-view-consistent-read-internals`(lock/connection-pool 개념으로 대체), `mvc-controller-basics`(network/http 개념으로 대체). top5에 등가 개념이 없다. 다음 cycle에서 card 필드 보강 후보.

accept authority(실 history)는 전 지표 개선·forbidden 감소했고, 합성 하락은 ~80%가 라벨 아티팩트라 **품질 저하가 아닌 코퍼스 성장의 부수효과**로 판단해 배포했다(사용자 승인).

### 0.4 무엇이 추가·심화됐나 (카테고리별 + 근거)

| 종류 | 수 | 근거 | 비고 |
|---|---:|---|---|
| 신규 개념 (Track C) | 11 | curriculum 누락 + corpus_gap_probe | network 4, language 3, security 2, system-design 1, design-pattern 1 |
| 재질문 클러스터 enrich (Track A+B, tier-1) | 5 | 학습자 history 재질문·uncertain | version-confusion, transaction-boundary, optimistic-vs-pessimistic, isolation misconception, non-repeatable-read↔replica-lag |
| gap 해소 enrich (Track A, tier-2) | 4 | corpus_gap_probe + history(예: `@Configuration` vs `@Component` 52회 재질문) | MVCC engine-support, storage-engine/InnoDB, `@Configuration` vs `@Component`, H2 isolation/locking |
| thin-body 심화 (Track B, body-only) | 20 | depth audit(body<2600 또는 헤더<3) | cycles 13·14, dense-neutral(encoding_text 불변) |
| summary 복구 (품질) | 코퍼스 전체 | 58% broken summary | body 한줄요약 기반 content-grounded 복구 |

누적 본문 **+58,396자**. dense 벡터를 바꾸는 편집(신규·alias·expected_query·summary)은 최종 1회 재빌드로 반영, body 심화는 dense-neutral.

### 0.5 검증 요약 (overfit 가드)

- 14 사이클 × 매 사이클 비-GPU 게이트(strict load·graph audit·readiness·lexical·pytest·golden·router) + 서브에이전트 독립 CS 정확도+풍부함 리뷰(작성자≠검증자). reject된 콘텐츠는 정정 후 반영.
- 최종 dense 게이트 all green: strict load 3350/0, graph broken=0·orphan=0(cycle/inversion은 main과 동일한 기존값), readiness 6/6(`ready_for_dense_rebuild=True`), golden 2/2, router 31/31, pytest 526.
- **overfit 가드**: alias/expected_query는 qrels 라벨이 아닌 학습자 실제 history 표현에서 도출, 합성 qrels는 독립 cross-check로만 사용. accept authority(real frozen) 개선 + 합성 하락의 라벨-아티팩트 진단으로 enrich-to-test 아님을 확인.

### 0.6 배포 / 비용 / 안전

- main merge: `77f47f9` (auto/corpus-expand, 15 commits, --no-ff).
- release: **`paradigm-v2-index-v1.0.3`** — `state/index.tar.zst` 18.6MB, SHA256 `74fa417d213a31cd011208118bde276966a369005b813fc630c2a3a72d87dd7d`, dense_corpus_sha256 `d511b244…`, full_corpus_sha256 `9dbbc96d…`. `bin/sync-index-metadata --tag paradigm-v2-index-v1.0.3`로 manifest + 문서 토큰 갱신.
- 빌드: RunPod **A40 (SECURE)** 1회 dense encode(3350 concepts, encode 18.4s), ~$0.1–0.3. (A100 80GB PCIe·A40 COMMUNITY는 capacity 부족으로 A40 SECURE로 전환.)
- 롤백: `bin/index-fetch --tag paradigm-v2-index-v1.0.2` (1커맨드) + 코드 `git revert 77f47f9`.

### 0.7 재현

```bash
export WOOWA_SESSION_MODE=development
# control(v1.0.2)은 cold-cache로 재측정: index swap 후 daemon 재시작 → cohort-eval
bin/cohort-eval --qrels tests/fixtures/real_learner_qrels_v1.json --out reports/fin_control_real_cold.json --top-k 5 --relations-expand 5
bin/cohort-eval --qrels tests/fixtures/r3_qrels_real_v1.json   --out reports/fin_control_synth_cold.json --top-k 5 --relations-expand 5
# candidate(v1.0.3)
bin/cohort-eval --qrels tests/fixtures/real_learner_qrels_v1.json --out reports/fin_cand_real.json --top-k 5 --relations-expand 5
bin/cohort-eval --qrels tests/fixtures/r3_qrels_real_v1.json   --out reports/fin_cand_synth.json --top-k 5 --relations-expand 5
# gate
bin/cohort-compare --control reports/fin_control_real_cold.json  --candidate reports/fin_cand_real.json  --fail-on-drift   # PASS
bin/cohort-compare --control reports/fin_control_synth_cold.json --candidate reports/fin_cand_synth.json --fail-on-drift   # FAIL(라벨 아티팩트, §0.3)
```

report 경로: `reports/fin_{control,cand}_{real,synth}.json` (+ cold control), 사이클 로그 `reports/corpus_loop_log.jsonl`(gitignored).

## 0′ 직전 cycle — Retrieval 재설계 (Pillar 0+2, 2026-05-31)

### 0.1 한 줄 요약

실제 학습 prompt로 라벨링한 `real_learner_qrels_v1`(50 q, 40 ranking-eligible) 기준, **top1 0.400 → 0.725 (+32.5pp)**, MRR +21.4pp, NDCG@5 +11.7pp, forbidden 9 → 8, p95 회귀 +0.5%(예산 내). 합성 qrels(180 q) 교차검증도 top1 +2.8pp / NDCG +1.1pp로 비회귀. 이 이득은 **전부 코드(Pillar 0 rerank fix + Pillar 2 router gate)** 에서 나오며 **인덱스는 v1.0.2 그대로**다 → 새 release 불필요(학습자는 `git pull`로 코드만 갱신, 기존 index 유지). Pillar 1 chunk 인덱스는 RunPod에서 빌드(26,188 chunks)했으나 실데이터에서 single-vector 대비 top1·recall·ndcg를 **희석**(top1 0.725→0.625)해 reject·복원했다. full pytest **523 passed**, golden **2/2**, router-generalization **27/27 (acc 1.000)**.

### 0.2 헤드라인 델타 — 실데이터 qrels (핵심, control=baseline v1.0.2-equiv)

| 지표 | baseline | 배포본 | Δ | 게이트 |
|---|---:|---:|---:|---|
| top1_match_rate | 0.400 | **0.725** | **+32.5pp** | ✅ (fail-on-drift PASS) |
| recall@5 | 0.773 | 0.785 | +1.2pp | — |
| MRR | 0.601 | **0.815** | +21.4pp | — |
| NDCG@5 | 0.627 | **0.744** | +11.7pp | ✅ |
| learner_alignment | 0.947 | 0.960 | +1.3pp | — |
| forbidden_hit_count | 9 | 8 | −1 | ✅ 비상승 |
| p50 / p95 ms | 48.4 / 179.4 | 60.1 / 180.3 | +0.5% p95 | ✅ <10% |

(ranking-eligible 40/50. corpus-gap probe 8건·forbidden 2건 등은 ranking 제외.)

### 0.3 교차검증 — 합성 qrels (회귀 안전, 180 q)

| 지표 | baseline | 배포본 | Δ |
|---|---:|---:|---:|
| top1_match_rate | 0.739 | 0.767 | +2.8pp |
| recall@5 | 0.925 | 0.918 | −0.7pp (fail field 아님) |
| MRR | 0.825 | 0.842 | +1.7pp |
| NDCG@5 | 0.834 | 0.845 | +1.1pp |
| forbidden_hit_count | 2 | 2 | 0 |
| p95 ms | 53.6 | 50.9 | −5.0% |

`cohort-compare --fail-on-drift`: 실·합성 **둘 다 PASS**(accuracy_drift 0, top1/ndcg drift ≥ 0, p95 회귀 < 10%).

### 0.4 도달성 — Pillar 2 시맨틱 라우터 게이트 (tier_0 구제)

cohort-eval은 라우터 독립(pure retrieval)이라 게이트 이득을 안 잡는다. 라우터 측 증거:
- `router-generalization-eval`: **27/27 (acc 1.000)** — cs_qa **15/15**(신규 MVCC/동시성 fixture 포함), tier_0_fallback **3/3**(weather/dinner/예약 가드 유지). **양방향**(CS 구제 + 비-CS 차단) 입증.
- 라이브 확인: `"MVCC가 뭐야"` → 기존 tier_0 → 이제 **cs_qa** (reason: `semantic rescue: lexical guard missed but nearest concept cosine 1.000 >= 0.560`).
- `reclassify-history --last 1000`: lexical drift **0** — 게이트는 base 라우터를 안 바꾸고 daemon 레이어에서 **가산적**으로만 구제(부작용 없음).

### 0.5 Pillar 1 (chunk body dense) — 측정 후 reject (negative result)

| 지표 (실데이터) | single-vector | chunk (pen=1.0) | 판정 |
|---|---:|---:|---|
| top1 | 0.725 | 0.625 | 희석 ↓ |
| recall@5 | 0.785 | 0.746 | ↓ |
| NDCG@5 | 0.744 | 0.668 | ↓ |
| forbidden | 8 | 3 | ↑(유일 이득) |

search-side rescue 스윕(overfetch×body-penalty, 재빌드 없음): `body_penalty ≤ 0.80`이면 chunk가 single-vector **수치와 정확히 일치**(card가 max-pool을 항상 이김). top1·recall 동시 우위 config **없음**. 실제 학습 질문이 concept 수준("MVCC가 뭐야", "낙관적 락")이라 이미 card(title|aliases|expected_queries|summary)로 도달 가능 → body window는 max-pool 노이즈만 추가. chunk index는 **66MB vs 14MB(5×)** 로 학습자 fetch에도 불리. 가설 기각, `f3afab6` revert(`b2a5406`).

### 0.6 라벨 신뢰도 (Phase M)

`authoring_method: behavioral_evidence_plus_ai_semantic_join`. 40 ranking-eligible 라벨 근거: **behavioral 16 / semantic 24 / none 10**(none = corpus-gap probe, 의도적 unanswerable). cohort 분포: mission_bridge 16, confusable_pairs 13, paraphrase_human 10, corpus_gap_probe 8, forbidden_neighbor 2, symptom_to_cause 1. 근거 1줄/라벨은 `reports/qrels_label_rationale.jsonl`(50줄, gitignored). 합성 qrels 교차검증으로 한쪽 라벨 편향 상호 견제.

### 0.7 무엇이 배포됐나 / 비용 / 안전

- **main 머지(green-gated)**: Phase M qrels fixture(`8833698`) + Pillar 0 rerank fix(`b6e0cc8`) + Pillar 2 router gate(`e3b4639`) + RunPod 빌드 tooling fix(`5219477`) + Pillar 1 revert(`b2a5406`).
- **새 index release 없음** — 인덱스는 v1.0.2 그대로(이득이 코드-온리). 학습자 영향: `git pull` 후 즉시 적용, `index-fetch` 재실행 불필요.
- **RunPod**: 1 빌드(A40 SECURE, chunk index 26,188 chunks, ~$0.05), reject 후 pod 정상 종료(orphan 0).
- **안전**: `WOOWA_SESSION_MODE=development` 유지. `state/learner/*.jsonl`은 gitignored → 게이트 fixture prompt가 main/public에 안 들어감. force-push 없음, 미통과본 배포 없음.
- **알림(로컬 history)**: 이번 무인 cycle의 eval/golden 실행이 로컬 `state/learner/history.jsonl`에 dev-mode rag_ask **10건**(MVCC smoke 1 + golden tool_only 3×3, ts ≈ 1780165539~1780166684)을 남겼다. gitignored라 main/public clone엔 영향 없다. "`state/learner` 변경 절대 금지" 하드 제약이 cleanup 편집보다 우선하므로 **삭제하지 않고 그대로 보존**했다(진짜 학습 데이터 오삭제 위험 회피). 원하면 사람이 위 ts 범위 라인만 직접 제거 가능 — gitignored라 git 복구는 불가하니 백업 후 진행 권고.
- **보류(다음 supervised 세션 권고)**: evidence enrichment(history 신호 기반 aliases/expected_queries 보강). 자가 라벨 qrels에만 검증 가능 → overfit/teach-to-test 위험 + 학습자 노출 index release를 동반하므로, 무인 배포 대신 corpus diff·label 도출을 사람이 검토하는 세션에서 진행 권고. `curation/mine_history.py`는 실제 `history.jsonl` payload 스키마(prompt/router_mode/top_concept_ids…)에 맞춘 어댑터 필요.

### 0.8 재현

```bash
export WOOWA_SESSION_MODE=development
bin/rag-daemon start-bg && bin/rag-daemon ping
bin/cohort-eval --qrels tests/fixtures/real_learner_qrels_v1.json --out reports/deploy_real.json
bin/cohort-compare --control reports/baseline_real.json --candidate reports/deploy_real.json --fail-on-drift
bin/cohort-eval --qrels tests/fixtures/r3_qrels_real_v1.json --out reports/deploy_synth.json
bin/cohort-compare --control reports/baseline_synth.json --candidate reports/deploy_synth.json --fail-on-drift
bin/golden verify && bin/router-generalization-eval
WOOWA_SESSION_MODE=development python3 -m pytest tests/ -q   # 당시 523 passed
```
`reports/`는 gitignored(baseline/deploy/sweep/auto_loop_log 산출물). chunk 인덱스 롤백 불필요(미배포). 코드 롤백은 `b2a5406` 또는 main merge revert.

## 2. Y14 closure historical snapshot

Y14 corpus closure는 corpus **3339 concepts**, `concept_graph.json` **6172 prerequisite edges**, broken edge **0** 상태로 닫았다. Batch 1-7 qrels prompt/reformulated 14세트는 strict top1/top5/MRR/NDCG **1.000**, forbidden **0**, latency p95 최대 **3.6ms**다. Remote dense index는 H100 secure에서 빌드했고, archive SHA256은 `d8da5782c6fdceeec34e541a30e511bf2f8d168c01dab4e47dfefcde641921dc`, Lance size는 **13.40MB**, archive size는 **18.7MB**다. Corpus readiness는 **ready=true / rebuild_needed=false**이고, full pytest는 **523 passed**다.

## 2.1 Historical Y14 Snapshot

| 축 | 최신 결과 |
|---|---:|
| Corpus concepts / graph | **3339 concepts / 6172 prereq edges / broken 0** |
| Y14 qrels prompt/reformulated | **14/14 sets top1=1.000, top5=1.000, forbidden=0, max p95=3.6ms** |
| rag_quality top1 / NDCG@5 / p95 | **1.000 / 1.000 / 1.6ms** |
| pytest | **523 passed** (run via `WOOWA_SESSION_MODE=development python3 -m pytest tests/ -q`) |
| corpus rebuild readiness | **ready=true, rebuild_needed=false, 0 wrong exact owners** |
| index archive | **v1.0.2, 18.7MB, sidecars=true, SHA256 d8da5782...** |
| remote build | **H100 secure, encode 9.2s, Lance 13.40MB** |

## 2.2 Historical Y13 Release Snapshot

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

## 2.3 Historical Fresh Onboarding Snapshot

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

# 1. Unit tests (current 696 passed)
python3 -m pytest tests/ -q

# 2. All 14 phase benches (J/K/L/M/N/P + T-X) via master runner
python3 tests/benchmarks/phase_y_all_benches.py   # 14/14 in ~20s
```

자세한 가이드: [`testing-guide.md`](testing-guide.md).

---

## 10. Phase T-X (2026-05-25/26) — automation expansion

**51 새 wrappers + 6 new modules + scripts/collection/ + scripts/mining/**

| Phase | 범위 | Pass |
|---|---|---|
| **T** Learner automation (7 wrappers) | learn-pr-retro · learn-record-code · learn-test · learn-response-quality · assess-learner-state · profile-recompute · session-start | 7/7 PASS + 17/17 e2e integration |
| **U** Onboarding/Collection (10, G1 closure) | bootstrap · bootstrap-repo · onboard-repo · list-repos · archive-status · sync-prs · repo-readiness · doctor · validate-state · registry-audit | 10/10 + 11/11 unit |
| **V** Coaching context (11) | coach-run · coach · my-pr · next-action · topic · reviewer · compare · mission-map · rag-rewrite-prepare · rag-route-fallback · chunk-context-prepare | 9/9 |
| **W** Mining/Analytics (12) | feedback-mine · response-quality-mine · routing-analyze · learning-turn-audit · learning-path-graph-audit · reclassify-history · cohort-eval · cohort-compare · golden · rag-eval · router-generalization-eval · learner-log-rag-eval | 12/12 |
| **X** Maintenance + sub-commands (11) | index-pack · sync-index-metadata · drill-grade-prepare · learn-feedback · learn-self-assess · learn-drill · learner-profile · set-profile · show-profile · reviewer-profile · rag-remote-build | 11/11 + archive gate |
| **합계** | **51 wrappers** | **49/49 bench + 28/28 unit + archive gate** |

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

---

## 11. Live PR review cycle (2026-06-02)

PR #391 리뷰 사이클에서 "미답변" 정합을 반복 오판한 근본 원인(시스템에 라이브 + 본인 PENDING 인지 경로 부재)을 메우는 기능. 매 호출 GitHub fresh 3-소스 정합(멘토 원댓글 ∖ (제출답글 ∪ 본인 PENDING 초안)) + GraphQL resolved/outdated/`reviewDecision` 오버레이 + 직전 스냅샷 대비 델타.

**추가/변경**
- `scripts/collection/github_client.py` — `fetch_pull_request_review_comments_for_review`, `fetch_review_threads`(GraphQL), `_run_graphql`.
- `core/pr_threads.py` (신규) — `reconcile_pr_threads(...)` 엔진(REST spine + GraphQL 오버레이, 스냅샷 델타).
- `bin/pr-thread-status` (신규) — 라이브 진입점(read-only, `--silent` JSON + 한국어 narration).
- `bin/learn-pr-retro --live` — 아카이브 회고의 stale "unresolved"를 라이브 status로 정정.
- **답글 문구 생성은 시스템 기능 아님** — 세션이 미답변 스레드(`diff_hunk`+멘토 원문)를 보고 직접 초안. `compose-response --live`는 사용자 지시로 폐기.

**검증 (2026-06-02)**
| 항목 | 결과 |
|---|---|
| `pytest tests/ -q` | **681 passed** (회귀 0) |
| `bin/router-generalization-eval` | exit 0, failures [] (새 router 모드 없음) |
| `bin/golden verify` | 2/2 pass |
| meta-runner `phase_y_all_benches.py` | **13/14** — 13 무조건 PASS; `phase_t_e2e_integration.py`는 7 wrapper STEP 모두 green이나 S2가 라이브 학습자 진척(experience=advanced + mastered≥5)을 assert → DongKey777 현재 0 mastered/9 proficient/620 events라 미충족(코드 회귀 아님, 학습 진척 마일스톤 미도달) |
| 라이브 스모크 PR #391 | 17 스레드 전부 `jurlring`, `reviewDecision=CHANGES_REQUESTED`, 유일 unanswered = Reservation.java root, 재실행 시 델타 비어 idempotent |

`bin/*` entries: 64 → **81** (Mode feature builders 14 + Live PR `pr-thread-status` 신규, `compose-response` 제거 — 답글 문구 생성은 시스템 기능 아님).
