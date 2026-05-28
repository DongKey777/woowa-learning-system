"""Tests for core.response + bin/ask end-to-end + bin/learn-event."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from core.response import (
    ResponseTrace,
    parse_response,
    quality_flags,
    render_citation_block,
)
from core.state import LearnerProfile, load_profile, save_profile


# ── core.response ──────────────────────────────────────────────────────────


def test_parse_response_extracts_header_citations() -> None:
    md = (
        "[RAG: tier-1 — definition, 훈련지식 기반 · 적용: must_skip:spring/bean, freshness]\n"
        "DI는 의존성 주입이다.\n\n"
        "참고:\n- spring/di-intro\n- spring/bean\n"
    )
    t = parse_response(md)
    assert t.tier == 1
    assert "definition" in t.reason
    assert t.applied_tags == ["must_skip:spring/bean", "freshness"]
    assert t.citations == ["spring/di-intro", "spring/bean"]
    assert t.has_rag_header is True
    assert t.has_citation_block is True


def test_parse_response_handles_no_header() -> None:
    t = parse_response("그냥 답변이야")
    assert t.tier is None
    assert t.has_rag_header is False


def test_parse_response_extracts_mastery_hints() -> None:
    md = "mastered: spring/bean, spring/component\nuncertain: database/index"
    t = parse_response(md)
    assert t.declared_mastered == ["spring/bean", "spring/component"]
    assert t.declared_uncertain == ["database/index"]


def test_quality_flags_detects_missing_header() -> None:
    t = parse_response("DI는 의존성 주입이다.\n참고:\n- spring/di\n")
    flags = quality_flags(t, expected_citations=["spring/di"])
    assert "missing_rag_header" in flags
    assert "missing_citation" not in flags


def test_quality_flags_detects_citation_mismatch() -> None:
    t = parse_response("[RAG: tier-1 — x]\n참고:\n- spring/other\n")
    flags = quality_flags(t, expected_citations=["spring/di"])
    assert "citation_mismatch" in flags


def test_quality_flags_detects_missing_citation() -> None:
    t = parse_response("[RAG: tier-1 — x]\nDI는 의존성 주입.")
    flags = quality_flags(t, expected_citations=["spring/di"])
    assert "missing_citation" in flags


def test_render_citation_block_caps_at_max() -> None:
    out = render_citation_block(["a", "b", "c", "d", "e"], max_n=3)
    assert out == "참고:\n- a\n- b\n- c"


def test_render_citation_block_empty() -> None:
    assert render_citation_block([]) == ""


# ── bin/ask end-to-end (subprocess, mocked search via env) ─────────────────


def _run(args: list[str], state_root: Path, **env_extra) -> subprocess.CompletedProcess:
    identity_dir = state_root / "learner"
    identity_dir.mkdir(parents=True, exist_ok=True)
    (identity_dir / "identity.json").write_text(
        json.dumps({"github_login": "TestLearner", "learner_id": "TestLearner"}),
        encoding="utf-8",
    )
    env = {
        "PYTHONPATH": str(REPO_ROOT),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "ask"), *args,
         "--state-root", str(state_root)],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_ask_tool_only_prints_tool_prompt(tmp_path: Path) -> None:
    proc = _run(["gradle 빌드 어떻게"], state_root=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "tool token" in proc.stdout.lower() or "tier-0" in proc.stdout


def test_ask_retro_requires_repo(tmp_path: Path) -> None:
    proc = _run(["내 PR 흐름 보여줘"], state_root=tmp_path)
    assert proc.returncode != 0
    assert "repo required" in proc.stderr.lower() or "--repo" in proc.stderr


def test_learn_event_appends_history_and_updates_profile(tmp_path: Path) -> None:
    save_profile(LearnerProfile(learner_id="dk"), state_root=tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "learn-event"),
         "--event-type", "rag_ask", "--mode", "learning",
         "--learner-id", "dk", "--prompt", "DI", "--answer", "...",
         "--citation", "spring/di-intro",
         "--add-mastered", "spring/di-intro",
         "--state-root", str(tmp_path)],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO_ROOT)},
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    event_id = proc.stdout.strip().splitlines()[-1]
    assert event_id.startswith("rag_ask-")

    hist_lines = (tmp_path / "learner" / "history.jsonl").read_text().splitlines()
    assert len(hist_lines) == 1
    ev = json.loads(hist_lines[0])
    assert ev["event_type"] == "rag_ask"
    assert ev["payload"]["citations"] == ["spring/di-intro"]
    assert ev["mode"] == "learning"

    profile = load_profile("dk", state_root=tmp_path)
    assert "spring/di-intro" in profile.mastered_concepts
    assert profile.total_events == 1


def test_learn_event_dev_mode_skips_profile_update(tmp_path: Path) -> None:
    save_profile(LearnerProfile(learner_id="dk"), state_root=tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "learn-event"),
         "--event-type", "rag_ask", "--mode", "development",
         "--learner-id", "dk", "--prompt", "system test",
         "--add-mastered", "spring/di",
         "--state-root", str(tmp_path)],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO_ROOT)},
        timeout=30,
    )
    assert proc.returncode == 0
    profile = load_profile("dk", state_root=tmp_path)
    assert "spring/di" not in profile.mastered_concepts
    assert profile.total_events == 0
