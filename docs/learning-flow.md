# Learning Flow — 학습자 일상 사용 시나리오

학습자가 매일 어떻게 woowa-learning-system을 쓰는지 시나리오별 자세히 보여준다. 학습자는 한국어로 의도만, AI 세션이 모든 명령을 자동 실행.

---

## 시나리오 1 — 개념 질문 (F1 cs_qa)

**학습자**: "Bean DI가 뭐야"

**AI 세션 자동**:
```bash
python3 bin/ask "Bean DI가 뭐야"
```

**daemon 응답** (markdown):
```
mode: cs_qa
budget: 4500
personas: MENTOR, SOCRATIC
artifacts loaded:
  - concept_graph (3339 nodes)
  - mastery_graph (events_total=0, mastered=0, proficient=0, ...; 실제 사용 후 누적)
  - rag_hits (top 5):
      1. spring/bean-di-basics — Bean DI 기초 (score 0.94)
      2. spring/ioc-di-basics — IoC와 DI (score 0.91)
      3. software-engineering/dependency-injection-basics — DI 일반 (score 0.88)
      ...
recent history: 마지막 20 turn
```

**AI 세션 → 학습자** (한국어):
```
[Mode: cs_qa]

Bean DI는 Spring 컨테이너가 객체를 관리하고 협력 객체를 외부에서 주입해주는 패턴이야.
[MENTOR]: 객체가 의존성을 직접 new로 만들지 않고 외부에서 받음으로써 결합도를 낮추고…
[SOCRATIC]: 만약 ReservationController가 직접 new ReservationService()를 했다면 어떤 문제가 생길까?

참고:
- spring/bean-di-basics
- spring/ioc-di-basics
- software-engineering/dependency-injection-basics
```

**자동 후속**:
- daemon이 `history.jsonl`에 `rag_ask` 이벤트 append (mode=cs_qa, top_concept_ids=[…], latency_ms=N) + pending capture 생성. 다음 turn의 personalization에 반영.
- Claude/Codex/Gemini hook이 답변 직후 `bin/capture-response`를 호출해 최종 답변 본문 전체를 저장. hook이 없으면 `bin/learn-response-quality --response-path` / `--response-file -` fallback을 사용한다.
- 수집 실패는 답변을 막지 않고 repair queue에 남긴다. 필요 시 한 줄만 안내: *"학습 기록 저장은 나중에 자동 보정할게."*
- full body는 `state/learner/response-bodies/sha256/`에 redacted content hash 기준으로 dedupe 저장되고, `response-quality.jsonl` row가 `source_event_id`로 `rag_ask`와 join된다.

---

## 시나리오 2 — 코드 리뷰 / 리팩토링 (F2 + F10 coaching)

**학습자**: "내 ReservationController 어떻게 리팩토링하면 좋아"

**AI 세션 자동**:
```bash
python3 bin/ask "내 ReservationController 어떻게 리팩토링하면 좋아" \
    --repo spring-roomescape-member
```

**daemon 응답** (mode `coaching`, budget 5500, 3 personas):
- `mission_patterns` (학습자 own PR 121 patterns 추출) ← **F10 forward**
- `concept_graph` + `mastery_graph` + `rag_hits`

prompt 일부:
```
### mission_patterns (repo=spring-roomescape-member, patterns=121)
  src/main/java/roomescape/admin/AdminReservationTimeController.java:17
    @RestController → spring/mvc-controller-basics
  src/main/java/roomescape/reservation/ReservationService.java:34
    @Transactional → spring/transactional-basics
  ...
```

**AI 세션 → 학습자**:
```
[Mode: coaching]

리팩토링 포인트 3가지 봤어:

[MENTOR]: ReservationController가 도메인 로직을 직접 갖고 있어 보이는데,
  Service 계층으로 분리하면 SRP가 깔끔해져. 너의 PR에서 봤듯이 이미
  @Transactional은 Service에 잘 붙였으니 다음 단계는...

[REVIEWER]: 다른 크루(`donghoony`) 비슷한 코드 리뷰에서 멘토(`hyeonic`)가
  "도메인 검증을 컨트롤러에서 하면 테스트 어려워진다"고 지적했어. 동일 패턴.

[SOCRATIC]: ReservationRequest를 Reservation 엔티티로 변환하는 책임은
  Controller / Service / Mapper 중 어디가 맞을까?

참고:
- spring/mvc-controller-basics
- software-engineering/oop-design-basics
- spring/transactional-basics
```

**자동 후속**: 학습자가 코드 수정 시 AI가 `bin/learn-event --event-type code_attempt --concept-ids spring/mvc-controller-basics --silent` 자동 호출. mastery_graph에 evidence 누적.

---

## 시나리오 3 — Cross-crew 비교 (F11)

