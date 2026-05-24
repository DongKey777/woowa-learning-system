# Paradigm-Level Research — 처음부터 다시 (Round 1)

작성: 2026-05-24
대상: 학습자 본인 (시스템 owner) — 진정한 architectural rethink

## 0. 이 문서가 답하는 질문

**"지금까지 legacy + new 두 시스템 모두 같은 paradigm (retrieval-based RAG)에 갇혀 있었다. 학습자의 진짜 미션을 다시 정의하고, paradigm 후보들을 처음부터 비교하면 어떤 설계가 나오는가?"**

이 round는 **brainstorm + 첫 단계 분석**. 측정/prototype은 Round 2.

---

## 1. 기존 시스템 (legacy + new) 의 *기능 spec*만 추출

paradigm은 reset, 기능 요구사항만 retain:

### 학습자 facing 기능 (functional spec)

1. **CS 개념 질문 응답** — "DI가 뭐야", "transactional propagation 종류" 등
2. **Mission PR 코칭** — 학습자 PR 받아서 mentor 시각 + 개선점 narrate
3. **Peer PR 비교** — 다른 크루 PR과 학습자 PR diff 비교
4. **PR retrospective** — 학습자 자기 PR 시리즈 timeline + 반복 mentor 패턴
5. **Drill scheduling** — 학습한 개념 spaced repetition (3/7/14일 등)
6. **Self-assessment** — 학습자 자기 평가 점수 입력
7. **Cognitive trigger** — 매 turn AI가 학습자에게 self-check 질문 (적절 시점)
8. **Long-term mastery tracking** — 학습자 mastered/uncertain concepts 누적
9. **Mode 분리** — learning vs system-development 활동 분리

### 비기능 spec

- 학습자가 외울 명령 = 0개 (자연어로만 interaction)
- 외부 paid API ❌ (subscription only)
- M4 16GB 안에서 작동
- 학습자 production 무중단 (이전 시스템 reuse 가능해야)
- 결과 reproducible (commit 기반)

### 핵심 insight — 기능 list만 보면

위 9개 기능 중 *RAG retrieval이 직접 필요한 것*은 **1번 (CS 개념 질문 응답)** 뿐. 
나머지 8개는 다른 mechanism으로 가능:
- 2-4: archive query + AI reasoning (no embedding)
- 5: 시간 기반 scheduling (no embedding)
- 6: pattern match (regex / simple)
- 7: AI session 즉시 판단 (no embedding)
- 8: counter / list (no embedding)
- 9: env var (no embedding)

→ **RAG는 1번 기능 1개에만 load-bearing**. 그런데 legacy + new 둘 다 RAG가 system architecture 중심.

→ **Paradigm shift candidate**: "RAG는 supporting tool, AI Coach가 architecture core"

---

## 2. Paradigm 후보 brainstorm

### Paradigm A — Embedding-RAG (현재 legacy + new)

```
질문 → embed → vector search → top-K → AI session reasons → answer
```

장점:
- semantic 검색, paraphrase 강건
- 검증된 패턴 (industry-standard)

단점 (둘 다 검증):
- Quality ceiling (legacy 92.7%, new 75.5%)
- 측정 어렵고 cycle coupling (T cycle -20pp 사례)
- Index/encoder/reranker 인프라 무거움 (~80K LOC or 1.9K LOC)
- 매 corpus 변경 시 RunPod build 필요

### Paradigm B — Hierarchical Concept Tree + AI Navigation

```
질문 → AI session이 corpus tree(category → concept) walk
       → relevant concept body 가져옴 → answer
```

구현 idea:
- `corpus/tree.json`: `{spring: {di: {...}, bean: {...}}, database: {...}}`
- AI session prompt: "다음 categories: [...]. 질문 의도에 가장 가까운 path 1-3개 선택"
- AI가 path 결정 → 해당 concept body 직접 read (file I/O)
- 답 생성

장점:
- Embedding 0개 — encoder 무료, latency O(file read)
- AI reasoning이 path 선택 (semantic 강건)
- Tree 시각화 → 학습자에게 "지금 어느 영역 공부 중" 같은 도식 제공 가능
- corpus 변경 = JSON 수정만, no RunPod

