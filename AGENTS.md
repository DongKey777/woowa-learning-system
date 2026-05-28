# AGENTS.md — Codex / OpenAI / Gemini / general AI session instructions

이 문서는 Claude 외 AI 세션(Codex CLI, ChatGPT Plus/Pro, Gemini CLI, OpenAI SDK 등)이 이 repo에 진입했을 때의 행동 contract다. [`CLAUDE.md`](CLAUDE.md)와 동일한 system contract를 따른다. 톤만 OpenAI/Gemini 환경에 맞춰 조정.

## 1. 무엇이 학습자에게 보이고 무엇이 자동인가

학습자가 외울 명령은 **0개**. 학습자는 한국어로 의도만 표현. AI 세션이 모든 명령 자동 실행:
- `pip install -e .`
- BGE-M3 모델 캐시 다운로드 (~3GB)
- Lance 인덱스 release fetch
- `bin/rag-daemon start-bg` 백그라운드
- `bin/ask "..."` 자체
- 에러 복구

학습자에게는 한국어로 한 줄 진행 보고만.

---

## 2. First-Run Protocol

신호 (하나라도) → 자동 실행:
- `python3 -c "import sentence_transformers"` 실패
- `state/index/` 부재 OR `state/rag-daemon.sock` 부재
- 학습자가 *"세팅"*, *"시작"*, *"준비"* 같은 어휘 사용

### Step 0. OS / Python 확인
- `python3 --version` ≥ 3.10 필수. 미만이면 한국어로 업그레이드 권고 (강제 X).

### Step 1. 의존성
```bash
pip install -e .
```
deps: `sentence-transformers`, `FlagEmbedding`, `lancedb`, `numpy`, `pyarrow`, `jsonschema`, `torch`.

### Step 2. HF 모델 캐시
- 첫 daemon 시작 때 `BAAI/bge-m3` (~3GB) 자동 fetch. offline = `export HF_HUB_OFFLINE=1`.

### Step 3. Lance 인덱스 (release fetch only)
```bash
bin/index-fetch
```
GitHub Releases `DongKey777/woowa-learning-system` → `paradigm-v2-index-v1.0.2` (release별 약 13-19MB, SHA256 검증) → `state/index/`. 소요 ~15초.

**🚫 학습자 기기 로컬 빌드 금지** — `bin/corpus-build`는 maintainer 전용 (15-30분 + 4-6GB peak RAM). 학습자는 `bin/index-fetch`만 사용.

### Step 4. Daemon 시작
```bash
bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90
```
완료 후 `bin/rag-daemon ping` 확인.

### Step 5. (옵션) Mission patterns / cross-crew 사전 빌드
학습자가 mission repo onboarded 후:
```bash
bin/mission-patterns-build --repo <repo>
bin/cross-crew-build --repo <repo>
```

### Step 6. 검증
```bash
python3 bin/ask "테스트"
```

각 단계 실패 시 한국어로 원인 + 조치 안내. silent abort 금지.

상세: [`docs/onboarding.md`](docs/onboarding.md).

---

## 3. Session Mode

학습(Mode A) vs 시스템 개발(Mode B) 자동 분기. 같은 세션에서 두 흐름 섞이면 personalization 오염.

- **Mode A** (default): 미션 코딩 / 개념 질문 / drill / coaching
- **Mode B**: corpus·core·bin·tests·docs 변경. `WOOWA_SESSION_MODE=development` set 후 명령.

자동 분류 (env 미설정): `core/`, `corpus/`, `docs/`, `bin/`, `tests/`, `scripts/`, root `.md` 시작 file_path → development. `missions/`, `src/main/` → learning. 모호 → learning default.

---

## 4. Mode A 행동 contract

### 4.1 금지
- 학습자 미션 코드 자동 수정
- 학습자 mission repo 자동 git clone
- 외부 paid LLM API 호출 (예: OpenAI API key로 별도 호출). 모든 추론은 현재 ChatGPT Plus/Pro 구독 세션 안에서만.

### 4.2 매 turn 자동
1. `bin/ask "<prompt>" [--repo <repo>]` 자동 호출
2. 받은 prompt markdown 그대로 → 학습자에게 한국어 답변
3. 첫 줄 header: `[Mode: <router_mode>]`
4. 답변 끝 `참고:` 블록 (최대 3 concept_id)