**학습자**: "다른 크루는 이 reservation 코드를 어떻게 작성했어"

**AI 세션 자동**:
```bash
python3 bin/ask "다른 크루는 이 reservation 코드를 어떻게 작성했어" \
    --repo spring-roomescape-member
```

**daemon 응답** (mode `f11_anchor`, budget 12000, REVIEWER + MENTOR):
- `review_anchors` (repo sync 후 생성되는 optional thread anchors)
- `cross_crew_review_graph` (사전 빌드 parquet, top 3 by embed_cosine)
- `mission_patterns`

prompt 일부:
```
### cross_crew_review_graph (total=95, top-3 by embed_cosine)
  - PR#390 Theme.java (mentor=hyeonic) ↔ PR#179 crew=minjae8563
    reviewer=coderabbitai[bot] jaccard=0.929 cos=0.983
    ↳ "이 Theme 클래스는 불변 객체로 잘 설계됐지만 도메인 객체로서 …"
  - PR#390 ReservationControllerTest.java (mentor=hyeonic) ↔ PR#357 crew=Jihyun3478
    reviewer=verus-j jaccard=0.875 cos=0.958
    ↳ "각 테스트 케이스의 코드가 너무 긴 것 같네요. 헬퍼 메서드로 …"
  ...
```

**AI 세션 → 학습자**:
```
[Mode: f11_anchor]

다른 크루 reservation 코드 분석한 결과 2가지 흥미로운 패턴 봤어:

[REVIEWER]: minjae8563의 Theme.java에는 너랑 거의 같은 final 키워드 + equals/hashCode
  패턴이 있었는데, 거기서 coderabbit bot이 "name validation을 생성자에서 해야 한다"고
  추가 지적했어. 너도 같은 검증을 생각해볼 가치 있어 보여.

[MENTOR]: Jihyun3478의 ReservationControllerTest는 헬퍼 메서드로 가독성 정리한
  버전이야. 너의 ReservationControllerTest와 비교해보면 비슷한 길이의 케이스에서
  …

참고:
- spring/test-fixture-given-when-then-primer
- software-engineering/oop-design-basics
```

---

## 시나리오 4 — 도구/명령 질문 (F3 tool_only)

**학습자**: "git rebase -i 어떻게 써"

**AI 세션 자동**:
```bash
python3 bin/ask "git rebase -i 어떻게 써"
```

**daemon 응답** (mode `tool_only`, budget 1500, no personas, no RAG):
- 빠른 fast-path. RAG/coach prompt 없이 학습자에게 직접 도구 가이드.

**AI 세션 → 학습자**:
```
[Mode: tool_only]

git rebase -i HEAD~3 하면 최근 3개 commit 편집창 떠. pick / squash / reword / drop
… (도구 사용 가이드, RAG 인용 없음)
```

---

## 시나리오 5 — PR 회고 (F4 retro)

**학습자**: "내 PR 흐름 보여줘"

**AI 세션 자동**:
```bash
python3 bin/ask "내 PR 흐름 보여줘" --repo spring-roomescape-member
```

**daemon 응답** (mode `retro`, budget 5000, MENTOR + REVIEWER):
- `mission_patterns` (학습자 누적 코드 패턴)
- `mastery_graph` (학습 진척)
- recent_history (마지막 20 turn)

**AI 세션 → 학습자**:
```
[Mode: retro]

너의 spring-roomescape-member 작업 흐름 정리:

학습 데이터가 충분히 쌓인 뒤의 예시 진척 (Bloom 레벨):
- mastered: 5 (bean-di, ioc-di, mvc-controller, configurationproperties, transactional-self-invocation)
- proficient: 2 (jdbc-jpa-mybatis, transaction-isolation)
- attempted: 5 (java-optional, java-exception-handling, stream, try-with-resources, oop)

반복 멘토 지적 (mentor=hyeonic, 2 thread):
1. ReservationControllerTest 가독성 (PR#357와 동일 패턴)
2. Theme 도메인 검증 (3 PR에서 반복)

[MENTOR]: 너의 mastered 5개 모두 Spring 핵심 — Bean/IoC/MVC가 안정됐고
  이제 도메인 모델링이 다음 단계로 보여…
[REVIEWER]: 멘토가 일관되게 "validation을 도메인에"라고 지적해온 패턴이 있어…

참고:
- spring/transactional-self-invocation-call-path-router
- spring/mvc-controller-basics
```

---

## 시나리오 6 — Drill (F6, 시스템이 issue → 학습자 답변)

**학습자**: "확인 질문 하나 줘" 또는 시스템이 자동으로 학습자 약점 감지 시 자동 issue.

