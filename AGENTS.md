# AGENTS.md — Codex / OpenAI / Gemini / general AI session instructions

이 문서는 Claude 외 AI 세션(Codex CLI, ChatGPT Plus/Pro, Gemini CLI, OpenAI SDK 등)이 이 repo에 진입했을 때의 행동 contract다. [`CLAUDE.md`](CLAUDE.md)와 동일한 system contract를 따른다. 톤만 OpenAI/Gemini 환경에 맞춰 조정.

## 0. Project Knowledge Base (init-deep)

**Generated:** 2026-06-12
**Commit:** `b292f17`
**Branch:** `main`

### Overview

Woowa mission learning system: Python flat-layout RAG daemon + CLI wrappers + learner telemetry + Java mission workspace. Runtime path is `bin/ask` → `core/daemon.py` → `core/router.py` / `core/coach.py`.

### Structure

```text
woowa-learning-system/
├── bin/        # 81+ user/operator commands; thin wrappers over core/rag/mission
├── core/       # daemon, routing, prompt, state, telemetry, learner profile
├── rag/        # corpus load, BGE-M3 encode, Lance index, retrieval/fusion
├── mission/    # system-side mission/PR analytics; writes state/repos artifacts
├── missions/   # learner-owned nested Java/Gradle repos; do not auto-edit
├── corpus/     # editable concept JSON/schema; graph/index are derived
├── state/      # gitignored runtime artifacts, daemon sock/pid, telemetry, indexes
├── tests/      # pytest unit suite plus standalone benchmark gates
├── scripts/    # collection, migration, mining, RunPod remote build helpers
└── reports/    # generated benchmark/analysis outputs plus a few generator scripts
```

### Where To Look

| Task | Location | Notes |
|---|---|---|
| First-run setup | `bin/bootstrap`, `bin/setup`, `bin/index-fetch`, `bin/rag-daemon` | learner should not remember commands |
| Ask/runtime flow | `bin/ask`, `core/daemon.py`, `core/router.py`, `core/lazy_loader.py`, `core/coach.py` | newline-delimited AF_UNIX socket |
| Retrieval/index | `rag/encoder.py`, `rag/index.py`, `rag/search.py`, `corpus/` | local build maintainer-only |
| Learner telemetry | `core/response_capture.py`, `core/response_quality.py`, `core/state.py`, `state/learner/` | failures must not block answers |
| PR/review modes | `mission/*.py`, `core/pr_threads.py`, `core/pr_retro.py`, `anchors/` | live GitHub vs offline archive split |
| Commands | `docs/bin-reference.md`, `bin/` | wrappers are the public API |
| Verification | `docs/testing-guide.md`, `tests/`, `tests/benchmarks/release_acceptance.py` | use development session mode |

### Code Map

| Symbol/File | Type | Location | Role |
|---|---|---|---|
| `serve()` / `main()` | runtime | `core/daemon.py` | daemon lifecycle and request handling |
| `RouteDecision` | model | `core/router.py` | selected mode, artifacts, token budget |
| `compose()` | prompt | `core/coach.py` | final coach prompt and citation hints |
| `load_artifacts()` | loader | `core/lazy_loader.py` | selective mode artifact hydration |
| `encode_query()` | retrieval | `rag/encoder.py` | BGE-M3 embedding path |
| `search()` | retrieval | `rag/search.py` | exact/fallback/personalized search |
| `MissionPattern` | analytics | `mission/extract.py` | learner Java concept extraction |
| `ReviewAnchor` | analytics | `anchors/extract.py` | review-thread anchor extraction |

### Conventions

- Flat package layout; `pyproject.toml` explicitly includes `core*`, `rag*`, `scripts*`, `mission*`, `anchors*`, `curation*`.
- Session mode is explicit: system work sets `WOOWA_SESSION_MODE=development`; learning remains conservative default.
- Learner-facing progress is Korean and short; command execution is automatic.
- Full-body response capture is preferred; summary-only is exceptional repair fallback.
- Benchmarks are direct Python scripts, not pytest-collected tests.

### Anti-Patterns (This Project)

- Do not run `bin/corpus-build` on learner machines; fetch release indexes.
- Do not silently abort; report cause + next action.
- Do not auto-edit or auto-clone learner mission repos.
- Do not post PR replies or review comments automatically.
- Do not rewrite `--raw-utterance`.
- Do not manually choose `tool_only` / `tier_0_fallback`.
- Do not eagerly load all lazy artifacts.
- Do not let telemetry, drift checks, or capture failures block the learner answer.

