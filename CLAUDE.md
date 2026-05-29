# CLAUDE.md — Claude session instructions for woowa-learning-system

이 문서는 Claude 세션(Claude Code, Anthropic API, Claude Pro/Max 구독)이 이 repo에 진입했을 때의 행동 contract다. 학습자 진입 시나리오를 100% 반영한다.

## 1. 무엇이 학습자에게 들리고 무엇이 자동인가

학습자가 외울 명령은 **0개**. 학습자는 한국어로 의도만 표현. 모든 명령은 Claude 세션이 자동 실행:

- `bin/setup` (의존성 설치 — 내부적으로 `.venv` 생성 후 `pip install -e .`)
- BGE-M3 모델 캐시 다운로드 (~3GB)
- Lance 인덱스 release fetch
- `bin/rag-daemon start-bg` 백그라운드
- `bin/ask "..."` 자체
- 에러 복구

학습자에게는 **한국어로 한 줄 진행 보고만** ("BGE-M3 다운로드 중…", "인덱스 다운로드 완료").

---

## 2. First-Run Protocol (새 클론 / 미설정 환경 감지 시)

다음 신호 중 하나라도 보이면 **순서대로 자동 실행**:
- `python3 -c "import sentence_transformers"` 실패
- `state/index/` 없음 OR `state/rag-daemon.sock` 없음
- 학습자가 *"세팅"*, *"시작"*, *"준비"* 같은 어휘 사용

### Step 0. OS / Python 검증
```bash
python3 --version  # 3.10+ 필수
uname -a
```
- < 3.10이면 한국어로 upgrade 권장 (강제 X). macOS: `brew install python@3.12`, Ubuntu: `sudo apt install python3.12`, Windows: `winget install Python.Python.3.12`.

### Step 1. Python 의존성
```bash
bin/setup
```
- `bin/setup`이 repo-local `.venv`를 만들고 그 안에 `pip install -e .`를 실행한다. macOS Homebrew / Debian 시스템 Python은 PEP 668 (externally-managed)이라 시스템에 직접 `pip install`이 거부되므로, venv가 마찰 없는 경로다 (`--break-system-packages` 불필요).
- 설치 후 `bin/` 명령들은 `core/_venv.py` 가드를 통해 `.venv`를 자동 사용한다 — 학습자/AI가 venv를 의식할 필요 없음. `.venv`가 없으면 가드는 no-op (시스템 Python에 deps가 이미 있는 maintainer 환경은 그대로).
- 핵심 deps: `sentence-transformers` (BGE-M3 wrapper), `transformers`, `torch` (MPS on M-series), `lancedb`, `numpy`, `pyarrow`, `jsonschema`. BGE-M3는 transformers + sentence-transformers로 로드한다 (FlagEmbedding은 RunPod 빌드 전용, 학습자 dep 아님).
- Mode B (시스템 개발)는 `bin/setup --dev`로 pytest까지 설치.

### Step 2. HuggingFace 모델 캐시 warm-up
- 첫 daemon 시작 시 `BAAI/bge-m3` (~3GB) 자동 다운로드.
- offline 환경: `export HF_HUB_OFFLINE=1` 후 사전 캐시된 모델 사용.

### Step 3. Lance 인덱스 다운로드 (release fetch only)
```bash
bin/index-fetch
```
- GitHub Releases `DongKey777/woowa-learning-system` → `paradigm-v2-index-v1.0.2` (release별 약 13-19MB, SHA256 검증) → `state/index/`
- 소요: ~15초
- `gh` CLI 미설치면 한국어로 OS별 설치 안내 후 재시도

**🚫 학습자 기기 `bin/corpus-build` 금지** — 15-30분 + 4-6GB peak RAM이라 학습 흐름 차단. 새 버전 필요 시 maintainer가 RunPod에서 빌드 후 `gh release create`, 학습자는 `bin/index-fetch --tag <new>` 로 업데이트.

### Step 4. Daemon 백그라운드 시작
```bash
bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90
```
- 첫 시작 후 모델 메모리 load가 끝날 때까지 대기.
- 학습자 첫 query 전에 `bin/rag-daemon ping` 확인.

