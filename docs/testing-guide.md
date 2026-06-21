# Testing guide — release acceptance 재현

## 1. Unit tests (`pytest tests/`)

```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-system
python3 -m pytest tests/ -q
```

현재 **696 passed**.

주요 test 모듈:
- `test_router.py` — router decision (force-token override, AI 세션 mode override, f11 fast-path, 키워드 dispatch, cs_qa guard)
- `test_intent_prompt.py` / `test_phase8_intent_dispatch.py` — intent classification
- `test_drill.py` — 12 drill tests (offer/scoring/spaced repetition)
- `test_lazy_loader.py` — artifact loading
- `test_coach.py` — multi-agent prompt composition
- `test_mastery.py` / `test_feedback.py` — Bloom autoloop
- `test_search.py` / `test_anchors.py` / `test_mission.py` — RAG / anchors / F10 forward
- `test_peer_pr.py` — peer PR analysis
- `test_phase8_code_metrics.py` — LOC budget + entry point count

회귀 검증: 모든 코드 변경 후 `pytest` 실행하여 696 passed 유지 확인. fresh dev 환경에서는 먼저 `bin/setup --dev`로 pytest/pandas/pylance 포함 dev extra를 설치한다.

---

## 2. Scenario benchmarks (`tests/benchmarks/`)

핵심 시나리오는 `tests/benchmarks/release_acceptance.py`에서 한 번에 집계한다.

### 2.1 Full scenario comparison

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/full_scenario_comparison.py
```

**전제**: woowa-learning-system daemon 실행 중.

기대: 14/14 mode dispatch correct, evidence coverage ≥90%, p50/p95 latency gate pass.

### 2.2 Phase K — F1 + F5 critical (2)

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/rag_quality_regression.py
```

**전제**: daemon 실행.

소요: ~30초 (200 query stratified from corpus.expected_queries).

출력: `reports/rag_quality_regression.json`

기대: top-5 ≥ 93%, top-1 ≥ 75%.

F5 mastery는 `state/learner/mastery_graph.sqlite` 직접 inspect:
```bash
sqlite3 state/learner/mastery_graph.sqlite \
    "SELECT bloom_level, COUNT(*) FROM mastery GROUP BY bloom_level"
```
기대: Phase K fixture/replay 기준 mastered ≥ 1. 운영 learner state reset 직후는 empty 가능.

### 2.3 Phase L — 9 plan §verification gates (9)

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/gate_measurements.py
```

**전제**: daemon + mission_patterns + cross_crew parquet 빌드됨.

소요: ~3분. 출력: `reports/phase_l_gates.json`.

기대: 9/9 pass (F1, F2, F4, F6, F8, F10 forward t1/t2, F10 backward, F11 × 2 repos).

### 2.4 Phase M — 12 uncovered scenarios (12)

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios.py
```

**전제**: daemon.

소요: ~20초 + ~15초 (S5 cold/warm restart).

출력: `reports/phase_m_uncovered.json`.

기대: 12/12 pass.

### 2.5 Phase N — 12 second-wave (12)

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios_phase_n.py
```

**전제**: daemon + mission/cross_crew built.

소요: ~6분 (N6 cross-crew idempotent rebuild = 3분, N1+N3 daemon restart = 1분 각).

출력: `reports/phase_n_uncovered2.json`.

기대: 12/12 pass.

### 2.6 Phase P — 10 deep scenarios (10)

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/deep_scenarios_phase_p.py
```

**전제**: daemon.

소요: ~30초.

출력: `reports/phase_p_deep.json`.

기대: 10/10 pass.