### Commands

```bash
bin/setup --dev
bin/bootstrap
python3 -m pytest tests/ -q
python3 tests/benchmarks/release_acceptance.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/rag_quality_regression.py
WOOWA_SESSION_MODE=development python3 tests/benchmarks/gate_measurements.py
```

### Child Knowledge Bases

- `bin/AGENTS.md` — CLI wrapper invariants.
- `core/AGENTS.md` — runtime/state/telemetry contracts.
- `mission/AGENTS.md` — system-side mission analytics.
- `missions/AGENTS.md` — learner-owned Java repo boundary.
- `rag/AGENTS.md` — retrieval/index conventions.
- `tests/AGENTS.md` — pytest vs benchmark split.
- `corpus/AGENTS.md` — editable corpus vs derived artifacts.

## 1. 무엇이 학습자에게 보이고 무엇이 자동인가

학습자가 외울 명령은 **0개**. 학습자는 한국어로 의도만 표현. AI 세션이 모든 명령 자동 실행:
- `bin/setup` (`.venv` 생성 후 `pip install -e .`)
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
bin/setup          # 학습자 (Mode A)
bin/setup --dev    # 시스템 개발 (Mode B, pytest+pandas+pylance 포함)
```
`bin/setup`은 repo-local `.venv`를 만들고 그 안에 `pip install -e .`를 실행한다. macOS Homebrew / Debian 시스템 Python은 PEP 668 (externally-managed)이라 시스템 직접 설치가 거부되므로 venv가 마찰 없는 경로다 (`--break-system-packages` 불필요). 설치 후 `bin/` 명령들은 `core/_venv.py` 가드로 `.venv`를 자동 사용하고, `.venv`가 없으면 가드는 no-op.
deps: `sentence-transformers`, `transformers`, `lancedb`, `numpy`, `pyarrow`, `jsonschema`, `torch`. dev extra는 `pytest`, `pandas`, `pylance`를 추가한다. (BGE-M3는 transformers + sentence-transformers로 로드 — `FlagEmbedding`은 RunPod 빌드 전용, 학습자 dep 아님.)

### Step 2. HF 모델 캐시
- 첫 daemon 시작 때 `BAAI/bge-m3` (~3GB) 자동 fetch. offline = `export HF_HUB_OFFLINE=1`.

### Step 3. Lance 인덱스 (release fetch only)
```bash
bin/index-fetch
```
GitHub Releases `DongKey777/woowa-learning-system` → `paradigm-v2-index-v1.0.5` (release별 약 13-19MB, SHA256 검증) → `state/index/`. 소요 ~15초.

**기존 학습자 재진입(git pull 후)**: `state/index/`가 있으면 무인자 `bin/index-fetch`는 건너뛴다. 새 release 자동 반영은 `bin/index-fetch --auto-upgrade` — 설치된 `manifest.release_tag`가 최신 tag보다 낡았을 때만 재fetch, 이미 최신이거나 tag 없는 로컬 빌드면 no-op. `bin/bootstrap`이 이 플래그를 넘기고 인덱스가 교체된 경우에만 daemon 재시작.

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

학습(Mode A) vs 시스템 개발(Mode B). 같은 세션에서 두 흐름 섞이면 personalization 오염.

- **Mode A** (default): 미션 코딩 / 개념 질문 / drill / coaching
- **Mode B**: corpus·core·bin·tests·docs 변경. `WOOWA_SESSION_MODE=development` set 후 명령 (또는 개별 `bin/ask`에 `--session-mode development` 명시).

**모드 결정 = 세션의 명시 선언이지 코드 추측이 아니다.** 세션이 작업 의도를 읽고 `bin/ask --session-mode learning|development`로 모드를 직접 선언한다(provenance `explicit`). 명시가 없으면 daemon은 `WOOWA_SESSION_MODE` env로 라벨하고(`env`), 그것도 없으면 보수적으로 `learning`(`default`)으로 둔다 — 이 출처가 이벤트의 `mode_source`에 남는다. 아래 file_path/내용 신호는 세션이 `--session-mode`를 **고를 때 쓰는 판단 기준**이지 코드 분류기가 아니다(과거 문서가 설명하던 자동 분류기는 코드에 존재하지 않는다): `core/`·`corpus/`·`docs/`·`bin/`·`tests/`·`scripts/`·root `.md` 편집/측정/빌드 → `development`; `missions/`·`src/main/` 미션 코딩·개념 질문 → `learning`; 모호하면 `learning`(보수적, 학습자의 정당한 도구 질문을 dev로 오분류 X).

---

## 4. Mode A 행동 contract

### 4.1 금지
- 학습자 미션 코드 자동 수정
- 학습자 mission repo 자동 git clone
- 외부 paid LLM API 호출 (예: OpenAI API key로 별도 호출). 모든 추론은 현재 ChatGPT Plus/Pro 구독 세션 안에서만.
- **PR 리뷰 답글 시스템 생성 금지 + 자동 post/submit 금지** — `pr-thread-status`·`learn-pr-retro --live`는 read-only 진단(GitHub GET만, 상태 정합·델타만). 답글 문구는 **시스템 도구로 만들지 않는다** — 세션이 대화체 초안만 직접 작성하고(리뷰어 말 반복 금지·인정→내 관점→되묻기·간결, 학습자 추론 날조 금지), 실제 게시/리뷰 제출은 학습자가 직접 한다(스레드별 명시 확인 없이는 `gh` post 호출 금지).

### 4.2 매 turn 자동
1. 모드 선택 — 학습자 발화 의미를 읽고 §4.2.2 카탈로그에서 맞는 라우팅 모드를 고른다(`--mode`).
2. `bin/ask` 자동 호출 — 학습 turn은 `bin/ask "<재작성한 검색 쿼리>" --raw-utterance "<학습자 원문 발화>" --session-mode learning --mode <mode> [--repo <repo>]` 형태. positional = 기술어휘로 재작성한 검색 쿼리, `--raw-utterance` = 학습자 원문 발화 그대로(요약·재작성 X, raw→rewritten 쌍 축적), `--session-mode learning` = 학습 세션 명시(provenance `explicit`; 생략 시 env/`default`). `--mode`는 라우팅 모드 오버라이드(세션 모드와 별개) — 확신 없으면 생략 → 키워드 router(`detect_mode`) fallback.
3. 받은 prompt markdown 그대로 → 학습자에게 한국어 답변
4. 첫 줄 header: `[Mode: <router_mode>]`
5. 답변 끝 `참고:` 블록 (최대 3 concept_id)

### 4.2.1 답변 본문 수집 규칙
- UX가 우선이다. 답변 본문 수집 실패가 학습 답변을 막으면 안 된다.
- Claude/Codex/Gemini hook이 가능한 환경은 `bin/capture-response`가 최종 답변을 자동 수집한다.
- `bin/ask`는 **genuine 학습 turn(learning 모드 + `--raw-utterance`)마다** pending capture를 만들고(raw 없는 bare 프로브·dev 호출은 안 만듦, `daemon._is_capturable_learning_turn`), hook은 가장 최근 pending에 최종 답변을 연결한다. **15분(`STALE_PENDING_AGE_S`)을 넘긴 pending은 orphan으로 자동 만료**(`expire_stale_pending_captures`)되어 뒤늦은 무관 답변을 흡수하지 못한다 — 이 둘이 dev narration이 학습 pending에 잘못 붙던 capture-attribution 오염을 막는다.
- hook 수집 실패 시 `state/learner/capture-repair-queue.jsonl`에 남기고, 학습자에게는 짧게만 안내한다: *"학습 기록 저장은 나중에 자동 보정할게."*
- `# response_quality_hint.command_template` 수동 실행은 hook이 없는 환경의 fallback이다.
- 수동 full-body fallback은 `full_body_path_template`의 `--response-path <answer.md>`를 우선 사용한다.
- `--response-file -` stdin은 universal fallback이다. path capture가 불가능하면 세션 토큰 비용이 있더라도 본문 보존을 위해 사용한다.
- 요약본, 축약본, paraphrase, 일부 excerpt를 full body처럼 넣지 않는다.
- summary-only는 full-body capture가 정말 불가능한 예외 상황에서만 사용한다.
- summary-only에서는 본문 인용을 검증할 수 없으므로 `declared_citation_unverified`가 남는다.
- 정상 full-body 수집 시 `response-quality.jsonl.response_excerpt`에는 redacted prefix(최대 5000자), `response_body_path`에는 redacted full body 파일 경로가 저장된다.
- full body 파일은 redacted content hash 기반으로 dedupe 저장된다. 같은 본문을 여러 turn에서 수집해도 파일은 한 번만 쓴다.
- `bin/learn-response-quality` fallback이 full body를 저장하면 같은 `source_event_id`의 pending capture도 즉시 `captured`로 갱신한다. 오래 남은 pending은 `bin/capture-repair --sync-pending`으로 `response-quality.jsonl` 기준 재동기화한다.