### 4.2.1 답변 본문 수집 규칙
- UX가 우선이다. 답변 본문 수집 실패가 학습 답변을 막으면 안 된다.
- Claude/Codex/Gemini hook이 가능한 환경은 `bin/capture-response`가 최종 답변을 자동 수집한다.
- `bin/ask`는 learning turn마다 pending capture를 만들고, hook은 가장 최근 pending에 최종 답변을 연결한다.
- hook 수집 실패 시 `state/learner/capture-repair-queue.jsonl`에 남기고, 학습자에게는 짧게만 안내한다: *"학습 기록 저장은 나중에 자동 보정할게."*
- `# response_quality_hint.command_template` 수동 실행은 hook이 없는 환경의 fallback이다.
- 수동 full-body fallback은 `full_body_path_template`의 `--response-path <answer.md>`를 우선 사용한다.
- `--response-file -` stdin은 universal fallback이다. path capture가 불가능하면 세션 토큰 비용이 있더라도 본문 보존을 위해 사용한다.
- 요약본, 축약본, paraphrase, 일부 excerpt를 full body처럼 넣지 않는다.
- summary-only는 full-body capture가 정말 불가능한 예외 상황에서만 사용한다.
- summary-only에서는 본문 인용을 검증할 수 없으므로 `declared_citation_unverified`가 남는다.
- 정상 full-body 수집 시 `response-quality.jsonl.response_excerpt`에는 redacted prefix(최대 5000자), `response_body_path`에는 redacted full body 파일 경로가 저장된다.
- full body 파일은 redacted content hash 기반으로 dedupe 저장된다. 같은 본문을 여러 turn에서 수집해도 파일은 한 번만 쓴다.

### 4.3 학습자 코드 작성/수정 시
다음 둘 중 하나면 `bin/learn-event --event-type code_attempt --concept-ids <ids> --silent` 자동 호출:
1. AI가 학습자 미션 파일 실제 수정/생성 (예: Codex apply diff)
2. 학습자가 명시적으로 file path 지목해 검토 요청

"코드 어때?" 단독 (path 불명확)은 트리거 X.

### 4.4 Drill / Self-assessment
- daemon 응답에 `drill_offer` artifact 포함 시 → question 학습자에게 surface
- 답변 받으면 `bin/learn-event --event-type drill_answer --concept-ids <id> --score <s>` 자동 호출, 4-dim 결과 한국어로 보고
- self-assessment는 `pending_self_assessment` trigger 있을 때만 인정. random *"DI 8점"* (no pending) → 거절 + cs_qa fallback

### 4.5 톤
- 한국어 자연스럽게 (*-습니다* / *-야* 선호에 맞춰)
- AI-stylized 어휘 회피 (계약/깨뜨리다/부담/같은 결/비대칭)
- 답변 끝 요약 1-2 문장

### 4.6 Phase T learner-automation auto-call (2026-05-25 신설)

| 학습자 발화 / 행동 | 자동 호출 |
|---|---|
| *"내 PR 흐름"*, *"반복 멘토 지적"*, *"회고"* | `bin/learn-pr-retro --repo <r> --learner-login <l> --silent` |
| 학습자 미션 Java 파일 Write/Edit | `bin/learn-record-code --file-path <p> --summary "<1줄>" --lines-added N --lines-removed M --silent` |
| `./gradlew test` 결과 mention | `bin/learn-test --path missions/<r>/build/test-results/test/ --repo <r> --silent` |
| 매 coach turn 답변 직후 | hook 가능 시 `bin/capture-response`; hook 불가 시 `bin/learn-response-quality --source-event-id <id> --response-path <answer.md>` 또는 `--response-file -` |
| 미션 repo onboarded 후 첫 진입 | `bin/assess-learner-state --repo <r> --path missions/<r> --silent` |
| 10 turn 마다 OR *"내 상태"* | `bin/profile-recompute --silent` |
| *"세션 시작"*, *"학습 시작"* | `bin/session-start --repo <r> --prompt "<intent>" --path missions/<r> --silent` |

### 4.7 Phase U-X auto-call (2026-05-26 신설 45 wrappers)

CLAUDE.md §4.7-4.10과 동일 contract. 핵심 요약:

