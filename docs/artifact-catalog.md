# Artifact catalog — `state/` `reports/` `corpus/` 구조

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
│   └── manifest.json              # build metadata (corpus_hash, built_at, etc.)
├── learner/
│   ├── profile.json               # v3 profile: concepts/activity/calibration/recommendations
│   ├── history.jsonl              # append-only event log (rag_ask/code_attempt/drill_answer/…)
│   ├── feedback.jsonl             # explicit helpful/not_helpful/unclear learner feedback
│   ├── response-quality.jsonl     # response telemetry: capture_method + redacted excerpt ≤5000 chars + body path/hash/flags
│   ├── response-bodies/           # optional redacted full final-answer bodies captured by path/stdin/text mode
│   ├── mastery_graph.sqlite       # Bloom autoloop state (mastery table + evidence table)
│   ├── drill_pending.json         # 1 open drill offer (or absent)
│   ├── drill_due.json             # spaced repetition due list
│   ├── pending_triggers.json      # pending self_assessment / review_drill triggers
│   └── review_anchors.json        # optional thread anchors extracted from mission repos
├── repos/                          # per-repo derived artifacts
│   └── <repo>/
│       ├── mission_patterns.json   # F10 forward — Tier 1+2 extracted patterns
│       └── cross_crew_review_graph.parquet  # F11 — 4-stage filter result
├── rag-daemon.sock                 # AF_UNIX daemon socket
└── rag-daemon.pid                  # daemon pid
```

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
| `rag_ask` | learning/development | top-level learner_id/repo + prompt, repo, router_mode, router_reason, top_concept_ids, latency_ms |
| `code_attempt` | learning | file_path, concept_ids, lines_added, lines_removed, summary, linked_test |
| `drill_answer` | learning | drill_session_id, concept_ids, score, level, dimensions, answer_preview |
| `self_assessment` | learning | trigger_session_id, score, concept_ids |
| `test_result` | learning | test_class, test_method, status |
| `coach_run` | learning | prompt, repo, mode, evidence_summary |

### `response-quality.jsonl` 수집 규칙
- `source_event_id`는 직전 `rag_ask` / `coach_run` event id와 join된다.
- 토큰 효율 기본값은 `capture_method="summary_only"`이며, `contract_flags`에 `body_not_captured`, `token_efficient_summary_only`가 남는다.
- summary-only에서는 본문 `참고:` 블록을 파싱할 수 없으므로 expected citation을 declared citation으로 복사하되 `declared_citation_unverified`를 남겨 false `missing_citation` drift를 피한다.
- full body capture는 `--response-path <answer.md>`가 우선이다. 이 방식은 최종 답변을 transcript에 다시 붙여넣지 않는 host/client에서 사용한다.
- `--response-file -` stdin은 호환 fallback이다. 긴 답변을 telemetry만을 위해 heredoc으로 재전송하지 않는다.
- full body가 들어온 경우 `response_excerpt`는 redacted prefix(최대 5000자), `response_body_path`는 redacted full body 파일 경로다.
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

## 3. `reports/` (committed, append-only measurement results)

```
reports/
├── PARADIGM_V2_VS_LEGACY_FINAL.md           # Phase J narrative
├── PHASE_K_VERIFICATION.md                   # F1 + F5 critical gates
├── PHASE_L_ALL_GATES_FINAL.md                # 9 plan gates
├── PHASE_M_UNCOVERED_FINAL.md                # 12 uncovered scenarios
├── PHASE_N_UNCOVERED2_FINAL.md               # 12 second-wave + read_history fix
├── PHASE_P_DEEP_FINAL.md                     # 10 deep scenarios + drill cycle
├── phase_l_gates.json                        # raw JSON per gate
├── phase_m_uncovered.json
├── phase_n_uncovered2.json
├── phase_p_deep.json
├── rag_quality_regression.json               # F1 200-query measurement
├── historical comparison reports             # archived 14-scenario / deepdive baselines
├── coaching_eval.json
├── phase_g_v2_eval.json                      # corpus enrichment cycle
├── phase9_rag_eval.json
└── ... (older reports retained for history)
```

각 `PHASE_*.md`는 narrative + table + reproduction 명령 포함. `*.json`은 raw structured output.

---

## 4. Lifecycle 요약

| Artifact | 생성 시점 | 소비자 | 갱신 트리거 |
|---|---|---|---|
| corpus/concepts/*.json | Mode B (corpus 작업) | RAG search + drill builder | corpus 신규 추가 |
| corpus/concept_graph.json | `bin/graph-build` | RAG + F8 prereq walk | corpus 변경 후 |
| state/index/concepts.lance/ | `bin/corpus-build` 또는 release fetch | daemon search | corpus 변경 후 |
| state/learner/history.jsonl | daemon ask 매 turn | recent_history + profile recompute | append-only |
| state/learner/mastery_graph.sqlite | `core/feedback.record_turn` | profile.json compute | 매 evidence event |
| state/learner/profile.json | `bin/profile-recompute` / `bin/learner-profile recompute` | router pending_triggers + 학습자 surface | history 누적 후 |
| state/learner/review_anchors.json | `anchors/extract.py` | F11 매칭 source | repo sync 후 optional |
| state/repos/<repo>/mission_patterns.json | `bin/mission-patterns-build` | coaching prompt | 학습자 신규 PR 후 |
| state/repos/<repo>/cross_crew_review_graph.parquet | `bin/cross-crew-build` | F11 prompt | anchors 갱신 후 |
| reports/*.json/.md | 측정 script 실행 (Mode B) | 회귀 분석 + commit message | 측정 cycle |
