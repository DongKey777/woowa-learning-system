# 학습자 관점 기능 spec — 내가 이해하는 이 시스템

작성: 2026-05-24
대상: 사용자(=학습자) review용. 동의/수정/삭제/추가 의견 받음.

---

## 0. 사용자(학습자)가 누구인가 — 내 이해

- 우아한테크코스 백엔드 과정 학습자
- 매일 활동: Java/Spring 미션 코딩 → 자기 fork로 PR 올림 → mentor + 다른 크루 리뷰 받음 → 수정 cycle 반복
- 1 미션 = 보통 step 1-4 단계, 각 단계마다 PR 1개 + reviewer 1-3명 + thread N개
- 학습 도메인: Spring / Database / OS / Network / 자료구조 / 알고리즘 / 디자인 패턴 / OOP / 테스트 등
- 도전: (a) 새 개념 빠르게 익히기, (b) mentor 반복 지적을 자기 패턴으로 흡수, (c) 다른 크루 코드와 자기 코드 비교 통한 시야 확장, (d) 단계 진척과 mastery 가속

---

## 1. 학습자 daily 활동 흐름 — 시스템이 끼어드는 시점

```
[월요일] 새 step 시작
  ↓ 미션 README 읽음 + 요구사항 파악
  ↓ "이번 step에서 뭘 학습해야 하지?" — 시스템 도움 시점 ①
  ↓ 코드 작성 시작
  ↓ "이 부분 어떻게 짜야 mentor 지적 안 받을까?" — 시점 ②
  ↓ "Spring transactional 이게 맞나?" — 막힌 개념 — 시점 ③
  ↓ commit + PR 올림
[화요일] mentor 리뷰 받음
  ↓ "이 지적이 왜?" — 시점 ④
  ↓ "다른 크루는 이 부분 어떻게 했나?" — 시점 ⑤
  ↓ thread 답글 작성 + 코드 수정
[수-금] cycle 반복
  ↓ "내가 이 미션 cycle 동안 진짜로 마스터한 게 뭐지?" — 시점 ⑥
  ↓ "지난 step에서 똑같은 지적 또 받았는데" — 시점 ⑦
[주말] 회고
  ↓ "이번 주 학습 진도가 어땠지?" — 시점 ⑧
  ↓ "다음 step에 뭘 미리 공부해야?" — 시점 ⑨
```

이 9개 시점이 시스템이 *실제로* 학습자에게 가치 주는 순간.

---

## 2. 9 시점 × 기능 (학습자 입장에서 reframe)

| # | 시점 | 학습자 pain | 기존 시스템 처리 | 진짜 필요한 것 |
|---|---|---|---|---|
| ① | step 시작 시 "뭘 학습?" | 미션 README가 모호, 학습 목표 안 잡힘 | legacy: 없음 / new: 없음 | step별 학습 목표 자동 추출 + checklist |
| ② | 코딩 중 "mentor 지적 회피" | 같은 종류 지적 반복 — pattern 모름 | legacy: PR retrospective (수동 trigger), new: 동일 | mentor 패턴이 코딩 중 자동 surface |
| ③ | 막힌 개념 즉시 답 | 학습 끊기지 않게 빠른 답 + 정확 | legacy: rag-ask (92.7% top5, 282ms) / new: ask (75.5%, 282ms) | 빠르고 정확한 개념 답 |
| ④ | mentor 지적 해석 | 지적이 단순 stylistic? 깊은 원리? 모름 | legacy: coach-run / new: ask coaching mode | mentor 의도 + 원리 + 행동 권장 |
| ⑤ | 동료 PR 비교 | "다른 크루는 어떻게?" — manual GitHub 탐색 부담 | legacy: peer_pr_precise + nickname-map / new: peer_pr | 닉네임 던지면 즉시 비교 + 차이 narrate |
| ⑥ | 이번 cycle mastery 점검 | "정말 익혔나" 모호 — self-bias 위험 | legacy: drill + self-assess / new: 둘 다 stub | objective 점검 (drill score) + subjective (self) 결합 |
| ⑦ | 반복 지적 자각 | 같은 실수 3번째 — patterns 보여줘야 | legacy: pr_retrospective / new: collect_retro | "3번째야" 알림 + 원인 분석 |
| ⑧ | 주간 회고 | 학습 진척 시각화 부재 | legacy: profile dominant_learning_points / new: 동일 | mastery progression chart 같은 시각 |
| ⑨ | 다음 step 미리 공부 | 다음 step 학습 목표 모름 | 둘 다 없음 | 다음 step 미리 학습 path 권장 |

