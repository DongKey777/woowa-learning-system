# Artifact catalog — `state/` `corpus/` 구조 (+ local `reports/`)

## 1. `corpus/` (committed, read-only at runtime)

```
corpus/
├── concepts/                      # 3339 JSON entities (1 concept = 1 file)
│   ├── algorithm/*.json
│   ├── data-structure/*.json
│   ├── database/*.json
│   ├── design-pattern/*.json
│   ├── language/*.json
│   ├── network/*.json
│   ├── operating-system/*.json
│   ├── security/*.json
│   ├── software-engineering/*.json
│   ├── spring/*.json
│   └── system-design/*.json
├── concept_graph.json             # ~5MB — nodes (3339) + edges (prerequisite 6172, confusable_with N)
└── schemas/
    └── concept.schema.json        # JSON schema (validation)
```

### Concept JSON schema
```json
{
  "id": "spring/bean-di-basics",
  "title": "Spring Bean DI 기초",
  "category": "spring",
  "level": "beginner|intermediate|advanced",
  "summary": "...",
  "body_markdown": "...",
  "aliases": ["Bean DI", "Dependency Injection in Spring"],
  "expected_queries": [
    "Spring Bean이 뭐야?",
    "Bean이랑 DI는 뭐가 달라?"
  ],
  "symptoms": [...],
  "relations": {
    "prerequisites": ["software-engineering/dependency-injection-basics"],
    "confusable_with": ["spring/ioc-di-basics"]
  },
  "learner_query_patterns": [...],
  "metadata": {...}
}
```

### `concept_graph.json` shape
```json
{
  "version": "v3",
  "built_at": "2026-05-...",
  "nodes": {
    "<concept_id>": {
      "category": "spring",
      "level": "beginner",
      "summary": "...",
      "body_ref": "concepts/spring/bean-di-basics.json",
      "code_signals": {
        "annotations": ["@Bean", "@Component"],
        "methods": [],
        "exceptions": [],
        "import_triggers": []
      }
    },
    ...
  },
  "edges": {
    "prerequisite": [["from_id", "to_id"], ...],   # 6172 pairs
    "confusable_with": [...],
    "extends": [...]
  },
  "stats": {...}
}
```

---

## 2. `state/` (gitignored, per-machine runtime)

```
state/
├── index/
│   ├── concepts.lance/            # Lance vector table; release index rebuilds after each 200 staged corpus docs
│   │   ├── _versions/
│   │   ├── data/
│   │   └── _transactions/
│   └── manifest.json              # build metadata (corpus_sha256, built_at, concepts_indexed, embed_dim, encoder_model, …)
├── learner/
│   ├── profile.json               # v3 profile: concepts/activity/calibration/recommendations
│   ├── history.jsonl              # append-only event log (rag_ask/code_attempt/drill_answer/…)
│   ├── feedback.jsonl             # explicit helpful/not_helpful/unclear learner feedback
│   ├── response-quality.jsonl     # response telemetry: capture_method + redacted excerpt ≤5000 chars + body path/hash/flags
│   ├── response-bodies/           # content-addressed redacted full final-answer bodies (sha256/redacted)
│   ├── pending-captures/          # hook-first response capture pending state
│   ├── capture-repair-queue.jsonl # non-blocking failed capture repair queue
│   ├── mastery_graph.sqlite       # Bloom autoloop state (mastery table + evidence table)
│   ├── drill_pending.json         # 1 open drill offer (or absent)
│   ├── drill_due.json             # spaced repetition due list
│   ├── pending_triggers.json      # pending self_assessment / review_drill triggers
│   ├── review_anchors.json        # F11 input — learner own review thread anchors (anchors-build)
│   ├── cross_mission.json         # mode cross_mission (learn-cross-mission-build)
│   ├── learning_path.json         # mode learning_path (learn-learning-path-build)
│   ├── memory_review.json         # mode memory_review (learn-memory-review-build)
│   └── meta_analytics.json        # mode meta_analytics (learn-meta-analytics-build)
├── repos/                          # per-repo derived artifacts
│   └── <repo>/
│       ├── archive/prs.sqlite3     # GitHub PR archive (bootstrap-repo / sync-prs)
│       ├── mission_patterns.json   # F10 forward — Tier 1+2 extracted patterns
│       ├── cross_crew_review_graph.parquet  # F11 — 4-stage filter result
│       ├── pr_meta.json            # mode pr_meta (learn-pr-meta-build)
│       ├── pr_review.json          # mode pr_review (learn-pr-review-build)
│       ├── pr_diff_evolution.json  # mode pr_diff_evolution (pr-diff-evolution-build)
│       ├── reviewer_profile.json   # mode reviewer_profile (reviewer-profile-build)
│       ├── temporal.json           # mode temporal (learn-temporal-build)
│       ├── thread_recon.json       # mode thread_recon (learn-thread-recon-build)
│       ├── cohort.json             # mode cohort (learn-cohort-build)
│       ├── peer_pr.json            # mode peer_compare — learner-vs-most-similar-peers diff (peer-pr-build)
│       ├── predict.json            # mode predict — fuses C/F/H artifacts (learn-predict-build)
│       ├── pr_threads/<n>.json     # live PR review-cycle snapshot per PR (pr-thread-status; delta source)
│       ├── actions/                # coach-run action snapshots (coach-run.json, …)
│       └── contexts/ analysis/ cache/ memory/ packets/ profiles/ logs/  # coaching context outputs
├── rag-daemon.sock                 # AF_UNIX daemon socket
└── rag-daemon.pid                  # daemon pid
```

