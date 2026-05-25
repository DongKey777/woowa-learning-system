# CLAUDE.md — Claude session instructions for woowa-learning-system

이 문서는 Claude 세션(Claude Code, Anthropic API, Claude Pro/Max 구독)이 이 repo에 진입했을 때의 행동 contract다. 학습자 진입 시나리오를 100% 반영한다.

## 1. 무엇이 학습자에게 들리고 무엇이 자동인가

학습자가 외울 명령은 **0개**. 학습자는 한국어로 의도만 표현. 모든 명령은 Claude 세션이 자동 실행:

- `pip install -e .`
- BGE-M3 모델 캐시 다운로드 (~3GB)
- Lance 인덱스 빌드 또는 release fetch
- `bin/rag-daemon start` 백그라운드
- `bin/ask "..."` 자체
- 에러 복구

학습자에게는 **한국어로 한 줄 진행 보고만** ("BGE-M3 다운로드 중…", "인덱스 빌드 완료").

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
pip install -e .
```
- 핵심: `sentence-transformers`, `FlagEmbedding` (BGE-M3), `lancedb`, `numpy`, `pyarrow`, `jsonschema`, `torch` (MPS on M-series).

### Step 2. HuggingFace 모델 캐시 warm-up
- 첫 daemon 시작 시 `BAAI/bge-m3` (~3GB) 자동 다운로드.
- offline 환경: `export HF_HUB_OFFLINE=1` 후 사전 캐시된 모델 사용.

### Step 3. Lance 인덱스 준비
두 경로 중 하나:

**(a) 사전 빌드된 release 다운로드** (권장, 빠름)
- GitHub Releases에서 `paradigm-v2-index-vX.Y.Z` artifact (~12MB) 다운로드 → `state/index/` 추출.
- 명령: 별도 release-fetch 스크립트 없을 시 `gh release download paradigm-v2-index-vX.Y.Z --pattern 'state-index.tar.zst' && tar -I zstd -xf state-index.tar.zst -C state/`.

**(b) 로컬 빌드** (release 없으면 fallback)
```bash
bin/corpus-build
```
- M4 16GB warm BGE-M3: 15-30분, peak RAM 4-6GB.
- 결과: `state/index/concept.lance/` (~12MB).

### Step 4. Daemon 백그라운드 시작
```bash
nohup bin/rag-daemon start > /tmp/daemon.log 2>&1 &
```
- 첫 시작 후 약 5-10초 prewarm (모델 메모리 load).
- 학습자 첫 query 전에 ready 확인: `grep -q ready /tmp/daemon.log`.

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

---

## 5. Mode B 행동 contract (시스템 개발)

### 5.1 모든 작업
- `WOOWA_SESSION_MODE=development` 환경 변수 set 후 후속 명령.
- 변경은 commit 기반 reproducible. 측정 결과 `reports/` 폴더에 저장.
- 회귀 검증: `pytest tests/ -q` 모든 변경 후. 213/213 통과 유지.

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

# Legacy 비교
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
- [`docs/bin-reference.md`](docs/bin-reference.md) — 10 bin entry usage
- [`docs/learning-flow.md`](docs/learning-flow.md) — 학습자 일상 시나리오
- [`docs/artifact-catalog.md`](docs/artifact-catalog.md) — `state/` `reports/` `corpus/` 구조
- [`docs/testing-guide.md`](docs/testing-guide.md) — 77 시나리오 재현
- [`docs/verification-results.md`](docs/verification-results.md) — 모든 측정 결과 인덱스