### 2.7 Full-pipeline 4-arm eval (HyDE→CC→rerank, 세션-side)

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/full_pipeline_eval.py
```

**전제**: daemon (CC 기본). 소요: ~1분 (521쿼리 × 2 daemon search). 출력: stdout + `tests/fixtures/full_pipeline_baseline.json`.

**왜 필요한가 — 검증 방식의 핵심**: golden/eval_v2/cohort 게이트는 **raw 쿼리를 daemon `search` action에 넣어** 측정하므로 **CC fusion만** 본다. 검색 품질의 큰 레버인 **HyDE 쿼리재작성·세션 rerank·내용주입**은 세션(LLM) 행동/ask 경로라 그 게이트에 **안 잡힌다**. 이 벤치는 세션 행동을 **캐시 fixture**로 재현해 풀 파이프라인을 deterministic하게 측정한다:
- `full_pipeline_hyde_v1.json` — concept-vocab HyDE 키워드(14-agent workflow 생성)
- `full_pipeline_rerank_v1.json` — blind rerank 선택(7-agent workflow, qid→concept_id)
- `full_pipeline_queries_v1.json` — 521 쿼리(T-C+T-F) + 정답 + dense stratum

4-arm: arm0 raw+rrf(recorded) / arm1 raw+cc(게이트가 봄) / arm2 +HyDE / arm3 +rerank(full). gate = arm3 top1 회귀(>baseline−0.05) 시 FAIL.

기대(v1): top1 arm0 0.484 → arm1 0.526 → arm2 0.768 → **arm3 0.860 (+38pp)**, top5 0.718→0.929.

**Live refresh** (쿼리/코퍼스 변경 시 fixture 재생성, Claude 세션 필요): (1) tier 쿼리로 HyDE 생성 workflow(14-agent, concept-vocab 키워드), (2) HyDE 후보의 rerank-addressable에 blind rerank workflow(7-agent, breakage-guard), (3) 출력을 fixture로 저장. 절차는 reports/top1-top5-research/VERIFICATION-METHOD.md.

---

## 3. 한 번에 모두 (CI / release gate)

```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-system

# 1. Unit tests
python3 -m pytest tests/ -q || exit 1

# 2. Ensure daemon up
bin/rag-daemon status || bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90

# 3. Ensure mission/cross_crew built (idempotent)
bin/mission-patterns-build --repo spring-roomescape-member
bin/mission-patterns-build --repo spring-roomescape-auth
bin/cross-crew-build --repo spring-roomescape-member
bin/cross-crew-build --repo spring-roomescape-auth

# 4. Run all scenarios
export WOOWA_SESSION_MODE=development
python3 tests/benchmarks/rag_quality_regression.py
python3 tests/benchmarks/gate_measurements.py
python3 tests/benchmarks/uncovered_scenarios.py
python3 tests/benchmarks/uncovered_scenarios_phase_n.py
python3 tests/benchmarks/deep_scenarios_phase_p.py
python3 tests/benchmarks/full_scenario_comparison.py

# 5. Full release gate
python3 tests/benchmarks/release_acceptance.py
```

전체 소요: ~15분 (대부분 N6 cross-crew rebuild).

---

## 4. 새 시나리오 추가 가이드

`tests/benchmarks/<wave>.py` 형식:

```python
from dataclasses import dataclass, field

@dataclass
class ScenarioResult:
    name: str
    description: str
    observed: str       # human-readable observation
    passed: bool
    method: str         # how measurement was done
    details: dict = field(default_factory=dict)

def my_new_scenario() -> ScenarioResult:
    # ... measurement code ...
    return ScenarioResult(
        name="XYZ_new_scenario",
        description="brief description",
        observed=f"<measurement>",
        passed=<bool>,
        method="<methodology>",
        details={...},
    )

def main():
    results = [my_new_scenario()]
    out = REPO_ROOT / "reports" / "<wave>.json"
    out.write_text(json.dumps({
        "metadata": {...},
        "scenarios": [asdict(r) for r in results],
        "passed_n": sum(1 for r in results if r.passed),
        "total_n": len(results),
    }, ensure_ascii=False, indent=2))
```

run + commit 결과 + 새 `PHASE_*.md` narrative.

---

## 5. Smoke check (즉시 확인)

빠른 health check (~1분):

```bash
# Daemon alive
bin/rag-daemon ping

# 1 query end-to-end
python3 bin/ask "Bean이 뭐야"

# Unit tests
python3 -m pytest tests/ -q

# Mastery state
sqlite3 state/learner/mastery_graph.sqlite \
    "SELECT bloom_level, COUNT(*) FROM mastery GROUP BY bloom_level"
```

기대:
- ping: `{"alive": true}`
- ask: `[Mode: cs_qa]` markdown 응답
- pytest: `696 passed`
- mastery: reset 직후는 empty 가능, 실제 learning/code/drill event 누적 후 attempted/proficient/mastered가 생김