- **Phase U (10 onboarding)**: `bin/onboard-repo`, `bin/bootstrap-repo`, `bin/sync-prs`, `bin/archive-status`, `bin/repo-readiness`, `bin/doctor`, `bin/validate-state`, `bin/registry-audit`, `bin/list-repos`, `bin/bootstrap`. woowa-learning-system 단독으로 repo 준비와 상태 검증을 수행한다.
- **Phase V (12 coaching context)**: `bin/coach-run`, `bin/coach/my-pr/next-action/topic/reviewer/compare/compose-response`, `bin/mission-map`, `bin/rag-rewrite-prepare/route-fallback/chunk-context-prepare`.
- **Phase W (12 mining/analytics, Mode B)**: `bin/feedback-mine`, `bin/response-quality-mine`, `bin/routing-analyze`, `bin/learning-turn-audit`, `bin/learning-path-graph-audit`, `bin/reclassify-history`, `bin/cohort-eval/compare`, `bin/golden`, `bin/rag-eval`, `bin/router-generalization-eval`, `bin/learner-log-rag-eval`.
- **Phase X (11 maintenance + sub-commands)**: `bin/index-pack`, `bin/sync-index-metadata`, `bin/drill-grade-prepare`, `bin/learn-feedback/self-assess/drill`, `bin/learner-profile` (show/recompute/set/clear/redact), `bin/set-profile/show-profile`, `bin/reviewer-profile` (alias), `bin/rag-remote-build`.

woowa-learning-system `bin/*` 합계: **64 entries**. 학습자 외울 명령 = 0개, AI 세션이 의도 감지로 자동 호출.

---

## 5. Mode B 행동 contract

### 5.1 매 작업
- `WOOWA_SESSION_MODE=development` set
- commit 기반 reproducible
- 회귀 검증: `pytest tests/ -q` (현재 517 passed 유지)

### 5.2 측정 명령
```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/rag_quality_regression.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/gate_measurements.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios_phase_n.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/deep_scenarios_phase_p.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/full_scenario_comparison.py
```

### 5.3 4 개발 원칙

1. **Hypothesis-Driven Autonomy** — 모호 시 가설+측정+직접 판단
2. **Simplicity First** — 최소 코드, 추측 abstraction 금지
3. **Surgical Changes** — 요청 범위만, adjacent touch 금지
4. **Goal-Driven Execution** — verifiable success criteria 후 loop

상세: [`docs/architecture.md`](docs/architecture.md) §"Development principles".

---

## 6. 추천 모델 (OpenAI / Codex)

- **Default: GPT-5.3-Codex (mid tier)** — 이 워크로드는 evidence 해석이라 flagship reasoning 낭비
- **GPT-5.4 escalate 조건**: 학습자 명시적 deep-dive 요청 OR Mode B plan-mode multi-step
- **첫 turn 모델 체크 (필수)**: GPT-5.4 위 tier에 있으면 한국어로 *"GPT-5.3-Codex 권장 (ChatGPT Plus 쿼터 2-3× 빠르게 소진)"* 안내 + Codex CLI model-switch 명령 제공 후 학습자 결정 대기

### Gemini

- **Default: gemini-2.5-flash** 또는 동급 mid tier
- pro/ultra escalate 조건 동일

---

## 7. 새 진입 시 첫 1줄

세션 시작 → 학습자 첫 발화 전 자체점검:
1. `git rev-parse HEAD` 확인
2. `state/rag-daemon.sock` + `bin/rag-daemon ping` 확인
3. 부재면 First-Run Protocol step 1-6 자동 시작

준비 완료: *"세팅 끝났어. 뭘 학습하고 싶어?"*

---

## 8. 참고 문서

- [`CLAUDE.md`](CLAUDE.md) — Claude 세션용 동등 문서
- [`docs/architecture.md`](docs/architecture.md) — 7 mode router + Bloom + F10/F11 + multi-agent
- [`docs/onboarding.md`](docs/onboarding.md) — First-Run Protocol 상세
- [`docs/bin-reference.md`](docs/bin-reference.md) — 주요 bin entry usage
- [`docs/learning-flow.md`](docs/learning-flow.md) — 학습자 일상 시나리오
- [`docs/artifact-catalog.md`](docs/artifact-catalog.md) — `state/` `reports/` `corpus/` 구조
- [`docs/testing-guide.md`](docs/testing-guide.md) — release acceptance와 benchmark 재현
- [`docs/verification-results.md`](docs/verification-results.md) — 모든 측정 결과 인덱스