---

## 3. 기능 list (구현 mechanism 무관, 학습자 가치 기준)

내가 이해한 시스템의 **11개 기능** (F1-F9 + F10/F11 사용자 명시 추가):

### F1. 개념 즉시 답변 (시점 ③)
- input: "DI가 뭐야" 같은 자연어 질문
- output: 정확한 답 + 인용 (출처)
- 학습자 가치: 막힘 해소, 학습 흐름 유지
- 측정: 정확도 + latency + 학습자 self-rated 도움됨

### F2. 미션 코칭 (시점 ②, ④)
- input: 학습자 현재 PR + 질문
- output: mentor 시각 narrate + 개선 권장
- 학습자 가치: mentor 의도 학습, 다음 PR cycle 개선
- 측정: PR review rounds 감소 추이, 학습자 만족도

### F3. 동료 PR 비교 (시점 ⑤)
- input: 닉네임 또는 PR 번호
- output: diff + 접근 방식 차이 narrate
- 학습자 가치: 시야 확장, 패턴 학습
- 측정: 학습자가 다른 크루 코드 채택한 patterns 수

### F4. 반복 패턴 인식 (시점 ②, ⑦)
- input: (background) 학습자 PR archive
- output: "이 mentor 지적 N번째" 같은 알림 + 패턴 분석
- 학습자 가치: 메타인지, 같은 실수 회피
- 측정: 반복 지적 발생 횟수 (시간 따라 감소?)

### F5. 학습 진척 추적 (시점 ⑥, ⑧)
- input: (background) 학습자 history + drill 결과
- output: mastery progression + weak area
- 학습자 가치: 자기 평가 정확도, 학습 방향 보정
- 측정: mastered concept count 추이

### F6. Drill (개념 review, 시점 ⑥)
- input: 학습자 답변
- output: 점수 + 약점 + next 시점
- 학습자 가치: spaced repetition으로 long-term retention
- 측정: review accuracy, retention rate

### F7. Self-assessment (시점 ⑥)
- input: 학습자 자기 점수 ("8점")
- output: calibration data (mastery로 격상 X)
- 학습자 가치: 자기 인식 정밀화
- 측정: self-rated vs drill score gap

### F8. 다음 step 안내 (시점 ①, ⑨)
- input: 학습자 현재 미션 + 다음 step
- output: 학습 목표 + prerequisite check
- 학습자 가치: 미리 준비, 효율
- 측정: pre-study 후 새 step 어려움 감소

### F9. 학습 vs 시스템 개발 mode 분리 (운영)
- 학습 활동만 personalization signal
- 시스템 개발 (이 codebase 작업)은 signal 오염 X
- 학습자 가치: 추천 정확도 보존
- 측정: 자동 — mode tag 적용률

### F10. Mission ↔ CS bidirectional integration (사용자 명시 추가, 가장 핵심)

이 시스템의 **architecture core 그 자체**. 다른 9개 기능의 meta-feature.

- **Forward (mission → CS)**:
  - 학습자 PR/코드 분석 → 사용된 패턴 추출 (`@Transactional`, `JdbcTemplate`, `Stream API`, sorting algorithm 등)
  - 각 패턴의 *CS prerequisite* identify
    - 예: `@Transactional` 사용 → AOP proxy, transaction propagation, isolation level, lock-wait timeout 등이 prerequisite
  - 학습자 mastery profile과 cross-reference → gap detect
  - "이 PR에서 너는 X를 썼지만 prerequisite Y는 아직 mastered 아님 → next learning point"
  - 코칭에 그 CS 인용 + 실제 학습자 코드 위치 가리킴

- **Backward (CS → mission)**:
  - 학습자가 "DI가 뭐야" 같은 CS 질문 → 그 개념이 *학습자 현재 미션 어디에 사용되거나 사용될 수 있는지* mapping
  - "너의 roomescape-admin step 2에서 ReservationController가 ReservationService를 직접 new 하고 있어. 이게 DI 안 쓰는 case. 만약 @Autowired로 받으면..."
  - 추상 정의가 아니라 *학습자 자기 코드*로 설명