### Step 5. Mission patterns / cross-crew 사전 빌드 (선택, 학습자가 미션 repo onboarded 후)
```bash
bin/mission-patterns-build --repo <repo-name>     # F10 forward 활성화
bin/cross-crew-build --repo <repo-name>           # F11 cross-crew 사전 계산
```
- 학습자가 *"<repo> 분석해줘"*, *"내 PR 보자"* 같은 의도 표현 시 자동 실행.

### Step 6. 첫 ask로 검증
```bash
python3 bin/ask "테스트"
```
- 정상 응답이 오면 onboarding 완료. 학습자에게 한국어로 한 줄 보고.

각 단계 실패 시 한국어로 원인 + 조치 안내. 절대 silent abort 금지.

---

## 3. Session Mode (학습 vs 시스템 개발)

같은 AI 세션에서 미션 학습(Mode A)과 시스템 개발(Mode B)을 섞으면 personalization 데이터 오염. Claude는 의도를 읽고 자동 분기:

### Mode A — Learning (기본값)
- 의도: 미션 코딩, 개념 질문, drill, coaching, 학습 테스트
- 환경: 별도 설정 불필요 (default = `learning`)
- 예: *"Bean DI 더 설명해줘"*, *"내 코드 어때"*, *"확인 질문 풀고 싶어"*

### Mode B — System Development
- 의도: corpus 수정, `core/`/`bin/`/`tests/`/`docs/` 변경, 회귀 측정, RunPod 빌드 등
- 환경: `WOOWA_SESSION_MODE=development` set 후 후속 명령
- 예: *"코퍼스 확장해"*, *"회귀 측정 돌려"*, *"새 bin entry 추가"*

자동 분류 (env 미설정 시): file_path가 `core/`, `corpus/`, `docs/`, `bin/`, `tests/`, `scripts/`, root `.md` 시작이면 `development`. `missions/`, `src/main/` 같은 미션 형태면 `learning`. 모호 → `learning` default (보수적).

---

## 4. Mode A 행동 contract (학습 세션)

### 4.1 절대 자동 X
- 학습자 미션 코드 자동 수정 — 학습자가 직접 작성. *"진행해보자"*는 "코치해줘"이지 "코드 만들어줘" 아님.
- 학습자 미션 repo 자동 git clone — 학습자가 `missions/<repo>/` 아래 직접 fork clone 후 알림.
- 외부 paid LLM API 호출 — 모든 reasoning은 현재 세션 안에서 (Claude Pro/Max 구독).

### 4.2 매 turn 자동 수행
1. **`bin/ask "<prompt>" [--repo <repo>]`** 자동 호출 — 학습자가 명령 외울 필요 없음.
2. 응답 받은 prompt markdown을 그대로 사용해 학습자에게 한국어 답변 생성.
3. 첫 줄 헤더: `[Mode: <router_mode>]` (예: `[Mode: cs_qa]`, `[Mode: coaching]`).
4. 답변 끝에 `참고:` 블록으로 인용 (최대 3개 concept_id).

### 4.2.1 답변 본문 수집 규칙
- UX가 우선이다. 답변 본문 수집 실패가 학습 답변을 막으면 안 된다.
- Claude Code hook 환경에서는 `Stop` hook이 `bin/capture-response`를 호출해 최종 답변 전체를 자동 수집한다.
- `bin/ask`는 learning turn마다 pending capture를 만들고, hook은 가장 최근 pending에 최종 답변을 연결한다.
- hook 수집 실패 시 `state/learner/capture-repair-queue.jsonl`에 남기고, 학습자에게는 짧게만 안내한다: *"학습 기록 저장은 나중에 자동 보정할게."*
- `# response_quality_hint.command_template` 수동 실행은 hook이 없는 환경의 fallback이다.
- 토큰 효율 수동 fallback은 `full_body_path_template`의 `--response-path <answer.md>`다.
- `--response-file -` stdin은 universal fallback이다. path capture가 불가능하면 세션 토큰 비용이 있더라도 본문 보존을 위해 사용한다.
- 요약본, 축약본, paraphrase, 일부 excerpt를 full body처럼 넣지 않는다.
- summary-only는 full-body capture가 정말 불가능한 예외 상황에서만 사용한다.
- summary-only에서는 본문 인용을 검증할 수 없으므로 `declared_citation_unverified`가 남는다.
- 정상 full-body 수집 시 `response-quality.jsonl.response_excerpt`에는 redacted prefix(최대 5000자), `response_body_path`에는 redacted full body 파일 경로가 저장된다.
- full body 파일은 redacted content hash 기반으로 dedupe 저장된다. 같은 본문을 여러 turn에서 수집해도 파일은 한 번만 쓴다.

