"""Unit tests for core/daemon.py — Phase Y8.

Daemon e2e is covered by Phase J/M/T benches (real socket). Here we test
pure-Python helpers without spawning a daemon: socket path / pid path
resolution + payload construction logic that doesn't require BGE-M3 load.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import daemon  # noqa: E402


def test_socket_path_resolution(tmp_path: Path) -> None:
    assert daemon._socket_path(tmp_path) == tmp_path / daemon.SOCKET_FILE


def test_pid_path_resolution(tmp_path: Path) -> None:
    assert daemon._pid_path(tmp_path) == tmp_path / daemon.PID_FILE


def test_search_returns_none_when_socket_missing(tmp_path: Path) -> None:
    # No socket file in tmp_path → search returns None gracefully
    result = daemon.search("test query", state_root=tmp_path)
    assert result is None


def test_ask_returns_none_when_socket_missing(tmp_path: Path) -> None:
    result = daemon.ask("test prompt", state_root=tmp_path)
    assert result is None


def test_module_exports() -> None:
    # Public API contract
    for name in ("search", "ask", "DEFAULT_STATE_ROOT", "SOCKET_FILE",
                 "PID_FILE", "DAEMON_TIMEOUT"):
        assert hasattr(daemon, name), f"missing public symbol: {name}"


def test_default_state_root_under_repo(tmp_path: Path) -> None:
    """DEFAULT_STATE_ROOT should be project-relative."""
    assert daemon.DEFAULT_STATE_ROOT.name == "state"
    assert daemon.DEFAULT_STATE_ROOT.parent.name == "woowa-learning-system"