단점:
- AI session call 1-2회 추가 per query (Context window 소모)
- Tree depth 깊으면 navigation token 폭증
- 학습자가 fuzzy query 시 AI가 wrong branch 탐색 risk

측정 가능 hypothesis:
- AI navigation accuracy ≥ 92.7% (legacy baseline)일 수 있다 → 검증 가능
- 평균 latency ≤ 1s (file I/O + AI prompt round trip) → 검증 가능

### Paradigm C — Question-as-Key Cache + AI Fallback

```
질문 → 비슷한 prior 질문 찾기 (fuzzy match) 
       → 캐시 hit이면 prior answer 재사용
       → miss면 AI session이 그냥 답변 (no retrieval)
```

구현 idea:
- `state/qa-cache.jsonl`: 매 turn append `{question, answer, learner_rating}`
- 새 질문 들어오면 simple fuzzy match (Jaro-Winkler + Korean tokens) top-3
- learner_rating 높은 prior answer 1개를 AI session에게 보여줌
- AI session이 "이 prior answer 적절?" 판단 → 적절하면 그대로 + 약간 수정, 아니면 새로 답변

장점:
- 같은 질문 반복 시 instant + 일관성
- corpus 0개 (학습자 자기 질문 + AI 답이 corpus)
- 학습 cycle이 자체 corpus 생성

단점:
- 첫 질문은 AI 일반 지식만 (검증 안 됨)
- Cache pollution risk (wrong answer가 캐시되면 영구)
- 학습자 1명 cache는 작아서 hit rate 낮을 듯

측정 hypothesis:
- 1주일 사용 후 hit rate ≥ 30% → cache가 의미 있는 size 도달

### Paradigm D — Mission-Anchored, CS Corpus 폐기

```
질문 → 학습자 현재 mission repo state(working copy, PR threads, file diff)
       → AI session이 그 mission 맥락 안에서만 답변
       → 일반 CS corpus 없음
```

구현 idea:
- corpus 폐기. 학습자 mission repo의 코드 + git history + PR threads만 corpus
- 매 질문에 mission context (현재 branch, 최근 diff, open PR threads) AI session에 통째 전달
- "DI가 뭐야" 질문도 학습자 현재 mission repo의 DI 패턴(@Component, @Autowired 사용처)을 보고 답변

장점:
- 학습자 실제 코드와 직접 연결 → 추상적 정의보다 immediate
- "이 코드의 이 부분이 DI다" 식 구체적 학습
- Corpus build 0회

단점:
- 일반 CS 개념 (학습자 mission 외) 답변 불가
- Mission repo가 작거나 패턴 부재 시 답 불가
- 학습자가 *모르는* 패턴은 mission에 없으니 가르칠 수 없음

측정 hypothesis:
- 학습자 200 query 중 mission-anchored 답이 가능한 비율은 60%+ → mission scope 충분
- 학습자 self-rated 만족도가 corpus-based보다 높음 → 학습자 코드와 직결의 가치 입증

### Paradigm E — Socratic Dialogue (No Retrieval)

```
질문 → AI session이 학습자에게 *역질문* 하면서 답 유도
       → 학습자가 직접 사고하여 답 도출
       → AI는 hint + framing만 제공
```

구현 idea:
- 매 학습자 질문에 AI는 직접 답 X
- "이 코드의 이 부분은 어떻게 보이는가?" "@Autowired가 없으면 어떻게 동작할까?"
- 학습자가 답 작성 → AI가 평가 + 다음 질문

장점:
- 학습 효과 가장 높음 (active learning 입증된 pedagogy)
- Corpus 무관 (AI 일반 지식)
- 가장 적은 인프라

단점:
- 학습자가 빠른 답 원할 때 frustrating
- 매 turn 학습자 input 필요 → friction
- 측정 어려움 ("학습 효과" objective metric 없음)

측정 hypothesis:
- 1주일 후 학습자 self-rated retention ≥ retrieval-based 시스템 → active learning 우위
- 학습자가 socratic mode를 자발적 사용 비율 ≥ 50% → 채택 의지

### Paradigm F — Task Graph + Capability Progression

```
질문 → AI session이 학습자의 mastery graph 보고 답
       → "이 질문은 'spring/bean' node를 가리킴. 학습자 prerequisite ('spring/ioc')은 mastered. 
          → 직접 답변 OK"
       또는 "prerequisite 미달. 먼저 spring/ioc부터 학습 권장"
```

