# Corpus expansion — monotonic quality

코퍼스는 계속 확장·심화되어야 하지만 **기존 query의 품질이 절대 회귀하면 안 된다**. 이 문서는 그걸 보장하는 검증된 프로세스를 박제한다. (모든 수치는 2026-06-24 실측, 코퍼스 3654 concepts.)

## 근본원인 (가설→실측으로 교정)

exact-query 단축(`rag/search._exact_query_shortcut_hits`)은 query를 정규화해 tier 우선순위(`expected_queries` > `aliases` > `title`)로 단일 concept에 해석한다. 한 tier에서 owner가 정확히 1개면 그 concept가 winning, 2개 이상이면 ambiguity guard가 `[]`를 반환(dense 폴백)한다. **소유권은 코퍼스 전역 속성**인데 author→build→release 어디서도 계산하지 않았다.

→ 새 concept가 기존 canonical concept의 phrase를 자기 `expected_query`/`alias`로 등록하면 단축을 **조용히 가로챈다(steal)**. 실측 사례: `"DI가 뭐야?"`가 `spring/bean-di-basics`(canonical) 대신 나중에 추가된 `software-engineering/dependency-injection-basics`로 해석됨.

**틀린 가설들(실측으로 반증)** — 검증 없이 처방했다면 헛수고·새 회귀였을 것:
- "EQ 네임스페이스 무방비 충돌 다수" → EQ 같은-tier 충돌 **0**, self-resolution 실패 **0** (네임스페이스는 깨끗함).
- "alias 충돌 1020개가 품질 저하" → 전수 dense 측정 **97.6% benign**(exact `[]`→dense가 정상 concept 회복). 24개만 진짜 틀림.
- 진짜 결함 = **fire+shadow 12개**(단축이 단일 winner로 firing + 다른 concept가 같은 norm 보유), 게이트 깨는 건 DI 1개.

## 가드 — `bin/corpus-lint` (fire+shadow 회귀 차단)

```bash
bin/corpus-lint              # 새 steal/flip 있으면 exit 1
bin/corpus-lint --json       # 기계 판독
bin/corpus-lint --rebaseline # 의도적 소유권 변경 시 baseline 재생성
```

- **기준**: `curation/ownership.fire_and_shadow` — production resolver를 재사용(divergence 0)해 fire+shadow set 계산 후 `tests/fixtures/exact_owner_baseline.json`(현재 12 grandfather)과 diff. **새 fire+shadow**나 **winner flip**이면 차단.
- **검증된 비례성**: 12/12 완전 검출 + benign 1017 alias 충돌엔 **FP 0**. (blanket 차단=FP 1017, EQ-only=FN 7, created_at 휴리스틱=방향신호 없음, self-resolution 불변식=DI 못 잡음, per-query 단조 게이트=steal에 장님 — 전부 실측 기각.)
- **index 불필요**, `load_corpus()`로 ~1s.

**배선**: `bin/corpus-build`가 encode(15-30분) **직전**에 이 게이트를 돌려 회귀 코퍼스를 못 굽게 한다(RunPod 빌드도 corpus-build 경유라 자동 적용). `curation/apply_changes`는 apply 후 `ownership_new_steals`로 authoring 시점 조기 보고.

## 프로세스 — author → lint → build → release

1. **author**: concept 추가/수정(수동 또는 `curation/`). `expected_queries`는 **canonical owner 1개** 원칙. 형제가 같은 표현을 써야 하면 `alias`로(EQ 아님).
2. **lint**: `bin/corpus-lint`. steal/flip이 잡히면 → ① 비-canonical concept에서 그 phrase를 alias로 강등(EQ 제거), 또는 ② 소유권 이동이 **의도**면 `--rebaseline`(리뷰된 근거와 함께).
3. **build**: `bin/corpus-build`(RunPod). 게이트가 또 한 번 막아준다.
4. **release**: `gh release create` + 학습자 `bin/index-fetch`.

## 권위 qrels는 grow-only

- **권위 = `tests/fixtures/real_learner_qrels_v1.json`**(held-out 실제). 배치마다 새 concept의 gold를 **append**, **숫자 맞추려 재라벨 금지**(그게 회귀를 숨긴다).
- **synth `r3_qrels_real_v1`는 report_only**(cross-check). 단일-gold 라벨이 코퍼스 성장보다 낡아(형제 rank1 아티팩트) blocking 부적합 — `learner_top1`(acceptable∪expected)·`vs-legacy`가 blocking 신호. synth는 **버전마다 재생성하지 않는다**(self-referential 편향 재유입).

## 지금 남은 실제 손상 (한 번의 RunPod 사이클로 해결)

빌드 가드는 *미래* steal을 막는다. *기존* 12 fire+shadow 중 게이트 깨는 DI는 corpus 편집으로 고친다. **이 corpus 편집은 `fix/di-canonical-owner` 브랜치에 staged됨**(corpus 변경은 dense fingerprint를 바꿔 인덱스가 corpus보다 뒤처지므로, main 일관성을 위해 브랜치에 둔다 — RunPod 빌드 후 머지):

- `software-engineering/dependency-injection-basics.json`: `expected_queries`/`aliases`에서 `"DI가 뭐야?"`/`"DI가 뭐야"` 제거.
- `spring/bean-di-basics.json`: `expected_queries`에 `"DI가 뭐야?"` 추가(canonical owner 회복).
- `bin/corpus-lint --rebaseline`(DI가 baseline에서 사라짐, 12→11) → (RunPod) `bin/corpus-build` → release → 브랜치 머지.
- 나머지 11 fire+shadow + 24 wrong-alias는 advisory(대부분 합리적 형제) — RunPod 사이클에서 triage.

> ⚠ **검증으로 발견한 corpus 편집 커플링**: `expected_queries[0]`는 drill 질문으로도 쓰이고 `len>=10` 이어야 한다(`core/drill.py:186`). 짧은 표현(`"DI가 뭐야?"` 8자)을 [0]에 두면 `build_offer_if_due`가 None을 반환해 drill이 깨진다(pytest `test_drill`이 잡음). exact-shortcut은 위치 무관이므로 짧은 표현은 배열 **끝**에 추가하고 [0]은 substantive 질문으로 둔다. corpus 편집은 항상 전체 pytest로 검증할 것.
