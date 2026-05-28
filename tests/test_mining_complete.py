"""End-to-end tests for Phase W mining wrappers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _run_json(*args: str) -> dict:
    env = {**os.environ, "WOOWA_SESSION_MODE": "development"}
    result = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout)


def test_feedback_mine_since_filters_recent_rows(tmp_path: Path) -> None:
    now = time.time()
    state_root = tmp_path / "state"
    _write_jsonl(state_root / "learner" / "feedback.jsonl", [
        {
            "logged_at": now - 10 * 86400,
            "signal": "not_helpful",
            "doc_paths": ["old/doc"],
            "learner_id": "DongKey777",
        },
        {
            "logged_at": now - 3600,
            "signal": "helpful",
            "doc_paths": ["recent/helpful"],
            "learner_id": "DongKey777",
        },
        {
            "logged_at": now - 120,
            "signal": "not_helpful",
            "doc_paths": ["recent/not-helpful"],
            "learner_id": "DongKey777",
        },
        {
            "logged_at": now - 60,
            "signal": "not_helpful",
            "doc_paths": ["fixture/ignored"],
            "learner_id": "default",
            "source_event_id": "demo-event",
        },
        {
            "logged_at": now - 30,
            "mode": "development",
            "signal": "helpful",
            "doc_paths": ["dev/ignored"],
            "learner_id": "DongKey777",
        },
    ])

    out = _run_json(
        "bin/feedback-mine",
        "--state-root", str(state_root),
        "--since", "7d",
    )

    assert out["since"] == "7d"
    assert out["since_ts"] is not None
    assert out["total_signals"] == 2
    assert out["rows_skipped_fixture_or_demo_n"] == 1
    assert out["signals_by_type"] == {"helpful": 1, "not_helpful": 1}
    assert out["top_helpful_docs"] == [{"doc": "recent/helpful", "n": 1}]
    assert out["top_not_helpful_docs"] == [{"doc": "recent/not-helpful", "n": 1}]


def test_response_quality_mine_since_filters_recent_rows(tmp_path: Path) -> None:
    now = time.time()
    state_root = tmp_path / "state"
    _write_jsonl(state_root / "learner" / "response-quality.jsonl", [
        {
            "logged_at": now - 10 * 86400,
            "quality_flags": ["citation_mismatch"],
            "citation_paths_expected": ["old/expected"],
            "citation_paths_declared": ["old/declared"],
            "learner_id": "DongKey777",
        },
        {
            "logged_at": now - 3600,
            "quality_flags": ["missing_citation"],
            "citation_paths_expected": ["recent/expected"],
            "citation_paths_declared": [],
            "learner_id": "DongKey777",
        },
        {
            "logged_at": now - 120,
            "quality_flags": ["missing_response_body"],
            "contract_flags": ["possible_summary_body"],
            "response_body_path": "learner/response-bodies/sha256/ab/abc.md",
            "response_body_deduped": True,
            "response_body_stored_bytes": 123,
            "citation_paths_expected": ["recent/expected"],
            "citation_paths_declared": ["recent/declared"],
            "learner_id": "DongKey777",
        },
        {
            "logged_at": now - 60,
            "quality_flags": ["missing_response_body"],
            "citation_paths_expected": ["fixture/ignored"],
            "citation_paths_declared": [],
            "learner_id": "default",
            "source_event_id": "demo-event",
        },
        {
            "logged_at": now - 30,
            "mode": "development",
            "quality_flags": ["missing_response_body"],
            "citation_paths_expected": ["dev/ignored"],
            "citation_paths_declared": [],
            "learner_id": "DongKey777",
        },
    ])

    out = _run_json(
        "bin/response-quality-mine",
        "--state-root", str(state_root),
        "--since", "7d",
    )

    assert out["since"] == "7d"
    assert out["since_ts"] is not None
    assert out["rows_total"] == 2
    assert out["rows_skipped_fixture_or_demo_n"] == 1
    assert out["missing_body_n"] == 1
    assert out["possible_summary_body_n"] == 1
    assert out["full_body_paths_n"] == 1
    assert out["body_deduped_n"] == 1
    assert out["response_body_stored_bytes_total"] == 123
    assert out["citation_drift_n"] == 1
    assert out["flag_counts"] == {"missing_citation": 1, "missing_response_body": 1}
    assert out["contract_flag_counts"] == {"possible_summary_body": 1}
    assert out["top_expected_citations"] == [{"path": "recent/expected", "n": 2}]
    assert out["top_declared_citations"] == [{"path": "recent/declared", "n": 1}]


def test_learning_turn_audit_require_full_body(tmp_path: Path) -> None:
    now = time.time()
    state_root = tmp_path / "state"
    body = state_root / "learner" / "response-bodies" / "sha256" / "aa" / "aa.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text("[Mode: cs_qa]\n\nanswer", encoding="utf-8")
    _write_jsonl(state_root / "learner" / "history.jsonl", [
        {
            "event_id": "ask-ok",
            "event_type": "rag_ask",
            "mode": "learning",
            "ts": now,
            "payload": {"prompt": "락 설명", "router_mode": "cs_qa"},
        },
        {
            "event_id": "ask-missing",
            "event_type": "rag_ask",
            "mode": "learning",
            "ts": now,
            "payload": {"prompt": "DI 설명", "router_mode": "cs_qa"},
        },
    ])
    _write_jsonl(state_root / "learner" / "response-quality.jsonl", [
        {
            "source_event_id": "ask-ok",
            "mode": "learning",
            "response_body_path": "learner/response-bodies/sha256/aa/aa.md",
            "response_length_chars": 20,
        },
    ])

    result = subprocess.run(
        [
            sys.executable,
            "bin/learning-turn-audit",
            "--state-root", str(state_root),
            "--last", "2",
            "--require-full-body",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    out = json.loads(result.stdout)
    assert result.returncode == 1
    assert out["full_body_joined"] == 1
    assert out["missing_full_body_n"] == 1
    assert out["issues_sample"] == [
        {"event_id": "ask-missing", "issues": ["missing_response_quality"]}
    ]
