# Woowa Learning System

우아한테크코스 미션 학습용 **자기 라우팅 RAG + 코드 그라운디드 + Bloom 자동 진행 + 멀티 에이전트** 학습 시스템.

```
학습자 자연어 질문
       ↓
   bin/ask (소켓 클라이언트)
       ↓
   daemon (BGE-M3 warm, AF_UNIX)
       ↓
   router (질문 의도 → 모드 결정) → lazy_loader (필요한 artifact만) → coach (multi-agent prompt)
       ↓
   AI 세션 (Claude / Codex / Gemini) → 학습자에게 답변
       ↓
   hook-first telemetry: history + full answer body capture + Bloom 자동 진행
```

## 5분 시작 (학습자)

1. Clone:
   ```bash
   git clone https://github.com/DongKey777/woowa-learning-system.git
   cd woowa-learning-system
   ```
2. AI 세션 열기 — Claude Code / Codex CLI / Gemini CLI 중 아무거나.
3. AI에게 한국어로 한 문장: *"세팅하고 학습 시작하자"*
4. AI가 [First-Run Protocol](docs/onboarding.md)을 자동 수행 (~6분):
   - Python 3.10+ 확인 + `bin/setup` (`.venv` 생성 후 `pip install -e .` — PEP 668 시스템 Python에서도 마찰 없이)
   - BGE-M3 모델 캐시 다운로드 (~3GB, 첫 실행만)
   - `bin/index-fetch` — GitHub Releases에서 사전 빌드된 Lance 인덱스 다운로드 (약 13-19MB, ~15초)
   - `bin/rag-daemon start-bg` 백그라운드
5. 학습자는 한국어로 질문만 던지면 됨. *"Bean DI가 뭐야"*, *"내 ReservationController 어떻게 리팩토링"*, *"다른 크루는 어떻게 작성했어"*, *"확인 질문 줘"* 등.

AI 세션은 답변 직후 학습 데이터를 자동 저장한다. Claude/Codex/Gemini hook 환경에서는 `bin/capture-response`가 최종 답변 전체를 pending `rag_ask`에 자동 연결한다. 수집 실패는 학습 흐름을 막지 않고 repair queue에 남기며, 필요하면 *"학습 기록 저장은 나중에 자동 보정할게."* 정도로만 짧게 안내한다. hook이 없는 환경은 `--response-path <answer.md>` 또는 `--response-file -` fallback을 사용한다. 저장된 본문 파일은 redacted content hash 기준으로 dedupe된다.

> **🚫 학습자 기기에서 인덱스 빌드 금지** — `bin/corpus-build`는 15-30분 + 4-6GB peak RAM이라 학습 흐름 차단. 새 인덱스 버전은 maintainer가 RunPod에서 빌드 후 GitHub Releases에 publish, 학습자는 `bin/index-fetch --tag <new>` 로만 업데이트.

## AI 세션 진입 문서

- **Claude (Claude Code, Anthropic API)** → [`CLAUDE.md`](CLAUDE.md)
- **OpenAI / Codex / Gemini / 일반 AI** → [`AGENTS.md`](AGENTS.md)

두 문서는 동일한 First-Run Protocol + system contract를 포함, 톤만 다름.

## 검증 상태 (2026-05-28)

| Gate | 결과 |
|---|---|
| Release acceptance | **96/96 RELEASE READY** |
| Y13 Quality / Performance / Latency gates | **47/47 ✅** |
| Y14 corpus closure qrels | **14/14 top1=1.000, p95≤3.6ms ✅** |
| Unit tests | **523 passed** |
| Runtime LOC budget | **9574 / 9700 ✅** |
| Index release artifact | **18.7MB, SHA256 검증 ✅** |

자세한 결과 → [`docs/verification-results.md`](docs/verification-results.md)

## 핵심 capability

- **F1 RAG**: Y14 qrels strict top1/MRR/NDCG 1.000, rag_quality top1/NDCG 1.000
- **F2-F4 coaching/retro**: mentor concern alignment 86.7%, recurring signal 100%
- **F5 Bloom autoloop**: `rag_ask`/code/drill evidence 자동 누적, 2026-05-28 learner state reset 후 실제 사용 데이터로 재누적
- **F6 drill**: corpus 100% 커버 (3339 concept 모두 non-stub 질문 생성)
- **F8 prereq**: concept_graph 6172 prerequisite edges, broken edge 0
- **F10 forward** (learner 코드 → concept): Tier 1 100%, Tier 2 85.2%
- **F10 backward** (concept → learner 파일): 100%
- **F11 cross-crew**: AI judge precision 85% (4-stage filter)
- **Latency**: warm CLI p50/p95 43.9ms / 47.6ms, first ask p50/p95 187.2ms / 196.0ms
- **Prompt payload**: 14-scenario 평균 약 4.1K chars

## 지원 기능 목록 (학습 모드)

한국어로 의도만 말하면 AI 세션이 발화를 읽고 알맞은 모드로 보낸다. 모드가 애매하면 키워드 router가 받쳐준다.

