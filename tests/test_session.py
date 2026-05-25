"""Unit tests for core.session — Phase T7."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.session import (  # noqa: E402
    _assess_cache_fresh, _profile_cache_fresh, ASSESS_CACHE_SECS,
    PROFILE_CACHE_SECS,
)


def test_assess_cache_fresh_returns_false_when_missing(tmp_path: Path) -> None:
    assert _assess_cache_fresh("nope", tmp_path, time.time()) is False


def test_assess_cache_fresh_true_when_recent(tmp_path: Path) -> None:
    p = tmp_path / "repos" / "demo" / "contexts"
    p.mkdir(parents=True)
    (p / "learner-state.json").write_text(json.dumps({
        "computed_at": time.time(), "schema_version": "v1"
    }), encoding="utf-8")
    assert _assess_cache_fresh("demo", tmp_path, time.time()) is True


def test_assess_cache_fresh_false_when_stale(tmp_path: Path) -> None:
    p = tmp_path / "repos" / "demo" / "contexts"
    p.mkdir(parents=True)
    (p / "learner-state.json").write_text(json.dumps({
        "computed_at": time.time() - ASSESS_CACHE_SECS - 100,
        "schema_version": "v1"
    }), encoding="utf-8")
    assert _assess_cache_fresh("demo", tmp_path, time.time()) is False


def test_profile_cache_fresh_missing(tmp_path: Path) -> None:
    assert _profile_cache_fresh(tmp_path, time.time()) is False


def test_profile_cache_fresh_recent(tmp_path: Path) -> None:
    p = tmp_path / "learner"
    p.mkdir(parents=True)
    (p / "profile.json").write_text(json.dumps({
        "computed_at": time.time(), "schema_version": "v3"
    }), encoding="utf-8")
    assert _profile_cache_fresh(tmp_path, time.time()) is True


def test_profile_cache_fresh_stale(tmp_path: Path) -> None:
    p = tmp_path / "learner"
    p.mkdir(parents=True)
    (p / "profile.json").write_text(json.dumps({
        "computed_at": time.time() - PROFILE_CACHE_SECS - 100,
        "schema_version": "v3"
    }), encoding="utf-8")
    assert _profile_cache_fresh(tmp_path, time.time()) is False


def test_session_with_no_daemon_returns_error(tmp_path: Path) -> None:
    """daemon socket missing → ask_ok=False, errors populated."""
    from core.session import start_session
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    # No daemon socket in tmp state
    result = start_session(
        repo=None, prompt="test query",
        mission_path=None,  # no assess
        state_root=state,
    )
    assert result["ask_ok"] is False
    assert any("daemon down" in e for e in result["errors"])
    assert result["assess_ran"] is False  # no mission_path
    # profile_ran True (no cache, no daemon needed for profile)
    assert result["profile_ran"] is True
