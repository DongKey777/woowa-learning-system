"""Unit tests for core.response_quality — Phase T4."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.response_quality import (  # noqa: E402
    EXCERPT_MAX_CHARS, SCHEMA_ID, detect_citation_drift, record_response_quality, redact_pii,
)


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
    assert row["response_hash"]
