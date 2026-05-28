"""Unit tests for core/daemon.py — Phase Y8.

Daemon e2e is covered by Phase J/M/T benches (real socket). Here we test
pure-Python helpers without spawning a daemon: socket path / pid path
resolution + payload construction logic that doesn't require BGE-M3 load.
"""
from __future__ import annotations

import json
import inspect
import sys
from pathlib import Path
from unittest.mock import Mock

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


def test_search_can_request_learner_profile_personalization(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / daemon.SOCKET_FILE
    sock_path.touch()
    sent: list[bytes] = []

    class FakeSocket:
        def __init__(self):
            self._chunks = [b'{"hits": []}\n', b""]

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, path):
            self.path = path

        def sendall(self, data):
            sent.append(data)

        def shutdown(self, how):
            self.how = how

        def recv(self, size):
            return self._chunks.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(daemon.socket, "socket", lambda *args, **kwargs: FakeSocket())
    assert daemon.search("DI", state_root=tmp_path, learner_id="dk") == []
    payload = json.loads(sent[0].decode("utf-8"))
    assert payload["learner_id"] == "dk"


def test_ask_returns_none_when_socket_missing(tmp_path: Path) -> None:
    result = daemon.ask("test prompt", state_root=tmp_path)
    assert result is None


def test_rag_ask_event_records_learner_identity_and_repo() -> None:
    src = inspect.getsource(daemon.serve)
    assert '"event_type": "rag_ask"' in src
    assert "ask_started = time.perf_counter()" in src
    assert 'if learner_id == "default":' in src
    assert 'learner_id = resolve_learner_login(state_root)' in src
    assert '"learner_id": learner_id' in src
    assert '"repo": repo' in src
    assert '"top_concept_ids": list(response_hints["citation_paths"])' in src
    assert '"latency_ms": latency_ms' in src
    assert "from core.feedback import record_turn" in src
    assert "record_turn(event, state_root=state_root)" in src


def test_render_ask_stdout_matches_cli_text_shape() -> None:
    text = daemon._render_ask_stdout({
        "markdown": "# prompt",
        "mode": "cs_qa",
        "budget": 4500,
        "personas": ["mentor"],
        "reason": "unit",
        "response_hints": {"citation_paths": ["spring/bean"]},
        "response_quality_hint": {"source_event_id": "ask-1"},
    }, json_route=True)

    assert text.startswith("# RouteDecision: ")
    assert "# response_hints: " in text
    assert "# response_quality_hint: " in text
    assert text.endswith("# prompt\n")


def test_module_exports() -> None:
    # Public API contract
    for name in ("search", "ask", "DEFAULT_STATE_ROOT", "SOCKET_FILE",
                 "PID_FILE", "DAEMON_TIMEOUT", "LISTEN_BACKLOG",
                 "start_background"):
        assert hasattr(daemon, name), f"missing public symbol: {name}"


def test_listen_backlog_allows_small_bursts() -> None:
    assert daemon.LISTEN_BACKLOG >= 16


def test_default_encoder_warm_queries_include_smoke_prompt(monkeypatch) -> None:
    monkeypatch.delenv("WOOWA_DAEMON_ENCODER_WARM_QUERIES", raising=False)
    assert daemon._encoder_warm_queries() == ("DI",)


def test_encoder_warm_queries_env_override(monkeypatch) -> None:
    monkeypatch.setenv("WOOWA_DAEMON_ENCODER_WARM_QUERIES", " DI | Spring Bean ")
    assert daemon._encoder_warm_queries() == ("DI", "Spring Bean")


def test_encoder_import_prime_defaults_on_with_env_rollback(monkeypatch) -> None:
    monkeypatch.delenv("WOOWA_DAEMON_ENCODER_IMPORT_PRIME", raising=False)
    assert daemon._encoder_import_prime_enabled() is True

    monkeypatch.setenv("WOOWA_DAEMON_ENCODER_IMPORT_PRIME", "0")
    assert daemon._encoder_import_prime_enabled() is False


def test_parallel_startup_import_prime() -> None:
    daemon._prime_parallel_startup_imports()


def test_default_state_root_under_repo(tmp_path: Path) -> None:
    """DEFAULT_STATE_ROOT should be project-relative."""
    assert daemon.DEFAULT_STATE_ROOT.name == "state"
    assert daemon.DEFAULT_STATE_ROOT.parent == REPO_ROOT


def test_start_background_returns_true_when_already_running(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(daemon, "ping", lambda state_root: True)
    popen = Mock()
    monkeypatch.setattr(daemon.subprocess, "Popen", popen)
    assert daemon.start_background(state_root=tmp_path, log_path=tmp_path / "d.log", timeout_s=0.01) is True
    popen.assert_not_called()


def test_start_background_spawns_detached_process(monkeypatch, tmp_path: Path) -> None:
    calls = {"n": 0}

    def fake_ping(state_root):
        calls["n"] += 1
        return calls["n"] >= 2

    class FakeProc:
        def poll(self):
            return None

    monkeypatch.setattr(daemon, "ping", fake_ping)
    popen = Mock(return_value=FakeProc())
    monkeypatch.setattr(daemon.subprocess, "Popen", popen)
    assert daemon.start_background(state_root=tmp_path, log_path=tmp_path / "d.log", timeout_s=1) is True
    kwargs = popen.call_args.kwargs
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is daemon.subprocess.DEVNULL