- **학습자 가치**: 학습이 mission 작업과 항상 연결됨 → 동기 + retention 강화. CS는 mission 위한 도구, mission은 CS 응용 사례. 둘이 분리되면 두 시스템이고, 연결되면 진짜 hub.

- **측정**:
  - 학습자가 next 권장 학습점을 실제 mastered하는 비율
  - mission 코드 + mentor 지적이 학습자 mastery profile에 자동 반영되는 정확도
  - 학습자 self-rated "이 답이 내 미션과 연결됨" 비율

- **legacy/new 모두 partial**: legacy `coach-run.cs_augmentation`은 단방향(mission 코칭 시 CS doc 인용), new `ask coaching mode`의 `rag_augment` 동일. **양방향 + mastery gap 자동 감지** 모두 없음.

### F11. Cross-crew + cross-reviewer 심층 review 비교 (사용자 명시 추가)

F3 (peer PR diff)을 *훨씬 깊게* 확장. F3는 path-level diff 비교에 그침. F11은 **code-level anchor + review opinion graph**.

- **Input**: 학습자 본인이 받은 review thread 1개 (예: "Controller가 Service를 직접 new 한다 → DI 권장" at `src/.../ReservationController.java:42`)
- **Output**:
  1. 비슷한 코드 패턴 가진 *다른 크루 PR* multiple 찾음 (anchor 매칭)
  2. 각 PR의 동일 부분에 달린 review threads (reviewer 다양)
  3. **Reviewer별 의견 비교**: 동일 패턴에 reviewer A는 "DI 권장" / reviewer B는 "context에 따라 OK" / reviewer C는 침묵
  4. **크루별 대응 비교**: 크루 X는 "그대로 유지" 답글 + reviewer 동의 → 최종 코드 동일 / 크루 Y는 "전면 수정" 답글 → 최종 코드 DI 적용
  5. AI session이 narrate: "너의 이 지적은 reviewer A가 자주 하는 의견. reviewer B는 다른 시각 — 크루 Y는 받아들였고, 크루 X는 reviewer B의 시각으로 유지. 두 선택의 trade-off는..."
- **학습자 가치**:
  - 한 reviewer 의견에 종속되지 않음 → 다양한 시각 학습
  - 다른 크루의 해석/대응 → 더 깊은 학습
  - 미션 관련 *모든 review knowledge* 활용 (archive에 누적된 지혜)
- **측정**:
  - 학습자가 본 review thread당 발견되는 cross-crew/reviewer anchor 수
  - 학습자 self-rated "여러 시각 도움됨" 비율
  - 단순 F3 대비 학습자 채택 비율
- **legacy/new 모두 없음**: F3 (peer PR precise)는 path overlap + thread sample만, **reviewer 의견 graph + 크루 대응 비교**는 두 시스템 모두 미구현.

기술 sketch:
- 학습자 thread anchor 추출 (path, line range, code snippet 5-10줄)
- archive 전체에서 anchor와 유사한 code pattern 찾기 (path 같은 + AST 또는 token level fuzzy match)
- match된 PR의 anchor 부근 threads 모음
- reviewer별 grouping + opinion text
- 크루 reply chain + 최종 commit이 thread 의견 따랐는지 자동 판단
- AI session prompt에 위 모든 input 전달 + narrate

산출 artifact:
- `state/learner/review_anchors.json`: 학습자 본인 review thread × anchor code
- `state/repos/<repo>/cross_crew_review_graph.json`: code pattern → reviewer 의견 + 크루 대응 graph

---

## 4. 비기능 spec — 학습자 환경

- **단일 entry**: 학습자는 자연어로만 interaction. 외울 명령 0개
- **외부 paid API ❌**: AI session = 학습자 본인 Claude Pro 구독
- **M4 16GB 안에서**: BGE-M3 한계 입증, 더 큰 모델 ❌
- **production 무중단**: 학습자가 매일 사용 중 — 새 시스템 fail 시 즉시 rollback
- **reproducibility**: 모든 변경 commit. 학습자가 git log 보고 시스템 진화 추적 가능

---

## 5. 기능별 *현재* status (legacy + new 결합)

