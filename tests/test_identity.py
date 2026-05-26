"""Unit tests for core.identity — Phase Y7 (identity wiring)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.identity import (  # noqa: E402
    DEFAULT_FALLBACK, detect_and_cache, load_identity, resolve_learner_login,
)


def _seed(tmp_path: Path) -> Path:
    (tmp_path / "learner").mkdir(parents=True)
    return tmp_path


def test_resolve_explicit_wins(tmp_path: Path) -> None:
    sr = _seed(tmp_path)
    assert resolve_learner_login(sr, explicit="bob") == "bob"


def test_resolve_env_wins_when_no_explicit(tmp_path: Path, monkeypatch) -> None:
    sr = _seed(tmp_path)
    monkeypatch.setenv("WOOWA_LEARNER_LOGIN", "alice")
    assert resolve_learner_login(sr) == "alice"


def test_resolve_cache_wins_when_no_env(tmp_path: Path, monkeypatch) -> None:
    sr = _seed(tmp_path)
    monkeypatch.delenv("WOOWA_LEARNER_LOGIN", raising=False)
    (sr / "learner" / "identity.json").write_text(
        json.dumps({"github_login": "cached_user"}), encoding="utf-8")
    assert resolve_learner_login(sr, auto_detect=False) == "cached_user"


def test_resolve_fallback_when_no_signal(tmp_path: Path, monkeypatch) -> None:
    sr = _seed(tmp_path)
    monkeypatch.delenv("WOOWA_LEARNER_LOGIN", raising=False)
    assert resolve_learner_login(sr, auto_detect=False) == DEFAULT_FALLBACK


def test_detect_and_cache_uses_gh_cli(tmp_path: Path) -> None:
    sr = _seed(tmp_path)

    class _R:
        def __init__(s, stdout): s.stdout = stdout; s.returncode = 0; s.stderr = ""

    def _mock_run(cmd, **kw):
        if cmd[0] == "gh" and "user" in cmd:
            return _R(json.dumps({"login": "ghuser", "email": "g@e.com"}))
        return _R("", 1)

    with patch("core.identity.subprocess.run", _mock_run):
        rec = detect_and_cache(sr)
    assert rec["github_login"] == "ghuser"
    assert rec["source"] == "gh_cli"
    assert (sr / "learner" / "identity.json").exists()


def test_detect_and_cache_returns_none_when_gh_missing(tmp_path: Path) -> None:
    sr = _seed(tmp_path)

    def _no_gh(cmd, **kw):
        raise FileNotFoundError("no gh")

    with patch("core.identity.subprocess.run", _no_gh):
        rec = detect_and_cache(sr)
    assert rec is None


def test_detect_and_cache_idempotent_skip_on_existing(tmp_path: Path) -> None:
    sr = _seed(tmp_path)
    (sr / "learner" / "identity.json").write_text(
        json.dumps({"github_login": "old_cache", "source": "manual"}),
        encoding="utf-8")

    # gh would return different login — but we expect cache to win
    with patch("core.identity.subprocess.run") as m:
        rec = detect_and_cache(sr)
        m.assert_not_called()
    assert rec["github_login"] == "old_cache"


def test_detect_and_cache_force_refresh_re_detects(tmp_path: Path) -> None:
    sr = _seed(tmp_path)
    (sr / "learner" / "identity.json").write_text(
        json.dumps({"github_login": "old"}), encoding="utf-8")

    class _R:
        def __init__(s, stdout): s.stdout = stdout; s.returncode = 0; s.stderr = ""

    def _mock(cmd, **kw):
        if cmd[0] == "gh":
            return _R(json.dumps({"login": "fresh"}))
        return _R("", 1)

    with patch("core.identity.subprocess.run", _mock):
        rec = detect_and_cache(sr, force_refresh=True)
    assert rec["github_login"] == "fresh"


def test_load_identity_missing_returns_none(tmp_path: Path) -> None:
    assert load_identity(tmp_path) is None


def test_load_identity_corrupt_returns_none(tmp_path: Path) -> None:
    sr = _seed(tmp_path)
    (sr / "learner" / "identity.json").write_text("not json", encoding="utf-8")
    assert load_identity(sr) is None


def test_resolve_precedence_full_chain(tmp_path: Path, monkeypatch) -> None:
    """explicit > env > cache > gh > fallback — verify all in one chain."""
    sr = _seed(tmp_path)

    # No cache, no env, no gh → fallback
    monkeypatch.delenv("WOOWA_LEARNER_LOGIN", raising=False)
    assert resolve_learner_login(sr, auto_detect=False) == DEFAULT_FALLBACK

    # Add cache → cache wins
    (sr / "learner" / "identity.json").write_text(
        json.dumps({"github_login": "from_cache"}), encoding="utf-8")
    assert resolve_learner_login(sr) == "from_cache"

    # Add env → env beats cache
    monkeypatch.setenv("WOOWA_LEARNER_LOGIN", "from_env")
    assert resolve_learner_login(sr) == "from_env"

    # Explicit beats all
    assert resolve_learner_login(sr, explicit="from_explicit") == "from_explicit"
