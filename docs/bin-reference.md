# bin/ reference — core entries + Phase T-X wrappers

| Entry | Mode | Purpose |
|---|---|---|
| [`bin/setup`](#binsetup) | A/B | `.venv` 생성 + `pip install -e .` (PEP 668 안전, `--dev`로 pytest 포함) |
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
bin/setup --dev    # + pytest (시스템 개발 / Mode B)
```

- macOS Homebrew / Debian 시스템 Python은 PEP 668 (externally-managed)이라 직접 `pip install`이 거부된다. venv가 이를 마찰 없이 우회한다 (`--break-system-packages` 불필요, 시스템 Python 오염 없음).
- 설치 후 `bin/` 명령들은 `core/_venv.py` 가드를 통해 `.venv`를 자동 사용한다. `.venv`가 없으면 가드는 no-op이라 시스템 Python에 deps가 이미 깔린 환경은 그대로 동작.
- 멱등(idempotent): 재실행 시 `.venv`를 재사용하고 in-place 업그레이드.
- `bin/bootstrap`이 First-Run 1단계에서 자동 호출 (deps가 이미 import되면 skip).

---

## `bin/ask`

학습자 query를 daemon에 보내고 markdown prompt 반환. AI 세션이 학습자 의도 받으면 자동 호출.

```bash
bin/ask "<prompt>" [--repo R] [--learner-id L] [--json-route] [--no-daemon] [--state-root PATH]
```

옵션:
- `--repo R`: 학습자 mission repo 지정 (coaching/retro/F11 모드 트리거 가능)
- `--learner-id L`: learner identifier (omitted이면 `state/learner/identity.json`에서 자동 해석)
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
bin/index-fetch [--tag paradigm-v2-index-v1.0.3] [--force] [--auto-upgrade] [--expected-sha256 <hex>]
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
cp state/index.tar.zst /tmp/paradigm-v2-index-v1.0.3.tar.zst
gh release create paradigm-v2-index-v1.0.3 /tmp/paradigm-v2-index-v1.0.3.tar.zst \
    --title "woowa-learning-system Lance index v1.0.3" --notes "...SHA256..."
# 이후 학습자는 bin/index-fetch --tag paradigm-v2-index-v1.0.3 로 업데이트
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
| `bin/learn-pr-retro --repo R [--include-bot]` | PR retrospective: recurring mentor signals + unresolved threads + timeline | p50 1.2ms |
| `bin/learn-record-code --file-path P --summary S` | code_attempt event + auto concept inference | p95 1.19ms (168× faster) |
| `bin/learn-test --path build/test-results/test/` | JUnit XML → test_result events, stable event_id (idempotent) | p50 3.6ms (333× faster) |
| `bin/learn-response-quality --source-event-id E --response-file -` / `--response-path answer.md` | full-response telemetry; path mode avoids transcript duplication, stdin is universal fallback, redacted full body + excerpt ≤5000 chars + summary/declared citation auto-extract | p95 0.16ms, drift 100%, PII 100% |
| `bin/capture-response --client claude\|codex\|gemini --hook-json -` | hook-first full body capture; joins latest pending `rag_ask`, non-blocking repair queue on failure | hook path |
| `bin/capture-repair --last N [--apply]` | replay repairable failed captures from `capture-repair-queue.jsonl` | local |
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
