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
    _write_jsonl(state_root / "cs_rag" / "feedback.jsonl", [
        {
            "logged_at": now - 10 * 86400,
            "signal": "not_helpful",
            "doc_paths": ["old/doc"],
        },
        {
            "logged_at": now - 3600,
            "signal": "helpful",
            "doc_paths": ["recent/helpful"],
        },
        {
            "logged_at": now - 120,
            "signal": "not_helpful",
            "doc_paths": ["recent/not-helpful"],
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
        },
        {
            "logged_at": now - 3600,
            "quality_flags": ["missing_citation"],
            "citation_paths_expected": ["recent/expected"],
            "citation_paths_declared": [],
        },
        {
            "logged_at": now - 120,
            "quality_flags": ["missing_response_body"],
            "citation_paths_expected": ["recent/expected"],
            "citation_paths_declared": ["recent/declared"],
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
    assert out["missing_body_n"] == 1
    assert out["citation_drift_n"] == 1
    assert out["flag_counts"] == {"missing_citation": 1, "missing_response_body": 1}
    assert out["top_expected_citations"] == [{"path": "recent/expected", "n": 2}]
    assert out["top_declared_citations"] == [{"path": "recent/declared", "n": 1}]
