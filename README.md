# Woowa Learning System

우아한테크코스 미션 학습용 **자기 라우팅 RAG + 코드 그라운디드 + Bloom 자동 진행 + 멀티 에이전트** 학습 시스템.

```
학습자 자연어 질문
       ↓
   bin/ask (소켓 클라이언트)
       ↓
   daemon (BGE-M3 warm, AF_UNIX)
       ↓
   router (7 mode 결정) → lazy_loader (필요한 artifact만) → coach (multi-agent prompt)
       ↓
   AI 세션 (Claude / Codex / Gemini) → 학습자에게 답변
       ↓
   feedback: history.jsonl append + Bloom 자동 진행 (attempted → familiar → proficient → mastered)
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
   - Python 3.10+ 확인 + `pip install -e .`
   - BGE-M3 모델 캐시 다운로드 (~3GB, 첫 실행만)
   - `bin/index-fetch` — GitHub Releases에서 사전 빌드된 Lance 인덱스 다운로드 (약 13-18MB, ~15초)
   - `bin/rag-daemon start-bg` 백그라운드
5. 학습자는 한국어로 질문만 던지면 됨. *"Bean DI가 뭐야"*, *"내 ReservationController 어떻게 리팩토링"*, *"다른 크루는 어떻게 작성했어"*, *"확인 질문 줘"* 등.

> **🚫 학습자 기기에서 인덱스 빌드 금지** — `bin/corpus-build`는 15-30분 + 4-6GB peak RAM이라 학습 흐름 차단. 새 인덱스 버전은 maintainer가 RunPod에서 빌드 후 GitHub Releases에 publish, 학습자는 `bin/index-fetch --tag <new>` 로만 업데이트.

## AI 세션 진입 문서

- **Claude (Claude Code, Anthropic API)** → [`CLAUDE.md`](CLAUDE.md)
- **OpenAI / Codex / Gemini / 일반 AI** → [`AGENTS.md`](AGENTS.md)

두 문서는 동일한 First-Run Protocol + system contract를 포함, 톤만 다름.

## 검증 상태 (2026-05-27)

| Gate | 결과 |
|---|---|
| Release acceptance | **96/96 RELEASE READY** |
| Y13 Quality / Performance / Latency gates | **47/47 ✅** |
| Unit tests | **484 passed** |
| Runtime LOC budget | **9496 / 9500 ✅** |
| Index release artifact | **17.9MB, SHA256 검증 ✅** |

자세한 결과 → [`docs/verification-results.md`](docs/verification-results.md)

## 핵심 capability

- **F1 RAG**: qrels strict top1 1.000, MRR 1.000, NDCG@5 0.987
- **F2-F4 coaching/retro**: mentor concern alignment 86.7%, recurring signal 100%
- **F5 Bloom autoloop**: 5 mastered + 2 proficient 자동 진행
- **F6 drill**: corpus 100% 커버 (3199 concept 모두 non-stub 질문 생성)
- **F8 prereq**: concept_graph 5764 edge, 100% level-correct
- **F10 forward** (learner 코드 → concept): Tier 1 100%, Tier 2 85.2%
- **F10 backward** (concept → learner 파일): 100%
- **F11 cross-crew**: AI judge precision 85% (4-stage filter)
- **Latency**: warm CLI p50/p95 43.9ms / 47.6ms, first ask p50/p95 187.2ms / 196.0ms
- **Token cost**: 평균 prompt payload 약 4.1KB

## 아키텍처 / API / 운영 가이드

| 문서 | 내용 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 7 mode router + multi-agent + Bloom + F10/F11 design |
| [`docs/onboarding.md`](docs/onboarding.md) | First-Run Protocol 상세 + 트러블슈팅 |
| [`docs/bin-reference.md`](docs/bin-reference.md) | 주요 `bin/*` entry와 Phase T-X wrapper 사용법 |
| [`docs/learning-flow.md`](docs/learning-flow.md) | 학습자 일상 사용 시나리오 |
| [`docs/artifact-catalog.md`](docs/artifact-catalog.md) | `state/` `reports/` `corpus/` 구조 |
| [`docs/testing-guide.md`](docs/testing-guide.md) | release acceptance와 benchmark 재현 |
| [`docs/verification-results.md`](docs/verification-results.md) | 모든 측정 결과 인덱스 |

## Mode A vs Mode B (세션 모드)

학습자가 같은 AI 세션에서 미션 학습(Mode A)과 시스템 개발(Mode B)을 섞으면 personalization 데이터가 오염됨. AI는 의도를 읽고 `WOOWA_SESSION_MODE`를 자동 설정:

- **Mode A (learning, 기본값)**: 미션 코딩, 개념 질문, drill, coaching. `code_attempt` 이벤트가 `learning` 모드로 기록되어 mastery autoloop에 반영.
- **Mode B (development)**: `corpus/`, `core/`, `bin/`, `tests/`, `docs/` 수정 같은 시스템 작업. `WOOWA_SESSION_MODE=development` set 후 후속 명령 호출. personalization stream에서 자동 제외.

## License

internal — 학습 시스템 평가용.