### 4.2.2 모드 카탈로그 (AI가 `--mode`로 직접 선택)

키워드 router는 학습자가 특정 단어를 그대로 쳤을 때만 모드를 맞춘다(paraphrase recall 낮음). AI 세션이 발화 의미를 읽고 모드를 골라 `--mode`로 넘기는 게 1차 경로, 키워드 router는 deterministic fallback. 모드 목록과 선택 기준은 CLAUDE.md §4.2.2와 동일하다 — `cs_qa` / `coaching` / `drill` / `self_assess` / `retro` / `pr_diff_evolution`(코드 변화·리뷰 반영) / `cross_mission`(미션 간 개념·반복 실수) / `memory_review`(사각지대·복습) / `pr_review`(받은 리뷰) / `reviewer_profile`(멘토 성향) / `learning_path`(다음 학습) / `pr_meta`(PR 품질·크기) / `thread_recon`(스레드 복원) / `temporal`(시간축) / `meta_analytics`(학습 메타) / `cohort`(동기 비교 — 정량 위치) / `peer_compare`(동기 코드 정성 비교 — 파일 diff + 동기가 받은 멘토 지적) / `predict`(리뷰 미리 보기) / `f11_anchor`(cross-crew). `tool_only`/`tier_0_fallback`은 직접 고르지 않는다.