> `pr_threads/<n>.json`은 진행 중 리뷰 사이클 전용 라이브 스냅샷이다. `bin/pr-thread-status`가 매 호출 GitHub를 fresh로 쿼리해 정합한 스레드 상태(미답변/pending/answered + resolved/outdated)를 기록하고, 다음 호출의 **델타**(새 resolved / 새 멘토 코멘트 / 새 답변) 계산 기준으로 직전 스냅샷을 덮어쓴다. archive(`prs.sqlite3`)와 달리 stale하지 않다(아카이브는 `sync-prs` 시점 + submitted-only).

### `mastery_graph.sqlite` schema
```sql
CREATE TABLE mastery (
  concept_id TEXT PRIMARY KEY,
  bloom_level TEXT,           -- attempted/familiar/proficient/mastered
  evidence_count INTEGER,
  last_seen_at REAL,
  promotion_trace TEXT        -- JSON [{from, to, ts}, ...]
);
CREATE TABLE evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  concept_id TEXT,
  source TEXT,                -- pr_merge/mentor_accept/drill_score/mission_use/self_assess
  weight REAL,
  payload TEXT                -- JSON
);
```

### `history.jsonl` event types
| event_type | mode | payload 필드 |
|---|---|---|
| `rag_ask` | learning/development | top-level learner_id/repo/`mode_source` + payload: prompt, `raw_utterance`, repo, router_mode, router_reason, `reformulated_query`, `reformulation_source`, top_concept_ids, latency_ms |
| `code_attempt` | learning | top-level repo(지정값 또는 `missions/<repo>/...` 경로 추론) + payload: file_path, concept_ids, lines_added, lines_removed, summary, linked_test |
| `drill_answer` | learning | drill_session_id, concept_ids, score, level, dimensions, answer_preview |
| `self_assessment` | learning | trigger_session_id, score, concept_ids |
| `test_result` | learning | test_class, test_method, status |
| `coach_run` | learning | prompt, repo, mode, evidence_summary |

#### `rag_ask` 필드 의미 (prompt vs raw_utterance / mode provenance)
- `payload.prompt`은 검색에 실제로 넘어간 텍스트다. 세션이 학습자 발화를 기술어휘로 **재작성한 출력**이며 raw 발화가 아니다.
- `payload.raw_utterance`는 세션이 `bin/ask --raw-utterance "<원문>"`로 동반 전달한 학습자 원문이다. raw→rewritten 쌍을 축적해 운영 경로(재작성 ON)를 나중에 측정하기 위한 필드. 미전달이면 `null`.
- `payload.reformulated_query`/`reformulation_source`는 daemon `core/reformulate`가 잡는 **anaphora 정도의 보정**만 기록한다(세션 재작성과는 다른 좁은 경로). 미해당이면 `null`.
- top-level `mode_source`는 `mode` 라벨의 출처다: `explicit`(세션이 `--session-mode` 명시) · `env`(`WOOWA_SESSION_MODE`) · `default`(둘 다 없어 보수적 `learning`) · `backfill_reclassified`(일회성 마이그레이션이 정정). 신뢰 가능한 mode 라벨과 그 신뢰도를 구분하기 위한 provenance다.
- 같은 `mode_source` 값이 그 turn의 `response_quality` 이벤트/`response-quality.jsonl` 행에도 상속된다(소스 `rag_ask` 이벤트가 단일 진실원천 — Stop-hook env로 재유도하지 않는다).

