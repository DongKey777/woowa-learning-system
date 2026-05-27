"""Tests for conservative auto reformulation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.reformulate import (  # noqa: E402
    auto_reformulate,
    is_follow_up_prompt,
    select_reformulation,
)
from core.router import route  # noqa: E402
from rag.corpus_loader import load_corpus_runtime  # noqa: E402


def _rag_event(concepts: list[str], *, mode: str = "learning") -> dict:
    return {
        "event_id": "ask-1",
        "event_type": "rag_ask",
        "mode": mode,
        "ts": time.time(),
        "payload": {
            "prompt": "Spring DI가 뭐야",
            "router_mode": "cs_qa",
            "top_concept_ids": concepts,
        },
    }


def test_explicit_reformulation_wins() -> None:
    result = select_reformulation(
        prompt="그게 뭐야",
        explicit_reformulated_query="Spring DI 정의",
        history=[_rag_event(["spring/bean-di-basics"])],
    )

    assert result is not None
    assert result.source == "explicit"
    assert result.reformulated_query == "Spring DI 정의"


def test_non_follow_up_and_no_history_are_noop() -> None:
    corpus = load_corpus_runtime()

    assert auto_reformulate(
        prompt="Spring DI가 뭐야",
        history=[_rag_event(["spring/bean-di-basics"])],
        corpus=corpus,
    ) is None
    assert auto_reformulate(
        prompt="그게 뭐야",
        history=[],
        corpus=corpus,
    ) is None


def test_short_follow_up_uses_recent_rag_context_and_routes() -> None:
    corpus = load_corpus_runtime()

    result = auto_reformulate(
        prompt="그게 뭐야",
        history=[_rag_event(["spring/bean-di-basics"])],
        corpus=corpus,
    )

    assert result is not None
    assert result.source == "auto_recent_rag_context"
    assert result.prior_concept_ids == ["spring/bean-di-basics"]
    assert "Spring" in result.reformulated_query
    assert "정의 설명" in result.reformulated_query
    assert route(result.reformulated_query).mode == "cs_qa"


def test_development_history_is_ignored() -> None:
    corpus = load_corpus_runtime()

    result = auto_reformulate(
        prompt="그게 뭐야",
        history=[_rag_event(["spring/bean-di-basics"], mode="development")],
        corpus=corpus,
    )

    assert result is None


def test_follow_up_regex_is_narrow() -> None:
    assert is_follow_up_prompt("그게 뭐야")
    assert is_follow_up_prompt("그 차이는?")
    assert not is_follow_up_prompt("오늘 날씨 어때")
    assert not is_follow_up_prompt("DI가 뭐야")


def test_bin_ask_auto_reformulates_with_state_history(tmp_path: Path) -> None:
    learner = tmp_path / "learner"
    learner.mkdir(parents=True)
    (learner / "history.jsonl").write_text(
        json.dumps(_rag_event(["spring/bean-di-basics"]), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bin" / "ask"),
            "그게 뭐야",
            "--state-root",
            str(tmp_path),
            "--no-daemon",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "WOOWA_REFORMULATE": "auto", "WOOWA_SESSION_MODE": "test"},
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "cs_qa"
    assert payload["response_hints"]["reformulated_query"]
    assert payload["response_hints"]["citation_paths"]