| 기능 | Legacy 구현 | New 구현 | 학습자 실제 만족? |
|---|---|---|---|
| F1 개념 답변 | ✓ 92.7% top5 / 282ms | ✓ 75.5% top5 / 282ms | partial (회귀 -17pp) |
| F2 미션 코칭 | ✓ coach-run.json | ✓ ask coaching mode | 미측정 |
| F3 peer PR 비교 | ✓ peer_pr_precise | ✓ peer_pr module | 미측정 |
| F4 반복 패턴 인식 | ✓ pr_retrospective | ✓ retro mode | 미측정 |
| F5 학습 진척 추적 | partial (profile) | partial (profile) | **미실현** |
| F6 Drill | ✓ drill.py | stub만 | **미실현** in new |
| F7 Self-assessment | ✓ learn-self-assess | stub만 | **미실현** in new |
| F8 다음 step 안내 | **없음** | **없음** | **미구현** |
| F9 mode 분리 | ✓ env var | ✓ env var | ✓ |
| **F10 Mission↔CS bidirectional** | **partial** (단방향 cs_augmentation only) | **partial** (단방향 rag_augment only) | **architecture core 미구현** |
| **F11 Cross-crew/reviewer 심층** | **없음** (F3로 path overlap만) | **없음** (F3로 path overlap만) | **완전 미구현** |

**Gap 정리** (우선순위 순):
- **F10**: 양방향 + mastery gap 자동 감지 부재 — backbone 미구현
- **F11**: code-level anchor + reviewer 의견 graph 부재 — 학습 깊이 손실
- F1: new 시스템 quality 회귀 (확인됨, top5 -17pp)
- F5: 두 시스템 모두 profile은 있으나 *학습자가 진척을 시각으로 못 봄*
- F8: 두 시스템 모두 미구현 — 학습 가속의 큰 빈 자리
- F6, F7: new 시스템 stub만 — 실제 작동 안 함

---

## 6. 사용자 확인 요청 — 이 spec이 맞나?

다음 중 어느 것이 사용자 의도와 다른가?

### 6.1. 기능 포함 여부

- [ ] F1 (개념 답변) — 동의?
- [ ] F2 (미션 코칭) — 동의?
- [ ] F3 (peer PR 비교) — F11에 흡수? light version 유지?
- [ ] F4 (반복 패턴 인식) — 동의?
- [ ] F5 (학습 진척 추적) — 동의?
- [ ] F6 (Drill) — 정말 필요? 안 쓰는 기능일 수도
- [ ] F7 (Self-assessment) — 정말 필요?
- [ ] F8 (다음 step 안내) — 이게 진짜 가치 있나?
- [ ] F9 (mode 분리) — 운영 기능, 동의?
- [ ] **F10 (Mission↔CS bidirectional)** — backbone 동의?
- [ ] **F11 (Cross-crew/reviewer 심층)** — depth layer 동의?

### 6.2. 우선순위 — 학습자에게 가장 가치 있는 순서?

**F10 + F11 추가 후 새 우선순위 (사용자 발화 반영)**:

1. **F10** Mission ↔ CS bidirectional — 이 시스템의 backbone
2. **F11** Cross-crew + cross-reviewer 심층 비교 — mission 학습 깊이 layer
3. F2 (미션 코칭) — F10+F11 위에서 best
4. F1 (개념 답) — F10이 mission-aware로 만듦
5. F8 (다음 step 안내) — F10 직접 활용
6. F4 (반복 패턴 인식) — F11이 본인 + cross-crew 패턴 결합
7. F5 (학습 진척 추적) — F10 자동 갱신
8. F3 (peer PR 단순 비교) — F11에 흡수 또는 light version 유지
9. F6 (Drill) — F10이 권장한 개념 review
10. F7 (Self-assessment)
11. F9 (mode 분리, 운영)

수정/재배열 의견?

### 6.3. 빠진 기능 있나?

내가 놓친 시점이나 활동:
- ...?
- ...?

### 6.4. 잘못 이해한 부분?

학습자 daily 흐름 §1에서 틀린 가정 있나?

---

## 7. 이후 단계

기능 spec 확정되면:
1. 각 기능의 *진짜 KPI* 도출 (학습자 self-rated + objective)
2. paradigm 후보 × 각 기능 충족 가능성 mapping
3. 가장 ambitious paradigm 선택 (사용자 발화 "한계 뛰어넘기")
4. Round 2 prototype + 측정

---

## 8. 자율 판단 — F10 추가 후