### `response-quality.jsonl` 수집 규칙
- `source_event_id`는 직전 `rag_ask` / `coach_run` event id와 join된다.
- 매 turn full body capture가 원칙이다. hook 환경에서는 `capture_method`가 `hook_claude`, `hook_codex`, `hook_gemini` 중 하나로 남는다.
- hook이 없는 환경의 토큰 효율 fallback은 `--response-path <answer.md>`다. 이 방식은 최종 답변을 transcript에 다시 붙여넣지 않는 host/client에서 사용한다.
- `--response-file -` stdin은 universal fallback이다. path capture가 불가능하면 세션 토큰 비용이 있더라도 본문 보존을 위해 사용한다.
- hook 수집 실패는 학습 답변을 막지 않고 `capture-repair-queue.jsonl`에 남긴다.
- 한 사용자 턴에서 `bin/ask`가 여러 번 호출되면 최종 hook capture와 연결되지 않은 이전 pending은 `superseded_by_later_capture`로 닫힌다.
- hook 없이 `bin/learn-response-quality` fallback으로 full body가 저장되면 같은 `source_event_id`의 pending capture도 `captured`로 갱신된다.
- 오래 남은 pending capture는 `bin/capture-repair --sync-pending`이 `response-quality.jsonl`의 full-body 행을 기준으로 재동기화한다.
- `capture_method="summary_only"`는 full-body capture가 정말 불가능한 예외 상황이며, `contract_flags`에 `body_not_captured`, `token_efficient_summary_only`가 남는다.
- summary-only에서는 본문 `참고:` 블록을 파싱할 수 없으므로 expected citation을 declared citation으로 복사하되 `declared_citation_unverified`를 남겨 false `missing_citation` drift를 피한다.
- full body가 들어온 경우 `response_excerpt`는 redacted prefix(최대 5000자), `response_body_path`는 redacted full body 파일 경로다.
- `response_body_path`는 redacted body hash 기반 content-addressed path라서 같은 본문은 한 번만 저장되고, row별 `response_body_deduped`로 중복 여부를 볼 수 있다.
- 요약본, 축약본, paraphrase를 full body처럼 넣으면 `contract_flags`에 `possible_summary_body`가 붙을 수 있다.

### `profile.json` shape
```json
{
  "schema_version": "v3",
  "learner_id": "DongKey777",
  "computed_at": 1779937200.0,
  "experience_level": "junior|intermediate|advanced",
  "concepts": {
    "mastered": ["spring/bean-di-basics"],
    "proficient": [],
    "uncertain": [],
    "underexplored": []
  },
  "activity": {
    "events_total": 0,
    "days_active": 0,
    "last_event_ts": 0.0,
    "event_type_counts": {}
  },
  "recent_code_changes_24h": 0,
  "calibration_status": {...},
  "next_recommendations": []
}
```

### `mission_patterns.json` shape
```json
{
  "repo": "spring-roomescape-member",
  "last_built_commit": "<sha>",
  "patterns": [
    {
      "file": "src/main/java/.../AdminReservationTimeController.java",
      "line": 17,
      "kind": "annotation",
      "value": "@RestController",
      "matched_concept_id": "spring/mvc-controller-basics",
      "confidence": 0.97,
      "extractor": "regex_tier1"
    },
    ...
  ]
}
```

### `cross_crew_review_graph.parquet` columns
- `anchor_thread_id`, `anchor_repo`, `anchor_pr`, `anchor_path`, `anchor_line`, `anchor_mentor`
- `candidate_pr`, `candidate_comment_id`, `candidate_path`, `candidate_line`,
  `candidate_reviewer`, `candidate_diff_hunk`, `candidate_comment`
- `crew_login`
- `jaccard` (float, ≥0.4 after Stage 2)
- `embed_cosine` (float, ranked by Stage 3)

---

## 3. `reports/` (local maintainer output, gitignored)

Mode B 측정 스크립트(`tests/benchmarks/*.py`, `bin/rag-eval`, `bin/cohort-eval` 등)는 결과를 `reports/`에 남긴다. 이 디렉토리는 **`.gitignore`** 처리되어 있어 public clone에는 포함되지 않는다 — 학습자에겐 0 가치이고 per-machine latency/cohort 스냅샷이 clone weight만 늘리기 때문이다.

학습자/contributor가 헤드라인 metric만 보려면 [verification-results.md](verification-results.md)에 inline으로 정리되어 있다. 본인 환경에서 raw report를 재생성하려면 해당 benchmark 스크립트를 직접 실행하면 디렉토리가 자동 생성된다.

---

## 4. Lifecycle 요약

| Artifact | 생성 시점 | 소비자 | 갱신 트리거 |
|---|---|---|---|
| corpus/concepts/*.json | Mode B (corpus 작업) | RAG search + drill builder | corpus 신규 추가 |
| corpus/concept_graph.json | `bin/graph-build` | RAG + F8 prereq walk | corpus 변경 후 |
| state/index/concepts.lance/ | `bin/corpus-build` 또는 release fetch | daemon search | corpus 변경 후 |
| state/learner/history.jsonl | daemon ask 매 turn | recent_history + profile recompute | append-only |
| state/learner/mastery_graph.sqlite | `core/feedback.record_turn` | profile.json compute | 매 evidence event |
| state/learner/profile.json | `bin/profile-recompute` / `bin/learner-profile recompute` | router pending_triggers + 학습자 surface | history 누적 후 + `bin/sync-prs` refresh(evidence-sync 직후 재투영) |
| state/learner/review_anchors.json | `anchors/extract.py` | F11 매칭 source | repo sync 후 optional |
| state/repos/<repo>/mission_patterns.json | `bin/mission-patterns-build` | coaching prompt | 학습자 신규 PR 후 |
| state/repos/<repo>/cross_crew_review_graph.parquet | `bin/cross-crew-build` | F11 prompt | anchors 갱신 후 |
| reports/*.json/.md (local, gitignored) | 측정 script 실행 (Mode B) | 회귀 분석 + commit message 작성용 | 측정 cycle |
