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


def test_rag_ask_event_records_raw_utterance_and_mode_source() -> None:
    src = inspect.getsource(daemon.serve)
    # Part A: raw learner utterance logged alongside the session-rewritten prompt
    assert '"raw_utterance": req.get("raw_utterance") or None' in src
    # Part B: mode provenance resolved from request, recorded on the event
    assert 'event_mode_source = req.get("mode_source")' in src
    assert '"mode_source": event_mode_source' in src


def test_render_ask_stdout_matches_cli_text_shape() -> None:
    text = daemon._render_ask_stdout({
        "markdown": "# prompt",
        "mode": "cs_qa",
        "budget": 4500,
        "personas": ["mentor"],
        "reason": "unit",
        # Mix of a session-facing key (citation_markdown) and telemetry-only keys
        # (citation_paths / citation_trace) the warm path must now slim away.
        "response_hints": {
            "citation_markdown": "참고:\n- spring/bean",
            "citation_paths": ["spring/bean"],
            "citation_trace": [{"id": "bean", "score": 0.9}],
        },
        "response_quality_hint": {"source_event_id": "ask-1"},
    }, json_route=True)

    assert text.startswith("# RouteDecision: ")
    assert "# response_hints: " in text
    assert "# response_quality_hint: " in text
    assert text.endswith("# prompt\n")
    # The warm path slims to the session subset — telemetry arrays must not leak.
    assert "citation_markdown" in text
    assert "citation_paths" not in text
    assert "citation_trace" not in text


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


def test_is_capturable_learning_turn_gates_on_raw_utterance() -> None:
    """Pending capture is created only for a genuine learning turn (learning mode
    + real learner utterance). Bare probes / multi-intent sub-asks (no
    raw_utterance) and dev-mode asks must be excluded — else the Stop hook's
    latest-pending attach absorbs the session's next unrelated message."""
    f = daemon._is_capturable_learning_turn
    # Genuine learning turn — captured.
    assert f("learning", "빈 등록이 뭐야") is True
    # Bare probe in (default) learning mode but no --raw-utterance — NOT captured.
    assert f("learning", None) is False
    assert f("learning", "") is False
    # Dev-mode asks — NOT captured regardless of utterance.
    assert f("development", "트랜잭션 격리") is False
    assert f("development", None) is False