**Daemon (uncertain_concepts 있을 때)** drill_offer artifact 생성:
```
### drill_offer (concept=spring/bean-di-basics)
  question: Spring Bean이 뭐야?
  expected_terms: ['Bean', 'Spring', 'IoC', '컨테이너', '의존성', ...]
```

**AI 세션 → 학습자**:
```
[Mode: drill]

[Drill] Spring Bean이 뭐야?
(쉬는 시간 갖고 답해줘 — 키워드: Spring/Bean/컨테이너/DI 같은 핵심 개념 포함하면 좋아)
```

**학습자**: 답변 작성 (예: *"Bean은 Spring 컨테이너가 관리하는 객체이고 DI로 협력 객체 받아…"*)

**AI 세션 자동**:
- `core/drill.score_pending_answer()` 호출 (또는 `bin/learn-event --event-type drill_answer --score 0.85 --silent`)
- 4 dimension 채점 (accuracy/depth/practicality/completeness)
- 결과를 학습자에게 한국어로 풀어서 보고:

```
좋은 답변이야! 채점 결과:
- 정확도 8/10 (Bean/Spring/컨테이너/DI 다 언급, '의존성 주입' 정확)
- 깊이 7/10 (객체 관리 + DI 흐름 잘 설명)
- 실용성 5/10 (코드 예시나 @Service 같은 어노테이션 추가하면 더 좋아)
- 완결성 7/10 ('왜/언제' 맥락 잘 들어감)
→ 종합 0.74 (good band) → 1주 후 spaced review 예정.

다음 약점: 어노테이션 사용 예제. @Service / @Component 차이 다시 생각해볼래?
```

자동 후속: `drill_score` evidence가 mastery_graph에 weight 0.7×0.74=0.518 누적. proficient → mastered promote 가능 (다른 source pair 있으면).

---

## 시나리오 7 — Self-assessment (F7, pending trigger 있을 때만)

**시스템 issue된 self-assessment trigger**: *"DI 개념 자신감 1-10점으로 매겨줘"*

**학습자**: "8점"

**AI 세션 자동**:
- `core/state` 의 `pending_triggers.self_assessment` 확인
- 있으면 `bin/learn-event --event-type self_assessment --score 0.8 --silent`
- 없으면 정중히 거절: *"방금 자기평가 요청한 적 없는데, '8점'만으로는 의도 모르겠어. 'DI 8점이야'처럼 풀어줄래?"* (random score는 mastery 오염 방지)

---

## 시나리오 8 — 영어 query (S9, BGE-M3 multilingual)

**학습자**: "What is dependency injection in Spring?"

**AI 세션 자동**: 그대로 `bin/ask` 호출. BGE-M3가 multilingual이라 spring/* 카테고리 정상 surface.

**AI 세션 → 학습자**: 한국어로 답변 (학습자 평소 톤 유지) + 영어 keyword가 자연스럽게 섞임.

---

## 시나리오 9 — Multi-turn 깊은 대화 (P2 Phase N)

**Turn 1**: "Bean DI 기본" → cs_qa, spring/bean-di-basics + spring/ioc-di-basics surface.
**Turn 2**: "그럼 IoC 컨테이너는?" → daemon이 recent_history(tail=20)에서 turn 1 reference 포함된 prompt 생성. AI 세션은 turn 1의 맥락 이어받아 답변.
**Turn 3**: "두 개 차이가 뭐야" → recent_history에 turn 1+2 모두 포함. AI는 두 개념 차이 직접 비교.

검증: turn 3 prompt markdown에 rag_ask 이벤트 20개 모두 surface (Phase N2).

---

## 시나리오 10 — 학습자 mistakes / fallback

**학습자**: "안녕"

**AI 세션 자동**: `bin/ask "안녕"` → router가 short greeting 감지 → `tool_only` mode (no RAG cost).

**AI 세션 → 학습자**: *"안녕! 뭘 학습하고 싶어?"* (간단 인사 + 안내)

---

## 자동화 요약

학습자가 명시적으로 호출하는 명령: **0개**.

AI 세션이 자동으로:
1. `bin/ask` (매 turn)
2. `bin/capture-response` (hook-first full body capture; hook 불가 시 `bin/learn-response-quality` fallback)
3. `bin/learn-event` (학습자 코드 수정 / drill 답변 / pending 응답 시)
4. `bin/mission-patterns-build` / `bin/cross-crew-build` (학습자가 mission repo 분석 의도 표현 시 1회)
5. `bin/rag-daemon start` (첫 진입 시)
6. (Mode B) 측정 명령들 (학습자가 "회귀 측정", "코퍼스 확장" 같은 시스템 의도 표현 시)

학습자는 한국어 의도만 표현, AI 세션이 모든 명령 + 결과 해석을 자동 수행.