### 4.3 학습자 코드 작성/수정 시
다음 조건 중 하나면 `bin/learn-event --event-type code_attempt --concept-ids <ids> --silent` 자동 호출:
- Claude가 학습자 미션 파일을 실제로 수정/생성한 경우 (Write/Edit tool)
- 학습자가 명시적으로 파일 경로 지목해 검토 요청 (*"`Reservation.java` 어때?"*)

"코드 어때?" 단독은 트리거하지 않음 (파일 path 불명확).

### 4.4 Drill / Self-assessment
- daemon `ask`가 drill mode dispatch + `drill_offer` artifact 포함 시 → 학습자에게 question surface.
- 학습자 답변 받으면 자동 `bin/learn-event --event-type drill_answer --concept-ids <id> --score <s>` 호출. 4-dimension 채점 결과 학습자에게 한국어로 풀어서 보고.
- self-assessment는 `pending_self_assessment` trigger가 있을 때만 인정. 학습자 random *"DI 8점"* (pending 없음) → 정중히 거절 + cs_qa 모드로 fallback.

### 4.5 응답 톤
- 한국어 자연스럽게, *-습니다* 또는 *-야* 톤 (학습자 선호에 맞춰).
- 인공적 표현 회피 (계약/깨뜨리다/부담/같은 결/비대칭 등 AI-stylized 단어 X).
- 답변 마지막 1줄 요약 1-2 문장. 길게 설명하지 않음.

### 4.7 Phase U onboarding/collection auto-call (2026-05-25 신설, Y6 갱신)

**시스템 규칙 (학습자 결정 불필요)**:
- `--owner` = `woowacourse` (Woowa 미션 표준 upstream)
- `--learner-login` = `DongKey777` (학습자 본인)
- clone source = 학습자 fork `DongKey777/<repo>` (mission_path 없으면 자동)

**새 미션 시작** (학습자: *"새 미션 spring-roomescape-waiting 시작할게"*):

```
bin/onboard-repo --repo <r> --owner woowacourse --learner-login DongKey777
  → 1. gh repo clone DongKey777/<r> → missions/<r>/ (if missing)
  → 2. bin/bootstrap-repo (upstream archive — woowacourse cohort PRs)
  → 3. bin/mission-patterns-build (F10 forward — 학습자 own Java patches)
  → 4. bin/anchors-build (F11 input — 학습자 own review threads, merge into existing)
  → 5. bin/cross-crew-build (F11 — anchors × cross-crew matching, BGE-M3)
```

**학습자가 PR push + mentor review 받은 후** (학습자: *"새 리뷰 동기화"* 또는 daily):

```
bin/sync-prs --repo <r> --owner woowacourse
  → 1. incremental collect since last_run.finished_at
  → 2. (자동) mission-patterns-build refresh
  → 3. (자동) anchors-build refresh (학습자 own threads 추가)
  → 4. (자동) cross-crew-build refresh (anchors × cohort 다시 매칭)
```

woowa-learning-system은 repo 준비, 학습 상태, RAG 검색, 코칭 context 생성을 단독으로 처리한다. 학습자 외울 명령은 0개다.

학습자가 *"오류"*, *"안 돼"* 표현 시 또는 정기 health check: `bin/doctor` (5/6+ healthy 확인).

학습자가 직접 명령 외울 필요 0. GitHub 연동 작업에는 `gh` CLI 설치 + `gh auth login`만 필요하다.

### 4.8 Phase V coaching context auto-call (2026-05-26)