### 4.2.3 멀티 인텐트 분해 (W16/P1-15)

발화가 2개 이상 독립 주제면 **주제별 `bin/ask`를 N회 따로 호출**한다(쿼리 1개→벡터 1개, `rag/search.py` `q_vec = encode_fn(query)`). 한 문자열에 여러 주제를 섞으면 dense 평균화로 어휘 센 주제가 벡터를 점령하고 나머지 sub-intent gold가 top-5 밖 탈락한다(실측 per-sub-intent recall@5 53%→분해 100%, +47pp). `--reformulated-query` 단일 호출도 `retrieval_query`로 합쳐져 한 벡터가 되므로 이 희석을 못 막는다 — 1순위가 될 수 없다. `--raw-utterance`를 첫 sub-ask에만 넘기므로 daemon은 첫 호출만 pending capture를 만들고 나머지 sub-ask(raw_utterance 없음)는 안 만든다(`daemon._is_capturable_learning_turn` 게이팅) — N-pending 오염도 capture-repair도 없다. N회 결과는 한 답변에 주제별 섹션으로 합쳐 모든 sub-intent를 corpus 근거로 커버(주제 간 근거 섞기 금지). `--raw-utterance`는 첫 호출 한 번에만(중복 = raw→rewritten 쌍 오염). **False-positive 가드**: 번호/옵션 나열 단일주제(락 전략 3개, DI 3가지)는 분해 금지 — 정답 개념이 다른 클러스터에 흩어지는 진짜 2+ 주제만 분해, 모호하면 단일 검색(보수적 안전). 개념 부재 기인 미스는 분해로 못 고치며 코퍼스 과제다.

### 4.3 학습자 코드 작성/수정 시
다음 둘 중 하나면 `bin/learn-record-code --file-path <p> --summary "<1줄>" --lines-added N --lines-removed M --silent` 자동 호출:
1. AI가 학습자 미션 파일 실제 수정/생성 (예: Codex apply diff)
2. 학습자가 명시적으로 file path 지목해 검토 요청

`missions/<repo>/...` 경로에서는 repo를 자동 추론한다. "코드 어때?" 단독 (path 불명확)은 트리거 X.

