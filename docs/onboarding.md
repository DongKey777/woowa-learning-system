# Onboarding — First-Run Protocol 상세

학습자가 fresh clone 후 AI 세션 켜면 **AI가 자동으로** 이 단계들을 수행한다. 학습자가 외울 명령은 0개.

## 학습자 시각의 흐름

```
1. git clone https://github.com/DongKey777/woowa-learning-system.git
2. cd woowa-learning-system
3. Claude Code / Codex CLI / Gemini CLI 실행
4. AI에게: "세팅하고 학습 시작하자"
5. AI가 한국어로 1-2분간 진행 보고:
   - "Python 3.13 확인 ✓"
   - "의존성 설치 중… (~30초)"
   - "BGE-M3 모델 다운로드 중… (~5분, 첫 실행만 3GB)"
   - "인덱스 다운로드 중… (~15초, release SHA256 검증)"
   - "Daemon 시작 + warm-up… (~10초)"
   - "준비 끝났어. 뭘 학습하고 싶어?"
```

이후 학습자는 한국어 질문만 던지면 됨.

---

## AI 세션 시각의 자동 단계

### Step 0 — 환경 sanity check

```bash
python3 --version            # 3.10+
uname -a                     # macOS / Linux 확인
```

- < 3.10이면 한국어 upgrade 권고 (강제 X):
  - macOS: `brew install python@3.12`
  - Ubuntu: `sudo apt install python3.12`
  - Windows native: `winget install Python.Python.3.12`
  - WSL: Ubuntu와 동일
- `git`, `pip` 부재 시 같은 톤으로 안내.

### Step 1 — Python 의존성

```bash
pip install -e .
```

설치되는 핵심 패키지:
- `sentence-transformers` (BGE-M3 wrapper)
- `FlagEmbedding` (BGE-M3 native)
- `lancedb` (vector index)
- `numpy`, `pyarrow` (수치/columnar)
- `jsonschema` (corpus validation)
- `torch` (BGE-M3 backend, MPS on M-series)

성공 신호: `pip list | grep -E "sentence-transformers|lancedb"` 로 둘 다 출력.

실패 시 OS별 안내:
- macOS Apple Silicon: torch wheel은 MPS 지원판 자동 선택됨. 안 되면 `pip install --upgrade torch`.
- Ubuntu Intel/AMD: CUDA가 없으면 CPU torch 자동. 성능은 비슷.
- Windows native: native venv 권장. WSL이 더 안정.

### Step 2 — HuggingFace 모델 캐시 warm-up

첫 daemon 시작 시 `BAAI/bge-m3` (~3GB) + reranker 자동 다운로드.

```bash
# 사전 캐시하려면
python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"
```

offline 환경: 사전에 모델 폴더 복사 후 `export HF_HUB_OFFLINE=1`.

학습자 디스크 ≥ 4GB 여유 권장. 부족하면 한국어로 안내 후 대기 (silent abort X).

### Step 3 — Lance 인덱스 다운로드 (release fetch only)

```bash
bin/index-fetch
```

GitHub Releases (`DongKey777/woowa-learning-system`)에 publish된 사전 빌드 인덱스 (release별 약 13-19MB zstd compressed)를 다운로드해서 `state/index/`로 추출. SHA256 자동 검증.

- 소요: 10-20초 (네트워크 광대역)
- 의존: `gh` CLI (미설치면 한국어로 OS별 설치 안내)
- 기본 tag: `paradigm-v2-index-v1.0.2`

#### 🚫 학습자 기기 로컬 빌드 절대 금지

`bin/corpus-build`는 **maintainer 전용** (RunPod / 강한 머신). 학습자 M4 기기에서 직접 빌드하면:
- 15-30분 소요 (BGE-M3 인코딩 dominates)
- peak RAM 4-6GB (8GB 기기 swap)
- 학습 흐름 차단

학습자 onboarding에서는 **`bin/index-fetch`만 사용**. 새 인덱스 버전이 필요하면 maintainer가 RunPod에서 빌드 → `gh release create` → 학습자는 `bin/index-fetch --tag <new>` 로 업데이트.

### Step 4 — Daemon 시작

```bash
bin/rag-daemon start-bg --log-path /tmp/daemon.log --timeout-s 90
```

