"""Unit tests for core.response_quality — Phase T4."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.response_quality import (  # noqa: E402
    EXCERPT_MAX_CHARS, SCHEMA_ID, detect_citation_drift, record_response_quality, redact_pii,
)
from core.response_capture import create_pending_capture, load_pending_capture  # noqa: E402


def test_redact_email() -> None:
    assert "[EMAIL]" in redact_pii("foo@bar.com 답변")
    assert "foo@bar.com" not in redact_pii("foo@bar.com 답변")


def test_redact_bearer_token() -> None:
    s = "Authorization: Bearer abc123def456ghi789jkl"
    out = redact_pii(s)
    assert "[BEARER_TOKEN]" in out
    assert "abc123def456ghi789jkl" not in out


def test_redact_api_key_github() -> None:
    s = "key=ghp_abc123def456ghi789jklmnop"
    out = redact_pii(s)
    assert "[API_KEY]" in out


def test_redact_phone() -> None:
    s = "전화 010-1234-5678 입니다"
    out = redact_pii(s)
    assert "[PHONE]" in out


def test_redact_preserves_safe_text() -> None:
    s = "Spring Bean DI 기본"
    assert redact_pii(s) == s


def test_detect_citation_drift_missing() -> None:
    assert detect_citation_drift(["a.md"], []) == {"missing_citation"}


def test_detect_citation_drift_extra() -> None:
    assert detect_citation_drift(["a.md"], ["a.md", "b.md"]) == {"extra_citation"}


def test_detect_citation_drift_mismatch() -> None:
    assert detect_citation_drift(["a.md", "b.md"], ["a.md"]) == {"citation_mismatch"}


def test_detect_citation_drift_exact_match_empty() -> None:
    assert detect_citation_drift(["a.md"], ["a.md"]) == set()


def test_record_response_quality_writes_jsonl(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    row = record_response_quality(
        source_event_id="ask-123", response_summary="DI 설명",
        response_body="Bean은 컨테이너가 관리하는 객체야. foo@bar.com 무시.",
        expected_citation_paths=["spring/bean-di.md"],
        declared_citation_paths=["spring/bean-di.md"],
        state_root=state,
    )
    assert row["schema_id"] == SCHEMA_ID
    assert "[EMAIL]" in row["response_excerpt"]
    assert row["response_body_path"]
    body_path = state / row["response_body_path"]
    assert body_path.exists()
    stored = body_path.read_text(encoding="utf-8")
    assert "[EMAIL]" in stored
    assert "foo@bar.com" not in stored
    assert row["citation_paths_expected"] == ["spring/bean-di.md"]
    assert "missing_response_body" not in row["quality_flags"]
    # File written
    log = (state / "learner" / "response-quality.jsonl").read_text(encoding="utf-8")
    assert json.loads(log.strip())["schema_id"] == SCHEMA_ID


def test_record_response_quality_missing_body_flag(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    row = record_response_quality(
        source_event_id="ask-x", response_summary="empty test",
        response_body=None, state_root=state,
    )
    assert "missing_response_body" in row["quality_flags"]
    assert row["response_length_chars"] == 0


def test_record_response_quality_citation_drift_auto(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    row = record_response_quality(
        source_event_id="ask-cite", response_summary="cited",
        response_body="some answer",
        expected_citation_paths=["a.md", "b.md"],
        declared_citation_paths=["a.md"],
        state_root=state,
    )
    assert "citation_mismatch" in row["quality_flags"]


def test_record_response_quality_appends_history_event(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    record_response_quality(
        source_event_id="ask-h", response_summary="x",
        response_body="answer body", state_root=state,
    )
    hist = (state / "learner" / "history.jsonl").read_text(encoding="utf-8")
    events = [json.loads(l) for l in hist.splitlines() if l.strip()]
    rq = [e for e in events if e.get("event_type") == "response_quality"]
    assert len(rq) == 1
    assert rq[0]["payload"]["source_event_id"] == "ask-h"


def test_record_response_quality_persists_session_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WOOWA_SESSION_MODE", "development")
    state = tmp_path
    (state / "learner").mkdir(parents=True)

    row = record_response_quality(
        source_event_id="ask-mode",
        response_summary="x",
        response_body="answer body",
        learner_id="TestLearner",
        state_root=state,
    )

    hist = (state / "learner" / "history.jsonl").read_text(encoding="utf-8")
    event = json.loads(hist.strip())
    assert row["mode"] == "development"
    assert event["mode"] == "development"


def test_record_response_quality_mode_source_explicit(monkeypatch, tmp_path: Path) -> None:
    # Explicit mode + provenance (e.g. inherited from the source rag_ask event)
    # wins over env, and is recorded on both the row and the history event.
    monkeypatch.setenv("WOOWA_SESSION_MODE", "learning")
    state = tmp_path
    (state / "learner").mkdir(parents=True)

    row = record_response_quality(
        source_event_id="ask-ms", response_summary="x", response_body="body",
        mode="development", mode_source="backfill_reclassified", state_root=state,
    )

    event = json.loads((state / "learner" / "history.jsonl").read_text().strip())
    assert row["mode"] == "development"
    assert row["mode_source"] == "backfill_reclassified"
    assert event["mode_source"] == "backfill_reclassified"


def test_record_response_quality_mode_source_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WOOWA_SESSION_MODE", "development")
    state = tmp_path
    (state / "learner").mkdir(parents=True)

    row = record_response_quality(
        source_event_id="ask-env", response_summary="x", response_body="body",
        state_root=state,
    )
    assert row["mode"] == "development"
    assert row["mode_source"] == "env"


def test_record_response_quality_mode_source_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WOOWA_SESSION_MODE", raising=False)
    state = tmp_path
    (state / "learner").mkdir(parents=True)

    row = record_response_quality(
        source_event_id="ask-def", response_summary="x", response_body="body",
        state_root=state,
    )
    assert row["mode"] == "learning"
    assert row["mode_source"] == "default"


def test_record_response_quality_keeps_five_thousand_char_excerpt(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    body = "가" * 5500

    row = record_response_quality(
        source_event_id="ask-long",
        response_summary="long answer",
        response_body=body,
        state_root=state,
    )

    assert EXCERPT_MAX_CHARS == 5000
    assert row["response_length_chars"] == 5500
    assert len(row["response_excerpt"]) == 5000
    assert row["response_excerpt_truncated"] is True
    assert row["response_body_path"]
    assert len((state / row["response_body_path"]).read_text(encoding="utf-8")) == 5500
    assert row["response_hash"]


def test_record_response_quality_dedupes_identical_full_bodies(tmp_path: Path) -> None:
    state = tmp_path
    body = "[Mode: cs_qa]\n\n같은 답변 본문\n\n참고:\n- database/lock-basics\n"

    first = record_response_quality(
        source_event_id="ask-1",
        response_summary="same",
        response_body=body,
        state_root=state,
    )
    second = record_response_quality(
        source_event_id="ask-2",
        response_summary="same again",
        response_body=body,
        state_root=state,
    )

    assert first["response_body_path"] == second["response_body_path"]
    assert first["response_body_storage"] == "sha256-redacted-v1"
    assert first["response_body_deduped"] is False
    assert second["response_body_deduped"] is True
    assert first["response_body_stored_bytes"] > 0
    assert second["response_body_stored_bytes"] == 0
    body_files = list((state / "learner" / "response-bodies").rglob("*.md"))
    assert len(body_files) == 1
    assert body_files[0].read_text(encoding="utf-8") == body


def test_learn_response_quality_cli_derives_summary_and_citations(tmp_path: Path) -> None:
    state = tmp_path
    body = (
        "[Mode: cs_qa]\n\n"
        "락은 동시에 같은 데이터를 바꿀 때 순서를 보장하는 장치야.\n\n"
        "참고:\n"
        "- database/lock-basics\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-cli",
            "--response-file", "-",
            "--expected-citation", "database/lock-basics",
            "--allow-orphan",
            "--state-root", str(state),
            "--silent",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["response_length_chars"] == len(body)
    assert row["response_summary"].startswith("락은 동시에")
    assert row["citation_paths_declared"] == ["database/lock-basics"]
    assert "missing_response_body" not in row["quality_flags"]
    assert "missing_citation" not in row["quality_flags"]


def test_learn_response_quality_cli_flags_summary_like_body(tmp_path: Path) -> None:
    state = tmp_path
    body = (
        "[Mode: cs_qa]\n\n"
        "학습자 답 invariant 보호 측면 정답 인정. 간접 lock 패턴을 추가 설명.\n\n"
        "참고:\n"
        "- database/lock-basics\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-short",
            "--response-file", "-",
            "--expected-citation", "database/lock-basics",
            "--allow-orphan",
            "--state-root", str(state),
            "--silent",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["response_length_chars"] == len(body)
    assert "possible_summary_body" in row["contract_flags"]


def test_learn_response_quality_cli_full_body_has_no_summary_flag(tmp_path: Path) -> None:
    state = tmp_path
    paragraph = (
        "락은 동시에 같은 데이터를 바꿀 때 순서를 보장하는 장치야. "
        "reservation row를 FOR UPDATE로 잡으면 예약 자체가 사라지지 않도록 "
        "보호하면서, 같은 슬롯 대기 등록을 시도하는 여러 트랜잭션이 같은 row "
        "앞에서 차례대로 줄 서게 된다. 그래서 이 row는 domain invariant를 "
        "보호하는 대상이면서 동시에 guard row 또는 간접 mutex 역할을 한다. "
    )
    body = "[Mode: cs_qa]\n\n" + paragraph * 3 + "\n참고:\n- database/lock-basics\n"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-full",
            "--response-file", "-",
            "--expected-citation", "database/lock-basics",
            "--allow-orphan",
            "--state-root", str(state),
            "--silent",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["response_length_chars"] == len(body)
    assert "possible_summary_body" not in row["contract_flags"]


def test_learn_response_quality_cli_response_path_is_token_efficient(tmp_path: Path) -> None:
    state = tmp_path / "state"
    answer = tmp_path / "answer.md"
    body = (
        "[Mode: cs_qa]\n\n"
        "비관적 락은 먼저 잠그고, 낙관적 락은 version 조건으로 나중에 충돌을 감지해.\n\n"
        "참고:\n"
        "- database/lock-basics\n"
    )
    answer.write_text(body, encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-path",
            "--response-path", str(answer),
            "--expected-citation", "database/lock-basics",
            "--allow-orphan",
            "--state-root", str(state),
            "--silent",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["capture_method"] == "file_path"
    assert row["capture_input_chars"] == len(str(answer))
    assert row["response_length_chars"] == len(body)
    assert row["response_body_path"]
    assert (state / row["response_body_path"]).read_text(encoding="utf-8") == body
    assert row["response_body_storage"] == "sha256-redacted-v1"
    assert row["response_body_deduped"] is False


def test_learn_response_quality_cli_enriches_from_source_event(tmp_path: Path) -> None:
    state = tmp_path / "state"
    history = state / "learner" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({
        "event_id": "ask-real",
        "event_type": "rag_ask",
        "mode": "learning",
        "learner_id": "DongKey777",
        "repo": "spring-roomescape-waiting",
        "payload": {
            "prompt": "격리 수준 설명",
            "repo": "spring-roomescape-waiting",
            "router_mode": "cs_qa",
            "top_concept_ids": ["database/transaction-isolation-locking"],
        },
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    body = (
        "[Mode: cs_qa]\n\n"
        "격리 수준은 다른 트랜잭션 변경이 어디까지 보이는지 정하는 약속이야.\n\n"
        "참고:\n"
        "- database/transaction-isolation-locking\n"
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-real",
            "--response-file", "-",
            "--state-root", str(state),
            "--silent",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["repo"] == "spring-roomescape-waiting"
    assert row["citation_paths_expected"] == ["database/transaction-isolation-locking"]
    assert row["quality_flags"] == []


def test_learn_response_quality_cli_marks_pending_as_captured(tmp_path: Path) -> None:
    state = tmp_path / "state"
    event = {
        "event_id": "ask-pending",
        "event_type": "rag_ask",
        "mode": "learning",
        "learner_id": "DongKey777",
        "repo": "spring-roomescape-waiting",
        "payload": {
            "prompt": "격리 수준 설명",
            "repo": "spring-roomescape-waiting",
            "router_mode": "cs_qa",
            "top_concept_ids": ["database/transaction-isolation-locking"],
        },
    }
    history = state / "learner" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    create_pending_capture(event, state_root=state)
    body = (
        "[Mode: cs_qa]\n\n"
        "격리 수준은 다른 트랜잭션 변경이 어디까지 보이는지 정하는 약속이야.\n\n"
        "참고:\n"
        "- database/transaction-isolation-locking\n"
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-pending",
            "--response-file", "-",
            "--state-root", str(state),
            "--silent",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    pending = load_pending_capture("ask-pending", state_root=state)
    assert pending is not None
    assert pending["status"] == "captured"
    assert pending["response_body_path"]
    assert pending["synced_from_response_quality"] is True


def test_learn_response_quality_cli_rejects_orphan_source_event(tmp_path: Path) -> None:
    state = tmp_path / "state"
    body = "[Mode: cs_qa]\n\n답변 본문"
    # Orphan rejection writes to learner state (capture-repair-queue), so it is
    # gated to learning mode and skipped under development mode. Pin the mode so
    # the test is hermetic regardless of the suite's ambient WOOWA_SESSION_MODE.
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-orphan",
            "--response-file", "-",
            "--state-root", str(state),
            "--silent",
        ],
        input=body,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "WOOWA_SESSION_MODE": "learning"},
    )

    assert proc.returncode == 2
    assert "source_event_id" in proc.stderr
    queue = state / "learner" / "capture-repair-queue.jsonl"
    assert queue.exists()
    row = json.loads(queue.read_text(encoding="utf-8"))
    assert row["reason"] == "orphan_source_event"


def test_learn_response_quality_cli_summary_only_marks_body_not_captured(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "learn-response-quality"),
            "--source-event-id", "ask-summary",
            "--response-summary", "낙관락/비관락 차이를 짧게 설명함",
            "--summary-only",
            "--expected-citation", "database/lock-basics",
            "--allow-orphan",
            "--state-root", str(state),
            "--silent",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["capture_method"] == "summary_only"
    assert row["response_body_path"] is None
    assert "missing_response_body" in row["quality_flags"]
    assert "missing_citation" not in row["quality_flags"]
    assert row["citation_paths_declared"] == ["database/lock-basics"]
    assert "body_not_captured" in row["contract_flags"]
    assert "token_efficient_summary_only" in row["contract_flags"]
    assert "declared_citation_unverified" in row["contract_flags"]
