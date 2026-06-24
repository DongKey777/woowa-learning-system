# bin/ reference — core entries + Phase T-X wrappers

| Entry | Mode | Purpose |
|---|---|---|
| [`bin/setup`](#binsetup) | A/B | `.venv` 생성 + `pip install -e .` (PEP 668 안전, `--dev`로 pytest+pandas+pylance 포함) |
| [`bin/ask`](#binask) | A | 학습자 자연어 query → daemon ask (학습자 외울 명령 X) |
| [`bin/rag-daemon`](#binrag-daemon) | A/B | BGE-M3 warm daemon start/stop/ping/status |
| [`bin/index-fetch`](#binindex-fetch) | A | GitHub Releases에서 pre-built Lance 인덱스 다운로드 |
| [`bin/learn-event`](#binlearn-event) | A | code_attempt/drill_answer/self_assess 등 이벤트 기록 |
| [`bin/mission-patterns-build`](#binmission-patterns-build) | A/B | F10 forward — 학습자 PR Java → mission_patterns.json |
| [`bin/cross-crew-build`](#bincross-crew-build) | A/B | F11 cross-crew parquet 사전 빌드 |
| [`bin/corpus-build`](#bincorpus-build) | B (maintainer only) | Lance dense 인덱스 빌드 — **학습자 기기 금지** |
| [`bin/corpus-curate`](#bincorpus-curate) | B | corpus JSON validation + lint |
| [`bin/graph-build`](#bingraph-build) | B | concept_graph.json 재빌드 (prereq 등) |
| [`bin/eval-compare`](#bineval-compare) | B | 두 eval 결과 비교 |
| [`bin/phase9-gate`](#binphase9-gate) | B | release gate 검증 |

---

## `bin/setup`

repo-local `.venv`를 만들고 그 안에서 `pip install -e .`를 실행하는 First-Run 의존성 부트스트랩.

```bash
bin/setup          # 런타임 deps (학습자 / Mode A)
bin/setup --dev    # + pytest/pandas/pylance (시스템 개발 / Mode B)
```

- macOS Homebrew / Debian 시스템 Python은 PEP 668 (externally-managed)이라 직접 `pip install`이 거부된다. venv가 이를 마찰 없이 우회한다 (`--break-system-packages` 불필요, 시스템 Python 오염 없음).
- 설치 후 `bin/` 명령들은 `core/_venv.py` 가드를 통해 `.venv`를 자동 사용한다. `.venv`가 없으면 가드는 no-op이라 시스템 Python에 deps가 이미 깔린 환경은 그대로 동작.
- 멱등(idempotent): 재실행 시 `.venv`를 재사용하고 in-place 업그레이드.
- `bin/bootstrap`이 First-Run 1단계에서 자동 호출 (deps가 이미 import되면 skip).

---

## `bin/ask`

학습자 query를 daemon에 보내고 markdown prompt 반환. AI 세션이 학습자 의도 받으면 자동 호출.

```bash
bin/ask "<prompt>" [--raw-utterance U] [--session-mode S] [--repo R] [--mode M] [--learner-id L] [--json-route] [--no-daemon] [--state-root PATH]
```

옵션:
- `--raw-utterance U`: 학습자 원문 발화(세션이 재작성하기 전). positional `<prompt>`은 검색에 넘어가는 재작성 쿼리이므로, raw→rewritten 쌍을 축적하려면 원문을 그대로(요약·재작성 X) 동반 전달한다. 이벤트 `payload.raw_utterance`에 기록, 미전달이면 `null`.
- `--session-mode S`: 세션 모드(`learning`|`development`) 명시 — provenance `explicit`로 기록. 생략 시 `WOOWA_SESSION_MODE` env(`env`)로, 그것도 없으면 보수적 `learning`(`default`)으로 라벨된다. 라우팅 `--mode`와는 별개의 축이다.
- `--repo R`: 학습자 mission repo 지정 (coaching/retro/F11 등 모드 트리거 가능)
- `--mode M`: AI 세션이 발화 의미를 읽고 고른 라우팅 모드를 명시. 키워드 검출보다 우선하고, 미지정·미인식이면 키워드 router로 fallback. 선택 가능한 모드는 [`CLAUDE.md`](../CLAUDE.md) §4.2.2 카탈로그 참조.
- `--learner-id L`: learner identifier (omitted이면 `state/learner/identity.json`에서 자동 해석)
- `--json-route`: stdout 첫 줄에 `# RouteDecision: {...}` 표기 (디버그)
- `--no-daemon`: daemon 우회, in-process pipeline (cold ~30s, debug/CI)
- `--state-root PATH`: 다른 state dir 지정 (테스트용)

출력: 학습자에게 보낼 prompt markdown. 첫 줄 `[Mode: <router_mode>]` 헤더, 본문 + `참고:` 인용.

예:
```bash
python3 bin/ask "Bean 의존성 주입 개념" --raw-utterance "Bean DI가 뭐야" --session-mode learning  # 학습 turn 표준형: 재작성 쿼리 + 원문 + 세션 모드
python3 bin/ask "Bean DI가 뭐야"                                          # 최소형 (키워드 → cs_qa)
python3 bin/ask "내 코드 어때" --repo spring-roomescape-member --mode coaching
python3 bin/ask "올리기 전에 어떤 리뷰 받을지 보고 싶어" --repo spring-roomescape-waiting --mode predict
python3 bin/ask "다른 크루는 어떻게" --repo spring-roomescape-member --mode f11_anchor
```

---

## `bin/rag-daemon`

```bash
bin/rag-daemon start    # foreground
bin/rag-daemon start-bg # detached background start + ready wait
bin/rag-daemon stop
bin/rag-daemon ping
bin/rag-daemon status   # JSON: pid/uptime/socket/health
```

- AF_UNIX socket: `state/rag-daemon.sock`
- pid file: `state/rag-daemon.pid`
- BGE-M3 + corpus + lazy_loader 메모리에 keep
- single-thread (single-learner 설계)
- 시작 후 ~5-10초 prewarm — `state/rag-daemon.log`에 `ready` 출력
- env `WOOWA_RAG_NO_DAEMON=1` 로 비활성 (in-process fallback)

권장 시작:
```bash
bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90
```

---

## `bin/learn-event`

학습자 활동 이벤트 기록. AI 세션이 학습자 코드 작성/drill/self-assess 감지 시 자동 호출.

```bash
bin/learn-event --event-type <type> [--concept-ids id1,id2] [--score 0.85] [--mode learning] [--silent]
```

event-type:
- `code_attempt` — 학습자 미션 파일 수정/생성. `--concept-ids` 필수 (mission_patterns에서 추출 가능)
- `drill_answer` — drill 답변. **`--answer "<원문>"`** (자유텍스트 → pending drill 4-dim 자동 채점, score_pending_answer). `--drill-concept`+`--drill-score`는 수동 점수 경로(자동채점 우회, 권장 X)
- `self_assessment` — 학습자 자기 평가 (pending_self_assessment trigger 있을 때만)
- `pr_merge` — PR merge 알림. `--concept-ids` 필수 (1.0 weight)
- `mentor_accept` — mentor 리뷰 thread resolved. `--concept-ids` 필수 (0.9 weight)

`--silent`: stdout 억제, history.jsonl + mastery_graph만 업데이트.

내부: `core/feedback.record_turn` → `core/mastery.promote` → Bloom level 진행.

---

## `bin/mission-patterns-build`

F10 forward 활성화. 학습자 own PR Java patch_text → annotation/method/exception/import 매핑 추출.

```bash
bin/mission-patterns-build --repo <repo-name>
```

source: `state/repos/<repo>/archive/prs.sqlite3` (woowa-learning-system 자체 archive. `bin/bootstrap-repo` / `bin/sync-prs`가 채우며, `--state-root` 로 다른 경로 지정 가능).

결과: `state/repos/<repo>/mission_patterns.json` (~50-200 patterns).

trigger: 학습자가 *"<repo> 분석"*, *"내 PR 패턴"* 같은 의도 표현 시 AI가 자동 호출. Idempotent (re-run 동일 결과 — Phase N5 검증).

---

## `bin/cross-crew-build`

F11 4-stage filter 사전 계산. 학습자 anchor × cross-crew review_comments → BGE-M3 cosine top-10 매칭.

```bash
bin/cross-crew-build --repo <repo-name>
```

소요: anchor 11개 = ~3분 (BGE-M3 encode), anchor 20개 = ~5분.

결과: `state/repos/<repo>/cross_crew_review_graph.parquet` (~30-50KB).

trigger: 학습자가 *"다른 크루는"*, *"정밀 비교"*, *"cross-crew"* 같은 의도 표현 시 AI가 자동 호출. Idempotent (Phase N6 검증).

---

## `bin/index-fetch`

GitHub Releases에서 pre-built Lance 인덱스 다운로드. SHA256 검증 후 `state/index/`로 추출.

```bash
bin/index-fetch [--tag paradigm-v2-index-v1.0.5] [--force] [--auto-upgrade] [--expected-sha256 <hex>]
```

옵션:
- `--tag <tag>`: 다른 release version 지정
- `--force`: 기존 `state/index/`를 덮어쓰기 (default는 존재 시 건너뜀)
- `--auto-upgrade`: 설치된 인덱스의 `manifest.release_tag`가 `--tag`(최신)보다 낡았으면 자동 재fetch. 이미 최신이면 no-op, tag 없는 로컬 빌드는 건드리지 않음(maintainer 보호). git pull로 새 release가 들어온 기존 학습자가 별도 명령 없이 최신화되는 경로 — `bin/bootstrap`이 이 플래그를 넘기고 인덱스가 바뀐 경우에만 daemon을 재시작.
- `--expected-sha256 <hex>`: 무결성 검증 override (default는 `KNOWN_SHA256[tag]` — known tag면 자동 검증, unknown tag면 경고 후 skip)
- `--pattern <name>`: asset 이름 override (default는 `<tag>.tar.zst`)

의존: `gh` CLI + GitHub 인증 (`gh auth login`)

학습자 onboarding의 Step 3에 해당. 소요 ~15초 (release별 약 13-19MB).

---

## `bin/corpus-build` (maintainer only)

🚫 **학습자 기기에서는 절대 실행 금지**. M4 15-30분 + 4-6GB peak RAM. 학습자는 `bin/index-fetch`만 사용.

Maintainer가 RunPod 또는 강한 머신에서 새 인덱스 빌드 시:
```bash
bin/corpus-build [--corpus-dir corpus/concepts] [--index-dir state/index]
```

- M4 16GB: 15-30분
- RunPod H100: ~5분
- 결과: `state/index/concepts.lance/` + `state/index/manifest.json`

Build 후 publish 흐름:
```bash
# index-pack은 state/index.tar.zst를 만든다. asset 이름은 반드시 <tag>.tar.zst 규칙을
# 따라야 index-fetch의 derived --pattern (<tag>.tar.zst)이 매칭된다.
bin/index-pack --force && bin/index-pack --verify-only   # archive_sha256 기록
cp state/index.tar.zst /tmp/paradigm-v2-index-v1.0.5
gh release create paradigm-v2-index-v1.0.5 /tmp/paradigm-v2-index-v1.0.5 \
    --title "woowa-learning-system Lance index v1.0.3" --notes "...SHA256..."
# 이후 학습자는 bin/index-fetch --tag paradigm-v2-index-v1.0.5 로 업데이트
```
**새 release마다 필수**: `bin/index-fetch`의 `KNOWN_SHA256`에 `{tag: archive_sha256}` 한 줄 추가 + `DEFAULT_TAG`를 새 tag로 bump. (`sync-index-metadata`는 문서/manifest만 갱신하고 index-fetch 상수는 건드리지 않는다.)

---

## `bin/index-pack` (maintainer only)

현재 `state/index/`를 self-contained release archive로 패키징하고 검증한다. archive 안에는 `concepts.lance/`, `manifest.json`, `corpus_snapshot.json`, `lexical_fusion_sidecar.json`가 모두 들어가야 한다.

```bash
bin/index-pack --force
bin/index-pack --verify-only
```

검증 항목:
- archive manifest의 `dense_corpus_sha256`가 현재 `state/index/manifest.json`과 동일
- archive manifest의 `full_corpus_sha256`가 현재 `state/index/manifest.json`과 동일
- runtime sidecar 2개가 archive에 포함됨

Y14 remote archive: `state/index.tar.zst`, **18.7MB**, SHA256 `d8da5782c6fdceeec34e541a30e511bf2f8d168c01dab4e47dfefcde641921dc`.

---

## `bin/corpus-curate`

corpus JSON schema validation + lint (`corpus/schemas/concept.schema.json`).

```bash
bin/corpus-curate [--strict]
```

- 모든 `corpus/concepts/**/*.json` 검사
- 누락 field / 잘못된 type / 깨진 relations 보고
- `--strict`: 1건이라도 위반 시 exit 1 (CI용)

---

## `bin/graph-build`

`corpus/concept_graph.json` 재빌드 (nodes + prerequisite edges + confusable_with edges).

```bash
bin/graph-build
```

- corpus JSON 전수 스캔
- prereq cycle detect (있으면 fail)
- 결과: `corpus/concept_graph.json` (~5MB, 3339 nodes / 6172 prereq edges)

trigger: corpus 신규 concept 추가 후 (Mode B).

---

## `bin/eval-compare`

두 eval 결과 JSON 비교 (control vs candidate).

```bash
bin/eval-compare --control reports/eval_a.json --candidate reports/eval_b.json [--fail-on-drift]
```

- top-1 / top-5 / latency drift 표 출력
- `--fail-on-drift`: 회귀 시 exit 1 (CI용)

---

## `bin/phase9-gate`

release gate 검증 (Phase 9 closing).

```bash
bin/phase9-gate
```

- 9 plan §verification gate 자동 측정 (F1/F2/F4/F5/F6/F8/F10/F11)
- 모두 통과 시 exit 0
- 사용 예: release 전 마지막 sanity

---

## 환경 변수 영향

| Variable | 영향받는 bin |
|---|---|
| `WOOWA_SESSION_MODE` | `learn-event` (이벤트 mode 태깅), `ask` (history append mode) |
| `HF_HUB_OFFLINE` | `corpus-build`, `cross-crew-build`, `rag-daemon` (BGE-M3 fetch 우회) |
| `WOOWA_RAG_NO_DAEMON` | `ask` (daemon 우회, in-process) |
| `WOOWA_RAG_DAEMON_IDLE_UNLOAD_SECS` | `rag-daemon` (N초 idle 후 모델 cache 해제, default 0 = off) |

---

## Phase T-X + capture wrappers (2026-05-25/26)

### Phase T — Learner automation (7)

| Wrapper | Purpose | Bench |
|---|---|---|
| `bin/learn-pr-retro --repo R [--include-bot] [--no-write]` | PR retrospective: recurring mentor signals + unresolved threads + timeline. 기본은 스냅샷을 `pr_retrospective/<r>.json`에 저장하고, `--no-write`(또는 read-only인 `--live`)면 저장 skip | p50 1.2ms |
| `bin/learn-record-code --file-path P --summary S` | code_attempt event + auto concept inference + repo inference from `missions/<r>/...` | p95 1.19ms (168× faster) |
| `bin/learn-test --path build/test-results/test/` | JUnit XML → test_result events, stable event_id (idempotent) | p50 3.6ms (333× faster) |
| `bin/learn-response-quality --source-event-id E --response-file -` / `--response-path answer.md` | full-response telemetry; path mode avoids transcript duplication, stdin is universal fallback, redacted full body + excerpt ≤5000 chars + summary/declared citation auto-extract; full body capture also marks matching pending capture `captured` | p95 0.16ms, drift 100%, PII 100% |
| `bin/capture-response --client claude\|codex\|gemini --hook-json -` | hook-first full body capture; joins latest pending `rag_ask`, non-blocking repair queue on failure | hook path |
| `bin/capture-repair --last N [--apply] [--sync-pending]` | replay repairable failed captures from `capture-repair-queue.jsonl`; with `--sync-pending`, reconcile stale pending-captures from `response-quality.jsonl` full-body rows | local |
| `bin/learning-data-clean --hard-delete-contamination [--apply]` | remove dummy source-id asks, orphan quality rows, partial ids, unreferenced body sidecars | local |
| `bin/assess-learner-state --repo R --path missions/<r>` | git + SQLite snapshot: head/working_copy/PRs/threads classified | p50 113ms (528× faster) |
| `bin/profile-recompute` | history → v3 profile.json (mastered/uncertain/calibration/recommendations) | 10K events ≤75ms |
| `bin/session-start --repo R --prompt P --path missions/<r>` | orchestrator: assess → recompute → daemon ask | cold 332ms, warm 4.2ms |

### Phase U — Onboarding / collection (10, G1 closure)

| Wrapper | Purpose |
|---|---|
| `bin/bootstrap` | First-Run system bootstrap (bin/setup → index-fetch → daemon) |
| `bin/bootstrap-repo --repo R --owner O` | Full GitHub PR archive collection via gh CLI |
| `bin/onboard-repo --repo R --owner O` | clone + bootstrap-repo + mission-patterns + cross-crew chain |
| `bin/list-repos [--json]` | List all `state/repos/*/` + per-repo capability flags |
| `bin/archive-status [--repo R]` | SQLite archive stats |
| `bin/sync-prs --repo R --owner O` | Incremental sync since last collection_run.finished_at |
| `bin/repo-readiness --repo R` | 4-check capability gate (archive/patterns/cross/state) |
| `bin/doctor [--repo R]` | 6-check system health (python/gh/index/bge-m3/daemon/state) |
| `bin/validate-state [--strict]` | JSON/SQLite schema validation across state/ |
| `bin/registry-audit [--repair]` | orphan / uninitialized / corrupt detection |

### Phase V — Coaching context (11)

| Wrapper | Purpose |
|---|---|
| `bin/coach-run --repo R --prompt P` | Coaching action snapshot — writes `state/repos/<r>/actions/coach-run.json` |
| `bin/coach`, `bin/my-pr`, `bin/next-action`, `bin/topic`, `bin/reviewer`, `bin/compare` | Thin coaching-mode wrappers over `bin/ask`/`bin/coach-run` |
| `bin/mission-map --repo R [--summary]` | File→concept_ids map (HEAD + archive patches) |
| `bin/rag-rewrite-prepare --mode hyde\|decompose\|normalize` | Query rewrite prompt templates |
| `bin/rag-route-fallback --prompt P` | Router internals + AI disambig for low-confidence |
| `bin/chunk-context-prepare --concept-id C [--out-dir D]` | Per-concept artifact bundle |

### Phase W — Mining / analytics (12)

| Wrapper | Purpose |
|---|---|
| `bin/feedback-mine` | `state/learner/feedback.jsonl` helpful/not_helpful distribution |
| `bin/response-quality-mine` | response-quality.jsonl flag + citation + capture method/dedupe + repair queue analysis |
| `bin/routing-analyze` | rag_ask router_mode + reason histogram |
| `bin/learning-turn-audit --last N [--require-full-body]` | Per-event integrity + response-quality/full-body join |
| `bin/learning-path-graph-audit` | concept_graph broken edges / cycles / level inversions |
| `bin/reclassify-history --last N` | Re-route history through current router (dry-run drift) |
| `bin/cohort-eval --cohort-file C.json` | N-query cohort latency + mode accuracy |
| `bin/cohort-compare --control A --candidate B [--fail-on-drift]` | A/B regression gate |
| `bin/golden update\|verify` | Golden fixture top-1 verify |
| `bin/rag-eval` | F1 RAG quality regression (wraps Phase K) |
| `bin/router-generalization-eval` | 20-fixture router accuracy |
| `bin/learner-log-rag-eval --limit N` | Historical prompt replay drift |

### Phase X — Maintenance + sub-commands (11)

| Wrapper | Purpose |
|---|---|
| `bin/sync-index-metadata --tag T` | release tag → manifest + docs |
| `bin/drill-grade-prepare --pending-file P` | Grading prompt for AI session |
| `bin/learn-feedback --signal helpful\|not_helpful\|unclear` | Explicit feedback signal |
| `bin/learn-self-assess --trigger-session-id S --score N` | Pending-trigger-guarded self-assess |
| `bin/learn-drill {offer\|answer\|status\|cancel}` | Drill cycle CLI |
| `bin/learner-profile {show\|recompute\|set\|clear\|redact}` | v3 profile admin |
| `bin/set-profile --repo R --field F --value V` | Per-repo profile preference |
| `bin/show-profile [--repo R]` | Pretty-print global + per-repo |
| `bin/reviewer-profile --repo R --reviewer-login L` | Alias for `bin/reviewer` |
| `bin/rag-remote-build [--dry-run]` | RunPod build wrapper (maintainer) |

### Mode feature builders (advanced-mode artifact precompute)

§4.2.2 카탈로그의 고급 모드들은 daemon ask 전에 per-repo / per-learner artifact를 사전 빌드해 둔다. 학습자가 해당 의도를 표현하거나 `sync-prs` 후 자동 호출(idempotent). source는 대부분 `state/repos/<r>/archive/prs.sqlite3` + git clone, 산출물은 모드 로더가 읽는다.

| Wrapper | Mode | 산출물 |
|---|---|---|
| `bin/anchors-build --repo R` | F11 input | `state/learner/review_anchors.json` |
| `bin/learn-evidence-sync --repo R` | Bloom mastery | mentor_accept/pr_merge evidence → `mastery_graph.sqlite` (idempotent dedup) |
| `bin/pr-diff-evolution-build --repo R` | `pr_diff_evolution` | `state/repos/<r>/pr_diff_evolution.json` |
| `bin/learn-pr-meta-build --repo R` | `pr_meta` | `state/repos/<r>/pr_meta.json` |
| `bin/learn-pr-review-build --repo R` | `pr_review` | `state/repos/<r>/pr_review.json` |
| `bin/learn-predict-build --repo R` | `predict` | `state/repos/<r>/predict.json` (C/F/H artifact fusion) |
| `bin/reviewer-profile-build --repo R` | `reviewer_profile` | `state/repos/<r>/reviewer_profile.json` |
| `bin/learn-temporal-build --repo R` | `temporal` | `state/repos/<r>/temporal.json` |
| `bin/learn-thread-recon-build --repo R` | `thread_recon` | `state/repos/<r>/thread_recon.json` |
| `bin/learn-cohort-build --repo R` | `cohort` | `state/repos/<r>/cohort.json` |
| `bin/peer-pr-build --repo R` | `peer_compare` | `state/repos/<r>/peer_pr.json` |
| `bin/learn-cross-mission-build` | `cross_mission` | `state/learner/cross_mission.json` |
| `bin/learn-learning-path-build` | `learning_path` | `state/learner/learning_path.json` |
| `bin/learn-memory-review-build` | `memory_review` | `state/learner/memory_review.json` |
| `bin/learn-meta-analytics-build` | `meta_analytics` | `state/learner/meta_analytics.json` |

### Live PR review cycle (2026-06-02)

진행 중인 리뷰 사이클 전용. 매 호출 GitHub를 fresh로 쿼리해 **멘토 원댓글 ∖ (제출답글 ∪ 본인 PENDING 초안)** 으로 미답변을 정합한다(REST가 spine, GraphQL이 resolved/outdated/`reviewDecision` 오버레이, 직전 호출 대비 델타). SQLite 아카이브(`my-pr`/무플래그 `learn-pr-retro`)는 `sync-prs` 시점 stale + submitted-only(본인 pending 초안 미수집)라 진행 중 판정엔 부적합 — 라이브 도구로 현재 상태부터 본다. 모두 read-only(post/submit 안 함). 답글 문구 생성은 시스템 기능이 아니다 — 세션이 `pr-thread-status`의 미답변 스레드(`diff_hunk`·멘토 원문 포함)를 보고 직접 초안을 쓴다.

| Wrapper | Purpose |
|---|---|
| `bin/pr-thread-status --repo R --pr N [--silent] [--no-snapshot]` | 라이브 3소스 정합 + GraphQL 상태 + 델타 + 라운드 타임라인. 미답변 스레드를 좌표(path/line/root body/diff_hunk)와 함께 surface. 스냅샷: `state/repos/<r>/pr_threads/<n>.json` |
| `bin/learn-pr-retro --repo R --live [--no-write]` | 아카이브 회고의 stale "unresolved"를 라이브 status(answered/resolved/outdated)로 정정. `--live`는 read-only라 스냅샷(`pr_retrospective/<r>.json`)을 mutate하지 않는다(`pr-thread-status --no-snapshot` parity) |

### Bench totals (Phase T-X)

| Phase | Wrappers | Bench result |
|---|---|---|
| T | 7 | 7/7 + 17/17 e2e integration checks |
| U | 10 | 10/10 + 11/11 collection unit |
| V | 11 | 9/9 |
| W | 12 | 12/12 |
| X | 11 | 11/11 |
| **합계** | **51** | **49/49 bench + 28/28 unit + release archive gate** |

위 51은 Phase T-X wrapper만 센 것이다. 여기에 상단 core entries + 위 "Mode feature builders" 15개 + Live PR review cycle의 `bin/pr-thread-status`(신규)까지 더한 **Total bin/\* in woowa-learning-system: 84 entries** (`find bin -maxdepth 1 -type f`). 학습자 외울 명령 = 0개.

Full regression via meta-runner: `python3 tests/benchmarks/phase_y_all_benches.py` → 14 benches. 13개는 무조건 PASS. 14번째 `phase_t_e2e_integration.py`는 7개 wrapper STEP은 항상 green이지만 S2가 **라이브 학습자 진척**(`experience_level==advanced` + mastered ≥ 5)을 스냅샷 assert하므로, 학습자가 그 마일스톤에 도달했을 때만 14/14다(코드 회귀 아님 — DongKey777 현재 0 mastered/9 proficient면 13/14).
