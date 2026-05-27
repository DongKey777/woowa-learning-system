# bin/ reference — core entries + Phase T-X wrappers

| Entry | Mode | Purpose |
|---|---|---|
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

## `bin/ask`

학습자 query를 daemon에 보내고 markdown prompt 반환. AI 세션이 학습자 의도 받으면 자동 호출.

```bash
bin/ask "<prompt>" [--repo R] [--learner-id L] [--json-route] [--no-daemon] [--state-root PATH]
```

옵션:
- `--repo R`: 학습자 mission repo 지정 (coaching/retro/F11 모드 트리거 가능)
- `--learner-id L`: learner identifier (default `default`)
- `--json-route`: stdout 첫 줄에 `# RouteDecision: {...}` 표기 (디버그)
- `--no-daemon`: daemon 우회, in-process pipeline (cold ~30s, debug/CI)
- `--state-root PATH`: 다른 state dir 지정 (테스트용)

출력: 학습자에게 보낼 prompt markdown. 첫 줄 `[Mode: <router_mode>]` 헤더, 본문 + `참고:` 인용.

예:
```bash
python3 bin/ask "Bean DI가 뭐야"                              # cs_qa
python3 bin/ask "내 코드 어때" --repo spring-roomescape-member  # coaching
python3 bin/ask "다른 크루는 어떻게" --repo spring-roomescape-member  # f11_anchor
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
- `drill_answer` — drill 답변. `--concept-ids` + `--score` (0..1) 필수
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
bin/index-fetch [--tag paradigm-v2-index-v1.0.1] [--force] [--expected-sha256 <hex>]
```

옵션:
- `--tag <tag>`: 다른 release version 지정
- `--force`: 기존 `state/index/`를 덮어쓰기 (default는 존재 시 건너뜀)
- `--expected-sha256 <hex>`: 무결성 검증 (default는 v1.0.0의 hash hardcoded)

의존: `gh` CLI + GitHub 인증 (`gh auth login`)

학습자 onboarding의 Step 3에 해당. 소요 ~15초 (release별 약 13-18MB).

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
bin/index-pack --archive /tmp/paradigm-v2-index-vX.Y.Z.tar.zst --force
bin/index-pack --archive /tmp/paradigm-v2-index-vX.Y.Z.tar.zst --verify-only
gh release create paradigm-v2-index-vX.Y.Z /tmp/paradigm-v2-index-vX.Y.Z.tar.zst \
    --title "paradigm-v2 Lance index vX.Y.Z" --notes "..."
