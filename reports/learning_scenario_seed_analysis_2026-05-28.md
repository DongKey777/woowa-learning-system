# Learning Scenario Seed Analysis — 2026-05-28

## Scope

실제 학습 흐름을 만들기 위해 최근 사용자 질문 축을 13턴 시나리오로 묶고, `WOOWA_SESSION_MODE=learning` 상태에서 `bin/ask --json`을 실행했다. 각 응답은 `bin/learn-response-quality --response-file -`로 실제 본문까지 연결해 기록했다.

피드백 신호는 학습자의 명시적 helpful/not-helpful 판단이어야 하므로 임의 생성하지 않았다.

## Scenario

| tag | prompt family | expected behavior |
|---|---|---|
| corpus-quality | 코퍼스 품질/확장 기준 | system/meta fallback |
| remote-index | 원격 인덱스 빌드 판단 | system/meta fallback |
| index-rebuild | 코퍼스 변경 후 재빌드 판단 | system/meta fallback |
| retrieval-vs-body | retrieval 적합도와 본문 품질 | system/meta fallback |
| learner-id | learner_id 기록 검증 | system/meta fallback |
| quality-feedback | response-quality vs feedback 분석 | system/meta fallback |
| dev-contamination | 개발 데이터 오염 방지 | system/meta fallback |
| citation-drift | citation drift 점검 | system/meta fallback |
| batch-audit | 20개 배치 코퍼스 점검 | system/meta fallback |
| profile-recompute | 프로필 재계산 영향 | system/meta fallback |
| di-basics | DI 기본 개념 | `cs_qa` + Spring/DI citations |
| optional | Java Optional 사용/회피 기준 | `cs_qa` + Java Optional citations |
| transactional | self-invocation transaction 문제 | `cs_qa` + Spring transaction citations |

## Data Accumulated

Before this exercise, `state/learner/response-quality.jsonl` had no production learning rows. After the scenario runs:

| metric | value |
|---|---:|
| scenario response-quality rows | 27 |
| scenario rag_ask rows joined | 27 |
| scenario router modes | `tier_0_fallback`: 21, `cs_qa`: 6 |
| rows with recorded latency | 13 |
| response-quality missing body | 0 |
| citation drift | 0 |
| explicit feedback rows | 0 |
| learning-turn-audit issues | 0 |

The 27 rows are: initial 13-turn seed, 1 Optional reroute verification, and 13-turn latency pass after adding daemon latency telemetry.

## Latency

Latest 13-turn latency pass:

| tag | mode | daemon latency ms | CLI wall ms | citations |
|---|---:|---:|---:|---|
| corpus-quality | `tier_0_fallback` | 2.3 | 33.1 | 0 |
| remote-index | `tier_0_fallback` | 1.1 | 32.9 | 0 |
| index-rebuild | `tier_0_fallback` | 0.7 | 28.9 | 0 |
| retrieval-vs-body | `tier_0_fallback` | 0.9 | 31.9 | 0 |
| learner-id | `tier_0_fallback` | 1.0 | 31.7 | 0 |
| quality-feedback | `tier_0_fallback` | 1.2 | 35.2 | 0 |
| dev-contamination | `tier_0_fallback` | 1.0 | 27.3 | 0 |
| citation-drift | `tier_0_fallback` | 1.0 | 34.5 | 0 |
| batch-audit | `tier_0_fallback` | 2.2 | 32.9 | 0 |
| profile-recompute | `tier_0_fallback` | 0.7 | 42.9 | 0 |
| di-basics | `cs_qa` | 49.6 | 81.5 | 3 |
| optional | `cs_qa` | 190.7 | 220.7 | 3 |
| transactional | `cs_qa` | 117.5 | 151.1 | 3 |

Summary:

| slice | n | avg ms | p50 ms | max ms |
|---|---:|---:|---:|---:|
| daemon all | 13 | 28.5 | 1.1 | 190.7 |
| daemon fallback | 10 | 1.2 | 1.0 | 2.3 |
| daemon cs_qa | 3 | 119.3 | 117.5 | 190.7 |
| CLI all | 13 | 60.4 | 33.1 | 220.7 |

## Findings

1. `Java Optional은 언제 쓰고 언제 피해야 해?` was incorrectly routed to `tier_0_fallback` because `언제` / `피해야` / use-avoid phrasing was absent from learning-intent tokens. It now routes to `cs_qa` and retrieves:
   - `language/java-optional-basics`
   - `language/optional-field-parameter-antipattern-card`
   - `language/optional-collections-domain-null-handling-bridge`

2. `rag_ask` telemetry did not store latency, so quality/performance/latency could not be joined at the learning-event level. Daemon `ask` now records `payload.latency_ms` and returns top-level `latency_ms` in JSON mode.

3. `response-quality` and `feedback` rows did not store `mode`, which made it impossible for mining tools to distinguish production learning data from development/test rows. Both now persist `mode`.

4. System-maintenance/meta questions are intentionally guarded as `tier_0_fallback`. They produce response-quality telemetry but do not enrich concept mastery. That is correct for learner-profile hygiene; if system-maintenance Q&A should become a supported learning domain later, it needs a separate mode rather than being mixed into CS mastery.

5. Profile recompute remains stable:
   - `learner_id`: `DongKey777`
   - `events_total`: 4324
   - `experience_level`: `advanced`
   - mastered concepts still include DI and transactional concepts
   - recommendations remain algorithm-heavy because the scenario added mostly meta/system turns plus already-known CS concepts.

## Verification Commands

```bash
WOOWA_SESSION_MODE=development python3 -m pytest \
  tests/test_daemon.py \
  tests/test_intent_should_use_rag.py \
  tests/test_router.py \
  tests/test_response_quality.py \
  tests/test_mining_complete.py -q

WOOWA_SESSION_MODE=development bin/response-quality-mine --top-n 12
WOOWA_SESSION_MODE=development bin/learning-turn-audit --last 100
WOOWA_SESSION_MODE=development bin/routing-analyze --top-n 12
WOOWA_SESSION_MODE=development bin/profile-recompute --silent
```

Current targeted test result: `78 passed`.
