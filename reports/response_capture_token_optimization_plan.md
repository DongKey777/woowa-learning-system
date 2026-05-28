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

1. 기본값은 token-safe telemetry다.
   - `--summary-only --contract-flag body_not_captured --contract-flag token_efficient_summary_only`
   - 항상 `source_event_id`, expected citations, summary, 품질 flag는 남긴다.

2. full body는 zero-copy 경로에서 우선 수집한다.
   - host/client가 최종 답변을 로컬 파일에 자동 materialize할 수 있으면 `--response-path <answer.md>`를 사용한다.
   - 이 경우 transcript에는 짧은 path command만 남고, redacted full body는 `state/learner/response-bodies/`에 저장된다.

3. stdin/heredoc은 호환 fallback이다.
   - 짧은 답변 또는 body가 이미 tool stdin으로 전달될 수밖에 없는 host에서만 사용한다.
   - 긴 답변을 telemetry만을 위해 heredoc으로 다시 붙여넣지 않는다.

4. full body가 들어온 경우 데이터는 더 풍부하게 남긴다.
   - `response-quality.jsonl`: summary, flags, hash, length, capture_method, excerpt prefix.
   - `response-bodies/*.md`: PII redacted full final answer.
   - `response_excerpt_truncated=true`로 JSONL prefix 잘림 여부를 명시한다.

## 기대 효과

예시 12KB 학습 답변 기준:

- 기존 stdin/heredoc: 답변 12KB가 transcript에 추가로 한 번 더 들어간다.
- path capture: transcript 추가분은 대략 150자 안팎의 command/path다.
- summary-only: transcript 추가분은 짧은 command와 1줄 summary 수준이다.

즉 full body를 계속 보존할 수 있는 host에서는 학습 데이터 풍부함을 유지하면서 transcript 추가 비용을 90% 이상 줄일 수 있다. zero-copy host가 없는 일반 구독제 AI 세션에서는 summary-only를 기본으로 하여 장시간 학습의 context 수명을 보호한다.

## 검증 기준

- `learn-response-quality --response-path`가 body를 읽고 redacted full body 파일을 저장한다.
- `summary-only`는 body 미수집을 명시적 contract flag로 남긴다.
- `response-quality-mine`이 capture method, full body path 수, transcript 입력 대비 응답 길이 비율을 집계한다.
- `response_quality_hint`는 stdin 강제를 중단하고 path/summary/stdin fallback을 모두 제공한다.
- benchmark에서 path/summary mode의 transcript 추가량이 stdin 대비 90% 이상 감소한다.