# 이후 학습자는 bin/index-fetch --tag paradigm-v2-index-vX.Y.Z 로 업데이트
```

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

K18 local archive: `state/index.tar.zst`, **17.9MB**, SHA256 `24cc838c949078f767fd41afd7cc50fd865e2a4fb387b2dfcb6cc39ee793bec6`.

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
- 결과: `corpus/concept_graph.json` (~5MB, 3219 nodes / 5822 prereq edges)

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

## Phase T-X 51 신규 wrappers (2026-05-25/26)

### Phase T — Learner automation (7)

| Wrapper | Purpose | Bench |
|---|---|---|
| `bin/learn-pr-retro --repo R [--include-bot]` | PR retrospective: recurring mentor signals + unresolved threads + timeline | p50 1.2ms |
| `bin/learn-record-code --file-path P --summary S` | code_attempt event + auto concept inference | p95 1.19ms (168× faster) |
| `bin/learn-test --path build/test-results/test/` | JUnit XML → test_result events, stable event_id (idempotent) | p50 3.6ms (333× faster) |
| `bin/learn-response-quality --source-event-id E --response-file -` | telemetry + PII redaction + citation drift detect | p95 0.16ms, drift 100%, PII 100% |
| `bin/assess-learner-state --repo R --path missions/<r>` | git + SQLite snapshot: head/working_copy/PRs/threads classified | p50 113ms (528× faster) |
| `bin/profile-recompute` | history → v3 profile.json (mastered/uncertain/calibration/recommendations) | 10K events ≤75ms |
| `bin/session-start --repo R --prompt P --path missions/<r>` | orchestrator: assess → recompute → daemon ask | cold 332ms, warm 4.2ms |

### Phase U — Onboarding / collection (10, G1 closure)

| Wrapper | Purpose |
|---|---|
| `bin/bootstrap` | First-Run system bootstrap (pip / index-fetch / daemon) |
| `bin/bootstrap-repo --repo R --owner O` | Full GitHub PR archive collection via gh CLI |
| `bin/onboard-repo --repo R --owner O` | clone + bootstrap-repo + mission-patterns + cross-crew chain |
| `bin/list-repos [--json]` | List all `state/repos/*/` + per-repo capability flags |
| `bin/archive-status [--repo R]` | SQLite archive stats |
| `bin/sync-prs --repo R --owner O` | Incremental sync since last collection_run.finished_at |
| `bin/repo-readiness --repo R` | 4-check capability gate (archive/patterns/cross/state) |
| `bin/doctor [--repo R]` | 6-check system health (python/gh/index/bge-m3/daemon/state) |
| `bin/validate-state [--strict]` | JSON/SQLite schema validation across state/ |
| `bin/registry-audit [--repair]` | orphan / uninitialized / corrupt detection |

### Phase V — Coaching context (12)

| Wrapper | Purpose |
|---|---|
| `bin/coach-run --repo R --prompt P` | Coaching action snapshot — writes `state/repos/<r>/actions/coach-run.json` |
| `bin/coach`, `bin/my-pr`, `bin/next-action`, `bin/topic`, `bin/reviewer`, `bin/compare`, `bin/compose-response` | Thin coaching-mode wrappers over `bin/ask`/`bin/coach-run` |
| `bin/mission-map --repo R [--summary]` | File→concept_ids map (HEAD + archive patches) |
| `bin/rag-rewrite-prepare --mode hyde\|decompose\|normalize` | Query rewrite prompt templates |
| `bin/rag-route-fallback --prompt P` | Router internals + AI disambig for low-confidence |
| `bin/chunk-context-prepare --concept-id C [--out-dir D]` | Per-concept artifact bundle |

### Phase W — Mining / analytics (12)

| Wrapper | Purpose |
|---|---|
| `bin/feedback-mine` | feedback.jsonl helpful/not_helpful distribution |
| `bin/response-quality-mine` | response-quality.jsonl flag + citation analysis |
| `bin/routing-analyze` | rag_ask router_mode + reason histogram |
| `bin/learning-turn-audit --last N` | Per-event integrity + response-quality join |
| `bin/learning-path-graph-audit` | concept_graph broken edges / cycles / level inversions |
| `bin/reclassify-history --last N` | Re-route history through current router (dry-run drift) |
| `bin/cohort-eval --cohort-file C.json` | N-query cohort latency + mode accuracy |
| `bin/cohort-compare --control A --candidate B [--fail-on-drift]` | A/B regression gate |
| `bin/golden update\|verify` | Golden fixture top-1 verify |
| `bin/rag-eval` | F1 RAG quality regression (wraps Phase K) |
| `bin/router-generalization-eval` | 20-fixture router accuracy |
| `bin/learner-log-rag-eval --limit N` | Historical prompt replay drift |

### Phase X — Maintenance + sub-commands (10)

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

### Bench totals (Phase T-X)

| Phase | Wrappers | Bench result |
|---|---|---|
| T | 7 | 7/7 + 17/17 e2e integration checks |
| U | 10 | 10/10 + 11/11 collection unit |
| V | 12 | 10/10 |
| W | 12 | 12/12 |
| X | 11 | 11/11 |
| **합계** | **52** | **50/50 bench + 28/28 unit + release archive gate** |

Total bin/* in woowa-learning-system: **64 entries**.

Full regression via meta-runner: `python3 tests/benchmarks/phase_y_all_benches.py` → 14/14 PASS in ~20s.