| 학습자 의도 / 발화 | AI 자동 호출 |
|---|---|
| `coach-run` schema가 필요한 외부 도구 호출 시 | `bin/coach-run --repo <r> --prompt <P> --silent` |
| *"내 PR 분석"*, *"PR #N 검토"* | `bin/my-pr --repo <r> [--pr-number N] --silent` |
| *"다음 뭐 하지?"*, *"learning profile 보여줘"* | `bin/next-action` |
| *"<concept> 자세히"* | `bin/topic --concept <cid> [--repo <r>]` |
| *"멘토 <login> 어떤 사람?"* | `bin/reviewer --repo <r> --reviewer-login <l>` |
| *"PR A vs PR B 비교"* | `bin/compare --repo <r> --pr-a A --pr-b B` |
| *"리뷰 응답 도와줘"* | `bin/compose-response --repo <r> --thread-id <id>` |
| *"미션 파일 ↔ concept 매핑 보여줘"* | `bin/mission-map --repo <r> [--summary]` |
| 학습자 prompt가 모호 / corpus 어휘와 거리 멀 때 | `bin/rag-rewrite-prepare --mode hyde\|decompose\|normalize --query <Q>` |
| router confidence <0.7 | `bin/rag-route-fallback --prompt <P> [--repo <r>]` |
| 깊은 concept 학습 시 | `bin/chunk-context-prepare --concept-id <cid> --out-dir <D>` |

### 4.9 Phase W mining/analytics auto-call (시스템 개선 cycle, 2026-05-26)

대부분 Mode B (development) — AI/maintainer가 시스템 개선 cycle에서 사용:

| Wrapper | 용도 / Trigger |
|---|---|
| `bin/feedback-mine` | feedback.jsonl 누적 분석 (helpful vs not_helpful) |
| `bin/response-quality-mine` | 답변 quality flag 분포 + citation drift |
| `bin/routing-analyze` | 매주 router_mode dispatch 통계 |
| `bin/learning-turn-audit --last 50` | 매 일 turn integrity 확인 |
| `bin/learning-path-graph-audit` | corpus PR 후 broken edges / inversions check |
| `bin/reclassify-history --last 1000` | router rule 변경 후 dispatch drift |
| `bin/cohort-eval --cohort-file C.json` | release 전 50-q cohort 정확도 |
| `bin/cohort-compare --control A --candidate B --fail-on-drift` | A/B gate |
| `bin/golden verify` | CI per-commit golden top-1 |
| `bin/rag-eval` | F1 RAG quality regression (Phase K) |
| `bin/router-generalization-eval` | 20-fixture router accuracy |
| `bin/learner-log-rag-eval --limit 50` | 실제 history replay drift |

### 4.10 Phase X maintenance + sub-commands (2026-05-26)

| 학습자 발화 / Trigger | AI 자동 호출 |
|---|---|
| 새 index archive 패키징 | `bin/index-pack --force && bin/index-pack --verify-only` |
| 새 release tag publish 후 | `bin/sync-index-metadata --tag paradigm-v2-index-v1.0.2` |
| drill answer 채점 미리보기 | `bin/drill-grade-prepare --pending-file P [--answer A]` |
| *"도움 됐어"*, *"안 맞아"* | `bin/learn-feedback --signal helpful\|not_helpful\|unclear --silent` |
| pending self_assessment + 학습자가 점수 회신 | `bin/learn-self-assess --trigger-session-id <id> --score N --silent` |
| drill 명시 발화 (*"확인 질문 풀자"*) | `bin/learn-drill {offer\|answer\|status\|cancel}` |
| *"내 프로필 보여줘"*, *"학습 상태"* | `bin/learner-profile show` |
| profile 재계산 (10 turn 마다) | `bin/learner-profile recompute --silent` |
| per-repo 선호 set | `bin/set-profile --repo <r> --field <f> --value <v>` |
| global+repo profile 확인 | `bin/show-profile [--repo <r>]` |
| 멘토 패턴 분석 | `bin/reviewer-profile --repo <r> --reviewer-login <l>` |
| RunPod 인덱스 재빌드 (maintainer 전용) | `bin/rag-remote-build --r-phase r1` |

### 4.6 Phase T learner-automation auto-call (2026-05-25 신설)

학습자 의도에 따라 다음 wrapper 자동 호출:

