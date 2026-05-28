# Response Capture Token Optimization Plan

## 문제 정의

현재 학습 답변 품질 수집은 `bin/learn-response-quality --response-file -`를 기본으로 안내한다. AI가 이미 학습자에게 긴 답변을 출력한 뒤 같은 본문을 heredoc/stdin으로 다시 넘기면, 로컬 저장 자체는 LLM 토큰을 쓰지 않지만 AI 세션 transcript에는 답변 본문이 한 번 더 들어간다.

결과적으로 장시간 구독제 AI 세션에서 다음 비용이 증가한다.

- context compaction이 빨라진다.
- 긴 turn 뒤 후속 응답 latency가 늘 수 있다.
- 이전 대화 세부정보가 빨리 압축된다.
- 사용자는 풍부한 학습 데이터를 얻지만, 같은 본문을 transcript에 2회 보관하는 비효율을 감수한다.

## 현재 수집 파이프라인

| 파이프라인 | 저장 위치 | 토큰 영향 | 비고 |
|---|---|---:|---|
| `bin/ask` / daemon `rag_ask` | `state/learner/history.jsonl`, `mastery_graph.sqlite` | 낮음 | prompt, route, latency, top concepts 저장 |
| `bin/learn-response-quality` | `response-quality.jsonl` | stdin 사용 시 높음 | 답변 본문 재전송이 transcript를 키움 |
| `bin/learn-event` / drill/self-assess | `history.jsonl`, profile | 낮음 | 구조화 입력 중심 |
| `bin/learn-record-code` / `learn-test` | `history.jsonl` | 낮음 | summary/XML parse 중심 |
| `learn-pr-retro`, `assess-learner-state` | repo state/report | 낮음 | 로컬/gh 데이터 수집 |
| mining/profile recompute | reports/profile | 없음 | 로컬 후처리 |

토큰 병목은 사실상 **response full body를 AI tool call에 다시 싣는 운영 방식**이다.

## 최적화 정책

1. 기본 원칙은 매 turn full body capture다.
   - 학습 품질을 위해 최종 답변 전체를 저장한다.
   - 저장된 full body는 PII redaction 후 `state/learner/response-bodies/sha256/`에 남긴다.
   - full body path는 redacted content hash 기반이므로 같은 본문은 한 번만 저장된다.
   - `response-quality.jsonl`에는 summary, hash, length, excerpt, capture metadata, dedupe 여부를 남긴다.

2. full body는 zero-copy 경로에서 우선 수집한다.
   - host/client가 최종 답변을 로컬 파일에 자동 materialize할 수 있으면 `--response-path <answer.md>`를 사용한다.
   - 이 경우 transcript에는 짧은 path command만 남고, redacted full body는 `state/learner/response-bodies/`에 저장된다.

3. stdin/heredoc은 universal fallback이다.
   - zero-copy path capture가 없는 일반 AI 세션에서는 `--response-file -`로 최종 답변 전체를 넘겨 full body 저장을 보장한다.
   - 이 경로는 transcript token을 더 쓰지만, 본문 저장 누락보다 우선순위가 높다.

4. summary-only는 예외다.
   - full-body capture가 정말 불가능할 때만 사용한다.
   - `body_not_captured`, `token_efficient_summary_only`, `declared_citation_unverified`를 남겨 데이터 품질 한계를 명시한다.

## 기대 효과

예시 12KB 학습 답변 기준:

- 기존 stdin/heredoc: 답변 12KB가 transcript에 추가로 한 번 더 들어간다.
- path capture: transcript 추가분은 대략 150자 안팎의 command/path다.
- summary-only: transcript 추가분은 짧은 command와 1줄 summary 수준이다.

즉 full body를 계속 보존할 수 있는 host에서는 학습 데이터 풍부함을 유지하면서 transcript 추가 비용을 90% 이상 줄일 수 있다. zero-copy host가 없는 일반 구독제 AI 세션에서는 full body 저장을 위해 stdin fallback을 사용하므로 추가 토큰 비용이 남는다. 이 비용은 의도적인 trade-off다.

저장 측면에서는 content-addressed sidecar로 중복 본문을 제거한다. 같은 redacted body가 여러 turn에서 반복되면 `response-quality.jsonl` row는 모두 남기되 `response_body_path`는 같은 파일을 가리키며, 중복 row에는 `response_body_deduped=true`, `response_body_stored_bytes=0`이 기록된다. 따라서 학습 이벤트의 풍부한 join 정보는 잃지 않고 디스크 쓰기와 저장 용량만 줄인다.

## 검증 기준

- `learn-response-quality --response-path`가 body를 읽고 redacted full body 파일을 저장한다.
- 동일 redacted body는 content-addressed path 하나로 dedupe된다.
- `--response-file -`는 full body 저장의 universal fallback으로 유지된다.
- `summary-only`는 body 미수집을 명시적 contract flag로 남기며 예외 경로로만 쓰인다.
- `response-quality-mine`이 capture method, full body path 수, dedupe 수, transcript 입력 대비 응답 길이 비율을 집계한다.
- `response_quality_hint`는 full body required, path preferred, stdin fallback, summary-only exceptional을 모두 표현한다.
- benchmark에서 path/summary mode의 transcript 추가량이 stdin 대비 90% 이상 감소한다.
- benchmark에서 동일 10KB급 답변 100건은 row 100개와 body path 100개를 유지하되 sidecar 파일 1개만 생성한다.