구현 idea:
- `state/learner/graph.json`: nodes={concept_id, mastery_score}, edges=prerequisite
- 매 질문에 AI가 graph 분석 → 답 + next step 권장
- corpus는 reference (인용용), retrieval 없음 (concept_id 직접 lookup)

장점:
- 학습자 progression 명확 (시각화 가능)
- 적절한 난이도 자동 조절
- Long-term mastery tracking과 자연스럽게 결합

단점:
- Graph 초기 구축 비용 (3199 concept × prerequisites)
- 학습자 mastery 측정 정확도가 graph 가치 결정 (drill scoring 정확해야)
- 학습자가 빠른 답 원하는 경우 (drill 안 풀고) progression 모름

측정 hypothesis:
- Graph 기반 권장이 학습자 자기 선택 path보다 retention ↑ → graph가 의미 있음
- 학습자 mastery progression rate (concepts mastered per week)가 baseline 대비 ↑

### Paradigm G — Mentor Pattern Mining (CS Theory Corpus 폐기)

```
질문 → 학습자 archive의 mentor 댓글 패턴 mining
       → "이 미션에서 mentor가 자주 지적한 것: X, Y, Z"
       → 그것이 학습 content
```

구현 idea:
- CS theory corpus 폐기. 학습자 본인 + 동료 PR archive의 mentor 댓글이 corpus
- 매 mission PR마다 mentor 패턴 추출 + AI session이 narrate
- 학습자가 일반 CS 개념 질문하면 "이 미션 cycle 안에서 mentor가 이 개념을 어떻게 지적했나" 답

장점:
- 가장 *학습자에게 specific*한 corpus
- Production-proven content (mentor가 실제 지적한 것)
- corpus build 없음 (archive ingest만)

단점:
- 신규 미션은 mentor 댓글 부족 → cold start
- 일반 CS 개념 (mentor가 안 지적한 것) 답 불가
- mentor 의견 편향 가능성

측정 hypothesis:
- 학습자 200 query 중 mentor-mined 답이 가능한 비율 ≥ 50% → corpus 충분
- 학습자 self-rated 답변 quality가 abstract CS corpus보다 ↑

### Paradigm H — Hybrid (현재 시스템 단순 진화 — 기각)

여러 paradigm 조합. 사용자 발화 "처음부터"와 충돌. 기각.

---

## 3. Paradigm 비교 matrix

| paradigm | CS 정의 답 | 미션 코칭 | Drill | Self-assess | Cognitive trigger | corpus 인프라 | latency | 학습 효과 가설 |
|---|---|---|---|---|---|---|---|---|
| A (RAG, 현재) | ✓ 75-92% | partial | code | code | code | 무거움 | 172-282ms | 검색 |
| B (tree-walk) | ? 가설 | partial | code | code | code | **경량** | ? 가설 | 검색 + 시각 |
| C (Q-cache) | partial | partial | code | code | code | 0 | instant if hit | 일관성 |
| D (mission-anchored) | partial | ✓ best | code | code | code | 0 | mission-load | code-specific |
| E (socratic) | ✗ AI만 | partial | ✓ | ✓ | ✓ | 0 | ai-bound | **active learning** |
| F (task graph) | ✓ + progression | ✓ | ✓ | ✓ | ✓ | graph build | graph-walk | progression |
| G (mentor mining) | partial | ✓ | code | code | code | 0 (archive만) | archive query | mentor-anchored |

→ **paradigm E (socratic) + F (graph) + G (mentor mining)**의 조합이 모든 기능 cover + 학습 효과 가장 강.

→ paradigm A (현재 RAG) 는 "CS 정의 답"만 강하고 나머지 약. 다른 paradigm으로 대체 가능.

---

## 4. 새로운 가설 (Paradigm-Level)

가설 1: **"RAG는 학습 시스템의 architecture core가 아니다."**
- 학습자의 진짜 미션은 "효율적 미션 학습 + 코칭 + 진척".
- CS 개념 답변은 *그 중 하나의 기능*.
- AI Coach + Task Graph + Mentor Mining이 architecture core, RAG는 supporting (또는 폐기).