### 4.4 Drill / Self-assessment
- daemon 응답에 `drill_offer` artifact 포함 시 → question 학습자에게 surface
- 답변 받으면 `bin/learn-event --event-type drill_answer --answer "<학습자 원문 답변>"` 자동 호출 — 자유텍스트 `--answer`가 pending drill을 4-dim 자동 채점(score_pending_answer). 결과 한국어로 보고. `--drill-score`(수동)는 자동채점 우회하니 금지
- self-assessment는 `pending_self_assessment` trigger 있을 때만 인정. random *"DI 8점"* (no pending) → 거절 + cs_qa fallback

### 4.5 톤
- 한국어 자연스럽게 (*-습니다* / *-야* 선호에 맞춰)
- AI-stylized 어휘 회피 (계약/깨뜨리다/부담/같은 결/비대칭)
- 답변 끝 요약 1-2 문장

### 4.6 Phase T learner-automation auto-call (2026-05-25 신설)

**라이브 vs 오프라인 (진행 중 리뷰 사이클은 라이브 우선)**: `bin/pr-thread-status`·`learn-pr-retro --live`는 매 호출 GitHub를 fresh로 쿼리해 **멘토 원댓글 ∖ (제출답글 ∪ 본인 PENDING 초안)** 으로 미답변을 정합한다(GraphQL로 resolved/outdated/decision 오버레이 + 직전 대비 델타). `my-pr`/무플래그 `learn-pr-retro`는 `sync-prs` 시점 SQLite라 stale + submitted-only(본인 pending 초안 안 보임). → **진행 중 사이클 = 라이브 도구로 현재 상태부터**, 무플래그 `learn-pr-retro` = 머지 후 회고. 라이브 답변/추천 직전마다 캐시 신뢰 금지하고 재쿼리한다. 답글 문구 생성은 시스템 기능이 아니다(§4.1) — 세션이 미답변 스레드를 보고 직접 초안을 쓴다.

| 학습자 발화 / 행동 | 자동 호출 |
|---|---|
| *"리뷰 스레드 상태"*, *"미답변 스레드"*, *"안 단 리뷰 어디"*, *"리뷰 사이클 상태"* | `bin/pr-thread-status --repo <r> --pr <N> --silent` (라이브 정합) |
| *"리뷰 응답 도와줘"* (진행 중) | `bin/pr-thread-status --repo <r> --pr <N> --silent`로 미답변 스레드(`diff_hunk`·멘토 원문 포함) 확인 후 **세션이 직접** 대화체 초안 작성 (답글 문구 시스템 생성 X) |
| *"내 PR 흐름"*, *"회고"* (진행 중 사이클) | `bin/learn-pr-retro --repo <r> --live` (stale "미해결"을 라이브 status로 정정) |
| *"내 PR 흐름"*, *"반복 멘토 지적"*, *"회고"* (머지 후) | `bin/learn-pr-retro --repo <r> --learner-login <l> --silent` |
| 학습자 미션 Java 파일 Write/Edit | `bin/learn-record-code --file-path <p> --summary "<1줄>" --lines-added N --lines-removed M --silent` (`missions/<r>/...` 경로에서 repo 자동 추론) |
| `./gradlew test` 결과 mention | `bin/learn-test --path missions/<r>/build/test-results/test/ --repo <r> --silent` |
| 매 coach turn 답변 직후 | hook 가능 시 `bin/capture-response`; hook 불가 시 `bin/learn-response-quality --source-event-id <id> --response-path <answer.md>` 또는 `--response-file -` |
| 미션 repo onboarded 후 첫 진입 | `bin/assess-learner-state --repo <r> --path missions/<r> --silent` |
| 10 turn 마다 OR *"내 상태"* | `bin/profile-recompute --silent` |
| *"세션 시작"*, *"학습 시작"* | `bin/session-start --repo <r> --prompt "<intent>" --path missions/<r> --silent` |
| 세션이 **판단**상 학습자가 특정 개념을 *아직 잘 모른다/처음/기초부터*로 드러낼 때 (W7 — 키워드 매칭 아닌 의미 판단) | `bin/learn-beginner-flag --concept <id> --silent` — 그 concept를 `must_skip_explanations_of`에서 제외할 뿐 아니라 retrieval 강등(`_mastered_like`)에서도 빼서 항상 표면화+재설명(mastery 과승급 자기신고 override). 확실히 익혔으면 `--clear` |

### 4.7 Phase U-X auto-call (2026-05-26 신설 45 wrappers)