**기본 질문/코칭 모드** — 개념을 배우거나 코드 코칭을 받는 평소 경로:

| 모드 | 무엇을 답하나 | 이렇게 물어보면 |
|---|---|---|
| `cs_qa` | CS 개념 설명 (corpus RAG) | *"Bean DI가 뭐야"* |
| `coaching` | 내 미션 코드 리팩토링 코칭 | *"ReservationController 어떻게 개선해?"* |
| `drill` | 확인 질문 출제 + 4축 채점 | *"확인 질문 줘"* |
| `self_assess` | 자가평가 점수 반영 (평가 대기 중일 때만) | *"DI 8점"* |
| `retro` | 멘토가 반복해서 짚은 부분 돌아보기 | *"내 PR 흐름 보여줘"* |

**미션 그라운디드 분석 모드 (A–N)** — 내 실제 PR·리뷰·동기 데이터를 읽어 답하는 전용 경로:

| 모드 | 무엇을 답하나 | 이렇게 물어보면 |
|---|---|---|
| `pr_review` (A) | 받은 리뷰 모아서 정리 | *"받은 리뷰 정리해줘"* |
| `reviewer_profile` (C) | 멘토가 어떤 스타일로 리뷰하는지 | *"내 멘토 리뷰 스타일 어때?"* |
| `learning_path` (D) | 지금 실력에 맞춰 다음 학습 순서 추천 | *"다음에 뭐 배우면 좋을지 알려줘"* |
| `meta_analytics` (E) | 내가 자주 묻는 것과 학습 패턴 짚기 | *"내가 자주 묻는 개념 뭐야?"* |
| `cohort` (F) | 동기들과 비교해 내 PR이 어디쯤인지 | *"동기들에 비해 내 PR 어때?"* |
| `thread_recon` (G) | 리뷰 스레드를 통째로 복원 | *"내 리뷰 스레드 통째로 보여줘"* |
| `pr_diff_evolution` (H) | 라운드마다 코드가 어떻게 바뀌었는지 + 리뷰→수정 연결 | *"라운드별 코드 변화 보여줘"* |
| `temporal` (I) | 리뷰 주기와 오래 멈춘 구간 짚기 | *"내 리뷰까지 얼마나 걸렸어?"* |
| `pr_meta` (J) | PR 크기·커밋 응집도 점검 | *"내 PR 너무 큰지 봐줘"* |
| `predict` (K) | C·F·H를 합쳐 어떤 리뷰가 올지 미리 예측 | *"내 PR 리뷰 미리 예측해줘"* |
| `cross_mission` (L) | 미션을 넘나드는 반복 실수와 개념 전이 | *"미션 간 반복된 실수 알려줘"* |
| `memory_review` (M) | 안 본 사각지대 개념과 복습 카드 | *"사각지대 개념 알려줘"* |
| `f11_anchor` (B) | 같은 미션 다른 크루 코드와 비교 | *"다른 크루 코드 보여줘"* |

## 아키텍처 / API / 운영 가이드

| 문서 | 내용 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | router (AI 세션 드리븐 + 키워드 fallback) + multi-agent + Bloom + F10/F11 design |
| [`docs/onboarding.md`](docs/onboarding.md) | First-Run Protocol 상세 + 트러블슈팅 |
| [`docs/bin-reference.md`](docs/bin-reference.md) | 주요 `bin/*` entry와 Phase T-X wrapper 사용법 |
| [`docs/learning-flow.md`](docs/learning-flow.md) | 학습자 일상 사용 시나리오 |
| [`docs/artifact-catalog.md`](docs/artifact-catalog.md) | `state/` `reports/` `corpus/` 구조 |
| [`docs/testing-guide.md`](docs/testing-guide.md) | release acceptance와 benchmark 재현 |
| [`docs/verification-results.md`](docs/verification-results.md) | 모든 측정 결과 인덱스 |

## Mode A vs Mode B (세션 모드)

학습자가 같은 AI 세션에서 미션 학습(Mode A)과 시스템 개발(Mode B)을 섞으면 personalization 데이터가 오염됨. AI 세션이 의도를 읽고 모드를 **명시 선언**한다(`bin/ask --session-mode learning|development`, provenance `explicit`) — 명시가 없으면 `WOOWA_SESSION_MODE` env(`env`), 그것도 없으면 보수적 `learning`(`default`). 이 출처는 이벤트 `mode_source`에 남는다:

- **Mode A (learning, 기본값)**: 미션 코딩, 개념 질문, drill, coaching. `code_attempt` 이벤트가 `learning` 모드로 기록되어 mastery autoloop에 반영.
- **Mode B (development)**: `corpus/`, `core/`, `bin/`, `tests/`, `docs/` 수정 같은 시스템 작업. `WOOWA_SESSION_MODE=development` set 후 후속 명령 호출 (또는 개별 `bin/ask`에 `--session-mode development`). personalization stream에서 자동 제외.

## License

internal — 학습 시스템 평가용.