prewarm 후 `state/rag-daemon.sock` 생성. 확인:

```bash
bin/rag-daemon ping
# {"alive": true, "ts": ...}
```

### Step 5 — Mission patterns / cross-crew 사전 빌드 (선택)

학습자가 미션 repo 준비 후:

```bash
bin/mission-patterns-build --repo <repo-name>
bin/cross-crew-build --repo <repo-name>
```

trigger: 학습자가 *"내 PR 보자"*, *"<repo> 분석"* 같은 의도 표현 시 AI가 자동 실행.

각각 결과:
- `state/repos/<repo>/mission_patterns.json` (~10-200 patterns)
- `state/repos/<repo>/cross_crew_review_graph.parquet` (~50-200 rows)

### Step 6 — 검증

```bash
python3 bin/ask "테스트 query"
```

정상 응답이 돌아오면 onboarding 완료. 학습자에게 한국어 1줄 보고:

> "세팅 끝났어. 뭘 학습하고 싶어?"

---

## 트러블슈팅

### `pip install` 중 torch wheel 다운로드 멈춤
- 네트워크 timeout. `pip install --timeout 60 -e .` 재시도.
- 그래도 안 되면 한국어로 *"네트워크가 느린 것 같아. 다시 시도할까?"* 안내.

### BGE-M3 다운로드가 멈춤
- HF rate limit. `HF_TOKEN` 환경변수 설정 권장 (학습자에게 한국어로 anonymous → token 권고).
- 또는 다시 시도 (resume 됨).

### `bin/index-fetch` 실패
- `gh` CLI 미설치: macOS `brew install gh`, Ubuntu `sudo apt install gh`, Windows `winget install GitHub.cli`
- GitHub 인증 실패: `gh auth login` 으로 브라우저 인증
- SHA256 mismatch: release artifact가 손상됐을 가능성. maintainer에게 보고.
- 학습자 기기에서 `bin/corpus-build` 시도는 금지 (위 §Step 3 참조).

### Daemon 시작 후 ping 응답 없음
- `cat /tmp/daemon.log` 로 에러 확인.
- 흔한 원인: BGE-M3 model 로드 중 (10초까지 정상) / port 충돌 (UNIX socket이라 보통 무관).

### `bin/ask "..."` 가 "daemon down" 반환
- daemon 죽었음. `bin/rag-daemon status` 후 재시작.
- 또는 `bin/ask "..." --no-daemon` 으로 in-process fallback (30s cold).

### Mission patterns가 0개
- 학습자 own PR이 archive에 없거나, Java 파일 patch_text가 비어 있음.
- archive sqlite 확인: `sqlite3 state/repos/<repo>/archive/prs.sqlite3 "SELECT COUNT(*) FROM pull_requests_current WHERE author_login = '<learner-login>'"`
- archive 자체가 없으면 (`state/repos/` 미생성) onboarding 첫 setup 시 fork repo 복사가 안 됐을 가능성 → maintainer에게 문의.

---

## 환경 변수 reference

| Variable | Default | 의미 |
|---|---|---|
| `WOOWA_SESSION_MODE` | `learning` | Mode A (learning) vs Mode B (development). development = personalization stream에서 제외 |
| `HF_HUB_OFFLINE` | unset | set이면 HuggingFace 모델 사전 캐시만 사용 (offline 환경) |
| `WOOWA_RAG_NO_DAEMON` | unset | set이면 daemon 우회, in-process fallback (debug/CI) |

추가 env는 [`bin-reference.md`](bin-reference.md) 각 entry 절 참조.

---

## 시간 예상 (최초 1회)

| 단계 | 시간 (M4 / 광대역) |
|---|---|
| `pip install -e .` | ~30초 (캐시 hit) ~ 2분 (cold) |
| BGE-M3 다운로드 | ~5분 (3GB) |
| `bin/index-fetch` (release) | ~15초 (약 13-19MB) |
| Daemon prewarm | ~10초 |
| **합계 (학습자 path)** | **~6분** |

두 번째 이후 세션은 daemon 살아있으면 즉시. daemon 죽었으면 step 4부터 (~10초).

학습자 path는 **항상 release fetch**. 로컬 인덱스 빌드는 학습 흐름 차단하므로 maintainer 전용.