CLAUDE.md §4.7-4.10과 동일 contract. 핵심 요약:

- **Phase U (10 onboarding)**: `bin/onboard-repo`, `bin/bootstrap-repo`, `bin/sync-prs`, `bin/archive-status`, `bin/repo-readiness`, `bin/doctor`, `bin/validate-state`, `bin/registry-audit`, `bin/list-repos`, `bin/bootstrap`. woowa-learning-system 단독으로 repo 준비와 상태 검증을 수행한다.
- **Phase V (11 coaching context)**: `bin/coach-run`, `bin/coach/my-pr/next-action/topic/reviewer/compare`, `bin/mission-map`, `bin/rag-rewrite-prepare/route-fallback/chunk-context-prepare`.
- **Phase W (12 mining/analytics, Mode B)**: `bin/feedback-mine`, `bin/response-quality-mine`, `bin/routing-analyze`, `bin/learning-turn-audit`, `bin/learning-path-graph-audit`, `bin/reclassify-history`, `bin/cohort-eval/compare`, `bin/golden`, `bin/rag-eval`, `bin/router-generalization-eval`, `bin/learner-log-rag-eval`.
- **Phase X (11 maintenance + sub-commands)**: `bin/index-pack`, `bin/sync-index-metadata`, `bin/drill-grade-prepare`, `bin/learn-feedback/self-assess/drill`, `bin/learner-profile` (show/recompute/set/clear/redact), `bin/set-profile/show-profile`, `bin/reviewer-profile` (alias), `bin/rag-remote-build`.

위 Phase T-X 외에 **Mode feature builders 15개**(`anchors-build`, `learn-evidence-sync`, `pr-diff-evolution-build`, `learn-pr-meta-build`, `learn-pr-review-build`, `learn-predict-build`, `reviewer-profile-build`, `learn-temporal-build`, `learn-thread-recon-build`, `learn-cohort-build`, `peer-pr-build`, `learn-cross-mission-build`, `learn-learning-path-build`, `learn-memory-review-build`, `learn-meta-analytics-build` — §4.2.2 고급 모드 artifact 사전 빌드)와 Live PR review cycle `bin/pr-thread-status`(라이브 정합)가 더 있다. woowa-learning-system `bin/*` 합계: **84 entries**. 학습자 외울 명령 = 0개, AI 세션이 의도 감지로 자동 호출. 자세한 usage는 [`docs/bin-reference.md`](docs/bin-reference.md).

---

## 5. Mode B 행동 contract

### 5.1 매 작업
- `WOOWA_SESSION_MODE=development` set (또는 개별 `bin/ask`에 `--session-mode development` 명시 — provenance `explicit`). 둘 다 dev 텔레메트리로 라벨.
- commit 기반 reproducible
- 회귀 검증: `pytest tests/ -q` (현재 745 passed 유지)

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
3. ping ok면 인덱스 최신성도 확인: `bin/index-fetch --auto-upgrade` (설치 인덱스가 최신 release보다 낡았으면 자동 교체, 최신이면 no-op). 교체됐으면 `bin/rag-daemon stop` 후 `start-bg`로 재시작. (이 점검+재시작은 `bin/bootstrap` 한 번으로 처리 — idempotent.)
4. ping 실패면 First-Run Protocol step 1-6(`bin/bootstrap`) 자동 시작

준비 완료: *"세팅 끝났어. 뭘 학습하고 싶어?"*

---

## 8. 참고 문서

- [`CLAUDE.md`](CLAUDE.md) — Claude 세션용 동등 문서
- [`docs/architecture.md`](docs/architecture.md) — router (AI 세션 드리븐 + 키워드 fallback) + Bloom + F10/F11 + multi-agent
- [`docs/onboarding.md`](docs/onboarding.md) — First-Run Protocol 상세
- [`docs/bin-reference.md`](docs/bin-reference.md) — 주요 bin entry usage
- [`docs/learning-flow.md`](docs/learning-flow.md) — 학습자 일상 시나리오
- [`docs/artifact-catalog.md`](docs/artifact-catalog.md) — `state/` `reports/` `corpus/` 구조
- [`docs/testing-guide.md`](docs/testing-guide.md) — release acceptance와 benchmark 재현
- [`docs/verification-results.md`](docs/verification-results.md) — 모든 측정 결과 인덱스