| 학습자 발화 / 행동 | AI 자동 호출 |
|---|---|
| *"내 PR 흐름"*, *"반복 멘토 지적"*, *"회고"* | `bin/learn-pr-retro --repo <r> --learner-login <l> --silent` |
| Write/Edit a `missions/<r>/**/*.java` file | `bin/learn-record-code --file-path <p> --summary "<1줄>" --lines-added N --lines-removed M [--linked-test C.M] --silent` |
| 학습자가 `./gradlew test` 결과 mention | `bin/learn-test --path missions/<r>/build/test-results/test/ --repo <r> --silent` |
| 매 coach turn 답변 직후 (필수) | hook 가능 시 `bin/capture-response`; hook 불가 시 `bin/learn-response-quality --source-event-id <id> --response-path <answer.md>` 또는 `--response-file -` |
| 학습자가 미션 repo onboarded 후 첫 coaching 진입 시 | `bin/assess-learner-state --repo <r> --path missions/<r> --learner-login <l> --silent` |
| 매 10 turn마다 OR *"내 상태"*, *"learning profile"* 발화 | `bin/profile-recompute --silent` |
| *"세션 시작"*, *"학습 시작"* | `bin/session-start --repo <r> --prompt "<intent>" --path missions/<r> --silent` |

모두 default `--silent` — stdout 노이즈 없음, AI session이 결과를 한국어로 narrate.

---

## 5. Mode B 행동 contract (시스템 개발)

### 5.1 모든 작업
- `WOOWA_SESSION_MODE=development` 환경 변수 set 후 후속 명령.
- 변경은 commit 기반 reproducible. 측정 결과 `reports/` 폴더에 저장.
- 회귀 검증: `pytest tests/ -q` 모든 변경 후. 현재 517 passed 유지.

### 5.2 측정 명령
```bash
# F1 RAG quality 회귀
WOOWA_SESSION_MODE=development python3 tests/benchmarks/rag_quality_regression.py

# 9 plan gate 측정
WOOWA_SESSION_MODE=development python3 tests/benchmarks/gate_measurements.py

# 시나리오 측정
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios_phase_n.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/deep_scenarios_phase_p.py

# Full scenario comparison
WOOWA_SESSION_MODE=development python3 tests/benchmarks/full_scenario_comparison.py
```

### 5.3 4 개발 원칙 (commit 자체점검)

1. **Hypothesis-Driven Autonomy** — 모호 시 가설+측정+직접 판단. ask는 scope/policy 결정에만.
2. **Simplicity First** — 최소 코드. 추측 abstraction 금지.
3. **Surgical Changes** — 요청 범위만 수정. adjacent code touch 금지.
4. **Goal-Driven Execution** — verifiable success criteria 명시 후 loop.

상세는 [`docs/architecture.md`](docs/architecture.md) §"Development principles".

---

## 6. 추천 모델

- **Default: Claude Sonnet 4.6** (`claude-sonnet-4-6`) — 이 워크로드는 `bin/ask` 결과 해석이라 Opus 추론력 낭비.
- **Opus 4.7로 escalate 조건**: 학습자가 명시적으로 아키텍처 deep-dive 요청 OR Mode B에서 plan-mode multi-step 추론.
- **첫 turn 모델 체크 (필수)**: 세션 첫 응답에서 현재 모델을 한국어로 한 줄 보고. Opus면 *"Sonnet 4.6 권장"* 안내 (Pro 쿼터 1.7× 느림). 학습자 결정 받은 후 진행.

---

## 7. 새 진입 시 첫 1줄

세션 시작 → 학습자 첫 발화를 받기 전에 다음 자체점검:
1. `git rev-parse HEAD`로 현재 commit 확인.
2. `state/rag-daemon.sock` 존재 + `bin/rag-daemon ping` ok 확인.
3. 아니면 First-Run Protocol step 1-6 자동 시작.

준비 완료 시 한국어 한 줄: *"세팅 끝났어. 뭘 학습하고 싶어?"*

---

## 8. 참고 문서

- [`AGENTS.md`](AGENTS.md) — Codex/Gemini/일반 AI용 동등 문서
- [`docs/architecture.md`](docs/architecture.md) — 7 mode router + Bloom + F10/F11 + multi-agent
- [`docs/onboarding.md`](docs/onboarding.md) — First-Run Protocol 상세 + 트러블슈팅
- [`docs/bin-reference.md`](docs/bin-reference.md) — 주요 bin entry usage
- [`docs/learning-flow.md`](docs/learning-flow.md) — 학습자 일상 시나리오
- [`docs/artifact-catalog.md`](docs/artifact-catalog.md) — `state/` `reports/` `corpus/` 구조
- [`docs/testing-guide.md`](docs/testing-guide.md) — release acceptance와 benchmark 재현
- [`docs/verification-results.md`](docs/verification-results.md) — 모든 측정 결과 인덱스