**F10 (Mission ↔ CS bidirectional)이 architecture core**. 모든 paradigm 후보를 F10 기준으로 재평가:

| paradigm | F10 forward 가능 | F10 backward 가능 | F10 mastery gap detect |
|---|---|---|---|
| A (RAG, 현재) | weak (retrieval만) | weak | weak (cross-axis 없음) |
| B (tree-walk) | weak | weak | weak |
| C (Q-cache) | 없음 | 없음 | 없음 |
| D (mission-anchored) | strong | weak (CS 일반 답 약함) | medium |
| E (socratic) | medium | medium | medium |
| **F (task graph)** | **strong** | **strong** | **strong** |
| G (mentor mining) | medium | weak | weak |

→ **F10 backbone**: Paradigm **F + D** hybrid (task graph + mission-anchored)
→ **F11 depth layer**: Paradigm **G + D** hybrid (mentor mining + mission-anchored)
→ 최종: **F + D + G의 3 paradigm hybrid**가 F10+F11 모두 cover

설계 sketch — 5 artifacts cross-reference:
- **`corpus/concept_graph.json`**: 3199 concepts × prerequisite edges (F10)
- **`state/repos/<repo>/mission_patterns.json`**: 학습자 mission 코드 사용 패턴 → concept_id (F10 forward)
- **`state/learner/mastery_graph.json`**: 학습자 mastery × concept graph 위치 (F10 gap)
- **`state/learner/review_anchors.json`**: 학습자 본인 review thread × anchor code (F11)
- **`state/repos/<repo>/cross_crew_review_graph.json`**: code pattern → reviewer 의견 + 크루 대응 graph (F11)

AI session이 매 turn 5 cross-reference:
1. 현재 mission state (working PR, recent code) → mission_patterns 자동 추출
2. mastery_graph에서 현재 위치 + gap
3. 답/코칭 시 mission 코드 위치 인용 + CS prerequisite 추적
4. 학습자 review thread → anchor code → cross-crew/reviewer 의견 비교
5. mentor 의견 + 크루 대응 graph로 narrate

→ **이게 진짜 "유기적 연결". F1/F2/F4/F8이 backbone, F11이 depth layer 위에서 자연스럽게 작동.**

---

## 9. Autonomous analysis — 사용자 위임 결정 (측정 + 가설 검증)

이전 §6에서 사용자 답 요청했던 unknowns를 직접 가설+측정+판단.

### A. 빠진 시점/기능 — 측정 결과

**A1. 다중 mission 동시 진행 (cross-mission transfer)** — 측정:
- 학습자 4 active repos (demo + roomescape-admin/auth/member)
- DongKey777 본인 PR: admin 0, auth 1, member 1 = 총 **2개 active mission**
- 본인 PR보다 peer PR 압도적 (231+56+375=662 PRs)

→ **결정**: 다중 mission *동시* 진행은 적음 (한 cycle에 1-2 repo). **F12 (cross-mission learning transfer)는 별도 기능 불필요** — F10 prerequisite graph가 concept-level이라 mission-agnostic하게 cover.

**A2. 사전 학습 (첫 미션 시작 전)** → 시점 ①과 사실상 동일. **F8에 통합** (별도 기능 X).

**A3. Drill 자동 surface** → F6 안에 trigger mechanism으로 포함. F6 input: "학습자 답변 OR 시간 기반 review_due trigger".

### B. 비기능 spec 결정

**B1. 목표 latency**:
- Warm query (F1/F2): ≤2s (학습 흐름 유지)
- Cold first per session: ≤30s 허용 (BGE-M3 load)
- F11 deep analysis: ≤10s (학습자 "심층 분석 중" 기다림 OK)
- F10 backward (CS query + mission lookup): ≤3s

**B2. AI session token budget per turn**:
- 평균 prompt ≤5K tokens 목표
- F11 trigger 시 ≤15K tokens 허용 (1 turn 한정)
- 일 100 turns × 평균 7K = 700K tokens/day → Claude Pro 5h window 안에 가능
- 핵심 구현 원칙: **artifact lazy load** — F10 forward 시 mission_patterns만, F11 trigger 시 review_anchors만. 매 turn 5 artifacts 통째 전달 ❌