가설 2: **"Embedding-free architecture가 가능하다."**
- corpus가 학습자 mission + mentor archive + concept tree로 분산되면
- 매 layer가 file I/O + AI reasoning으로 작동 → encoder 무료
- Index build + RunPod 비용 0

가설 3: **"학습 효과 metric이 retrieval accuracy metric보다 더 본질적이다."**
- legacy 92.7% top5는 *fixture를 정확히 매칭하는 능력*
- 학습자의 진짜 KPI: "1주일에 mastered concept 수", "PR review rounds 감소 추이", "self-rated 자신감"
- 새 metric으로 측정하면 RAG 정확도가 best 시스템 아닐 수 있음

---

## 5. Round 2 권장 (실제 prototype + 측정)

이 round는 brainstorm. 다음 round는 *측정 가능한 prototype*:

### 권장 prototype 후보 (1-2개 선택)

**후보 1 (가장 ambitious): Paradigm F + G + E hybrid**
- `corpus/graph.json`: 3199 concepts → prerequisite edge graph
- `state/learner/mastery.json`: 학습자 mastery score per concept
- `state/repos/<repo>/mentor_signals.json`: 학습자 archive에서 mentor 패턴 mining
- `bin/ask` 매 turn에 (a) graph walk (b) mentor pattern (c) socratic prompt 조합
- 측정: 1주일 학습자 사용 → mastered concept rate + self-rated retention
- 시간: 5-7일 (graph build + mentor mining + socratic prompt + 측정)
- 코드 estimate: ~600 LOC (graph 200 + mining 200 + ask refactor 200)

**후보 2 (가장 safe): Paradigm B (tree-walk) 검증**
- corpus를 tree로 재구성 (`{spring: {di: {...}, ...}}`)
- AI session prompt "tree에서 path 선택" → file read → 답
- 측정: 50 sample query × AI navigation accuracy vs legacy 92.7%
- 시간: 1-2일
- 코드 estimate: ~200 LOC (tree builder + ask integration)
- Embedding-RAG의 *대체 검증*만 — 다른 paradigm shift 없음

**후보 3 (가장 minimal): Paradigm A 인정 + corpus curation 가속**
- 현재 new 시스템 유지 + corpus alias enrichment 자동화 강화
- 측정: 3 cycle 후 top5 75% → 90%+ 도달 가능?
- 시간: 7-14일
- 사용자 발화 ("처음부터")와 모순 → **기각 추천**

---

## 6. 권장 결정 — 사용자 input 필요

질문 1: paradigm shift의 *깊이*?
- (i) 후보 1 — RAG paradigm 폐기, AI Coach + Graph + Mentor mining hybrid 처음부터 (5-7일)
- (ii) 후보 2 — Paradigm B 단일 검증 (1-2일)
- (iii) 다른 paradigm 후보 (D, E, G 단독)
- (iv) 후보 1 + 2 둘 다 prototype 후 비교

질문 2: 학습자의 진짜 KPI는?
- 권장: "1주일 학습자 사용 후 self-rated 만족도 + mastered concept rate"
- 측정 가능 + 학습 효과와 직결
- 단 1주일 실제 사용 = 시간 commitment

질문 3: 새 repo 또는 기존 repo branch?
- `woowa-learning-system` 폐기 + 새 repo `woowa-learning-coach`?
- 또는 `woowa-learning-system` 안에 `core_v2/` 폴더로 parallel?
- 학습자 production은 legacy hub 유지

이 3 질문에 답 주면 Round 2 (실제 prototype) 진행. 자율로 결정 가능하면 가장 promising 후보 1로 진행.

---

## 부록 A: 의도된 메타-반성

이 문서 자체가 paradigm-level rethink. 단 한 가지 한계 인정:

- 이 문서도 "기능 spec 추출 + paradigm brainstorm + 가설"이라는 *시스템 설계 paradigm* 안에서 작동
- 진짜 fundamental rethink는 "학습이라는 활동 자체"부터 질문해야 (학습자가 코드 작성 외의 활동이 진짜 필요한가? Pair programming with AI가 더 본질 아닌가? 등)
- 단 그 수준은 이 turn에서 다 못 다룸 → Round 3+에서 deepening