**B3. Data privacy**:
- `state/` 항상 gitignored, 외부 API ❌
- history.jsonl + mission archive는 local 보관, cross-machine 동기화는 옵션 (학습자 명시 trigger 시만)

### C. Architecture-critical unknowns — 측정 결과

**C1. F10 prerequisite graph quality** — 측정:
- 3199/3199 = **100%** has `relations.prerequisites`
- 평균 1.80 prereqs/concept
- Unresolved edges 9/5758 = **0.2%** (모두 `*-index`/`navigator` self-reference, trivially fixable)

→ **결정**: F10 backbone **graph quality 충분**. 0.2% unresolved는 Round 2에서 정리.

**C2. F11 anchor matching precision** — 측정:
- Legacy archive 데이터 풍부: admin 6,723 / auth 618 / member 18,842 review_comments
- distinct reviewers admin 152 / auth 9 / member 288
- 본인 own_review_threads: auth 20개 + member 11개 = 31개 anchor 후보
- 31개 anchor × peer archive cross-search → 풍부한 corpus

→ **결정**: F11 data potential 검증됨. 단 anchor matching false positive 비율은 **Round 2 prototype에서 측정 필요** (현재는 가설).

**C3. F8 "다음 step 안내" 의미** — 측정 + 결정:
- 학습자 step은 program-driven (woowa 코스, 선택 X) 확인
- F8 = (a) 현재 step 진행 중 *다음 단계 hint* + (b) step 종료 시 *다음 step prerequisite warning*
- *step 외 학습 path* 권장 ❌ (학습자 선택 자유 보장)

### D. Scope 결정

**D1. Round 2 prototype 범위**:
- 11 기능 전체 = 80K LOC급 → 무리
- **Round 2 = F10 + F11 두 기능 PoC + 기존 F1-F9 코드 reuse**
- 추가 LOC 추정: F10 backbone (~500) + F11 depth (~400) = ~900 LOC
- 합산: 1933 (현재 new) + 900 = ~**2833 LOC** → plan 2350 초과되지만 F10+F11 critical하므로 plan budget 업데이트

**D2. Reset 시점**:
- 사용자 force-push 동의했지만 prototype 단계는 **별도 branch `paradigm-v2`**에서 진행
- F10 + F11 PoC + 측정 → 검증 시 main에 merge → 검증 후 force-push reset
- 학습자 production은 legacy hub 무중단

**D3. 작업 순서 (가장 cheap → 비쌈)**:
1. **F10 forward** (mission → CS): `mission_patterns.json` builder (Java 어노테이션 regex extract) + `concept_graph.json` cross-ref. ~300 LOC
2. **F10 backward** (CS → mission): query → patterns reverse mapping + AI prompt에 학습자 코드 위치 인용. ~200 LOC
3. **F10 mastery gap detect**: prerequisite walk + 학습자 mastery 대비. ~100 LOC
4. **F11 anchor extraction**: 학습자 review thread → `review_anchors.json`. ~150 LOC
5. **F11 cross-crew matching**: archive 전체 fuzzy match → `cross_crew_review_graph.json`. ~200 LOC
6. **F11 AI session prompt 통합**: 5 artifacts cross-reference. ~50 LOC

각 단계마다 측정 → 다음 단계 결정. **F10 forward 단독으로도 학습자에게 큰 가치** (mastery gap detect가 작동).

---

## 10. Final spec — 자율 분석 후 확정

11 기능 = backbone (F10) + depth (F11) + supporting (F1-F9). 우선순위 + scope:

- **Tier 0 (backbone)**: F10 — 필수. 다른 모든 기능의 meta-feature
- **Tier 1 (depth)**: F11 — F10 위에서 학습 깊이 가속
- **Tier 2 (학습자 daily 가치)**: F2 (코칭), F1 (개념답), F4 (반복패턴)
- **Tier 3 (학습 progression)**: F5 (진척), F8 (다음 step)
- **Tier 4 (option)**: F3 (peer PR light), F6 (drill), F7 (self-assess)
- **Tier 5 (운영)**: F9 (mode 분리)

**Round 2 immediate scope**: F10 + F11 + 기존 F1-F4 reuse. F5/F6/F7/F8/F9는 후속 round.

**Branch**: `paradigm-v2` on `woowa-learning-system`. 검증 시 main reset (force-push 사용자 동의).

이 spec 확정. 다음 단계: Round 2 prototype 시작.
