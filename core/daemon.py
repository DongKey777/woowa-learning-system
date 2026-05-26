"""BGE-M3 keep-alive daemon (cold 7-8s → warm <2s).

AF_UNIX socket + line-delimited JSON protocol. Single-learner = single-thread.
~100 LOC, no extra deps.

Protocol:
  request:  {"action": "search", "query": "...", "top_k": 5, "rerank": true}
  response: {"hits": [{"concept_id": ..., "score": ..., ...}, ...]}
  request:  {"action": "ping"}
  response: {"alive": true, "ts": ...}

Client API (used by lazy_loader):
  hits = daemon_client.search(query, top_k=5)  # returns [] if daemon down
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
DAEMON_TIMEOUT = 30  # seconds per request
PID_FILE = "rag-daemon.pid"
SOCKET_FILE = "rag-daemon.sock"
LISTEN_BACKLOG = 32
DEFAULT_LOG_PATH = Path("/tmp/daemon.log")


# ── client API ────────────────────────────────────────────────────────────


def _socket_path(state_root: Path) -> Path:
    return state_root / SOCKET_FILE


def _pid_path(state_root: Path) -> Path:
    return state_root / PID_FILE


def search(
    query: str,
    top_k: int = 5,
    relations_expand: int = 3,
    state_root: Path = DEFAULT_STATE_ROOT,
    learner_id: str | None = None,
) -> list[dict] | None:
    """Returns hits via daemon. None if daemon unavailable (caller falls back)."""
    sp = _socket_path(state_root)
    if not sp.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_TIMEOUT)
        sock.connect(str(sp))
        req_payload = {
            "action": "search",
            "query": query,
            "top_k": top_k,
            "relations_expand": relations_expand,
        }
        if learner_id:
            req_payload["learner_id"] = learner_id
        req = json.dumps(req_payload) + "\n"
        sock.sendall(req.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        resp = json.loads(data.decode("utf-8").strip())
        return resp.get("hits", [])
    except (OSError, json.JSONDecodeError):
        return None


def ask(
    prompt: str,
    repo: str | None = None,
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict | None:
    """Full ask pipeline via daemon. Returns None if daemon unavailable.

    Reads until socket close (server closes after sendall) — JSON contains
    raw \\n in escaped form, so newline-delimited not safe for large payloads.
    """
    sp = _socket_path(state_root)
    if not sp.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_TIMEOUT)
        sock.connect(str(sp))
        req = json.dumps({"action": "ask", "query": prompt, "repo": repo,
                          "learner_id": learner_id}) + "\n"
        sock.sendall(req.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)  # signal "done writing" so server can detect end
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        return json.loads(data.decode("utf-8").strip())
    except (OSError, json.JSONDecodeError):
        return None


def ping(state_root: Path = DEFAULT_STATE_ROOT) -> bool:
    sp = _socket_path(state_root)
    if not sp.exists():
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(str(sp))
        sock.sendall(b'{"action":"ping"}\n')
        data = sock.recv(1024)
        sock.close()
        return json.loads(data.decode("utf-8")).get("alive") is True
    except (OSError, json.JSONDecodeError):
        return False


def stop(state_root: Path = DEFAULT_STATE_ROOT) -> bool:
    pid_p = _pid_path(state_root)
    sp = _socket_path(state_root)
    if not pid_p.exists():
        return False
    pid = int(pid_p.read_text())
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        pass
    pid_p.unlink(missing_ok=True)
    sp.unlink(missing_ok=True)
    return True


def start_background(
    state_root: Path = DEFAULT_STATE_ROOT,
    log_path: Path = DEFAULT_LOG_PATH,
    timeout_s: float = 90.0,
) -> bool:
    """Start daemon in a detached process and wait until ping succeeds."""
    if ping(state_root):
        return True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "bin" / "rag-daemon"),
        "start",
        "--state-root",
        str(state_root),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if ping(state_root):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.25)
    return False


# ── server (daemon) ───────────────────────────────────────────────────────


def serve(state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """Daemon main loop. Loads BGE-M3 once, serves search + ask requests forever."""
    sp = _socket_path(state_root)
    pid_p = _pid_path(state_root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.unlink(missing_ok=True)

    pid_p.write_text(str(os.getpid()))

    # Pre-load encoder + corpus + ask pipeline
    from rag.search import search as rag_search
    from rag.corpus_loader import load_corpus
    from rag.encoder import encode_query
    from core.coach import compose
    from core.lexical_fusion import make_lexical_fusion_fn
    from core.lazy_loader import (
        _hits_to_dicts,
        _personalize_hits,
        load as lazy_load,
    )
    from core.router import route
    from core.state import append_history_event, load_profile, read_history
    corpus = load_corpus(strict=True)
    lexical_fns: dict[int, object] = {}

    def _lexical_fn(loaded_corpus, expand: int):
        if expand <= 0:
            return None
        if expand not in lexical_fns:
            lexical_fns[expand] = make_lexical_fusion_fn(loaded_corpus, expand=expand)
        return lexical_fns[expand]

    _ = encode_query("warm-up")
    print(f"[daemon] ready (pid={os.getpid()}, socket={sp})", flush=True)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sp))
    # bin/ask clients may arrive in small bursts from AI sessions or tests.
    # A larger backlog keeps those clients queued instead of forcing a cold
    # in-process fallback that lacks daemon-only telemetry hints.
    srv.listen(LISTEN_BACKLOG)
    try:
        while True:
            conn, _ = srv.accept()
            try:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    conn.close()
                    continue
                req = json.loads(data.decode("utf-8").strip())
                action = req.get("action")
                if action == "ping":
                    conn.sendall(json.dumps({"alive": True, "ts": time.time()}).encode() + b"\n")
                elif action == "search":
                    hits = rag_search(
                        req["query"],
                        top_k=req.get("top_k", 5),
                        relations_expand=req.get("relations_expand", 3),
                        rerank_fn=_lexical_fn(corpus, req.get("lexical_expand", 8)),
                        corpus=corpus,
                    )
                    personalization = None
                    if req.get("learner_id"):
                        profile = load_profile(req["learner_id"], state_root=state_root)
                        hits, personalization = _personalize_hits(hits, profile)
                    payload = {"hits": _hits_to_dicts(hits)}
                    if personalization is not None:
                        payload["personalization"] = personalization
                    conn.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
                elif action == "ask":
                    # Full ask pipeline in daemon — bin/ask becomes thin socket client
                    prompt = req["query"]
                    repo = req.get("repo")
                    learner_id = req.get("learner_id", "default")
                    profile = load_profile(learner_id, state_root=state_root)
                    decision = route(
                        prompt, repo=repo,
                        pending_self_assessment=profile.pending_triggers.get("self_assessment"),
                        pending_drill=profile.pending_triggers.get("review_drill"),
                    )
                    artifacts = lazy_load(
                        decision,
                        repo=repo,
                        state_root=state_root,
                        query=prompt,
                        corpus=corpus,
                        learner_profile=profile,
                    )
                    # Drill mode integration: if router dispatched drill and
                    # there's no pending offer yet, try to build one from the
                    # learner's uncertain concepts so the AI session has a
                    # concrete question to present.
                    if decision.mode == "drill":
                        from core.drill import build_offer_if_due
                        uncertain = profile.uncertain_concepts or []
                        offer = build_offer_if_due(
                            learner_id=learner_id, state_root=state_root,
                            uncertain_concept_ids=uncertain,
                        )
                        if offer:
                            artifacts["drill_offer"] = {
                                "concept_id": offer.concept_id,
                                "question": offer.question,
                                "expected_terms": offer.expected_terms,
                                "source": offer.source,
                            }
                    if req.get("reformulated_query"):
                        artifacts["reformulated_query"] = req["reformulated_query"]
                    recent = read_history(state_root=state_root, tail=20)
                    # Phase Y11 F8: event_id is generated BEFORE compose so the
                    # response_quality_hint.command_template can embed it and
                    # the AI session's follow-up wrapper call joins on the
                    # same id as the history event.
                    event_id = f"ask-{int(time.time() * 1000)}-{os.getpid()}"
                    # Phase Y11 P1.2 — derive learner_context from v3 profile
                    # (mastered + proficient → must_skip_explanations_of).
                    # Caller-provided learner_context wins if given.
                    derived_context = req.get("learner_context")
                    if derived_context is None:
                        skip_targets = sorted(
                            set(profile.mastered_concepts)
                            | set(getattr(profile, "proficient_concepts", []))
                        )
                        if skip_targets:
                            derived_context = {
                                "must_skip_explanations_of": skip_targets,
                            }
                    markdown, response_hints, response_quality_hint, effective_route = compose(
                        decision, artifacts, prompt,
                        repo=repo, learner_id=learner_id, recent_history=recent,
                        source_event_id=event_id,
                        state_root=state_root,
                        learner_context=derived_context,
                    )
                    # Append turn event with effective_route (post-downgrade) so
                    # downstream telemetry (response-quality-mine, routing-analyze)
                    # sees the same mode the AI session was instructed to answer in.
                    event_mode = req.get("mode") or os.environ.get(
                        "WOOWA_SESSION_MODE", "learning")
                    event = {
                        "event_id": event_id,
                        "ts": time.time(),
                        "event_type": "rag_ask",
                        "mode": event_mode,
                        "payload": {
                            "prompt": prompt,
                            "repo": repo,
                            "router_mode": effective_route.mode,
                            "router_reason": effective_route.reason,
                            # I2: history aligns with hints (downgrade → []).
                            "top_concept_ids": list(response_hints["citation_paths"]),
                        },
                    }
                    try:
                        append_history_event(event, state_root=state_root)
                    except Exception:  # noqa: BLE001
                        pass  # history append must not break the response
                    payload = {
                        "markdown": markdown,
                        "mode": effective_route.mode,
                        "budget": effective_route.budget_tokens,
                        "personas": effective_route.personas,
                        "reason": effective_route.reason,
                        "event_id": event_id,
                        "response_hints": response_hints,
                        "response_quality_hint": response_quality_hint,
                    }
                    conn.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
                else:
                    conn.sendall(json.dumps({"error": f"unknown action {action}"}).encode() + b"\n")
            except Exception as exc:  # noqa: BLE001
                conn.sendall(json.dumps({"error": str(exc)}).encode() + b"\n")
            finally:
                conn.close()
    finally:
        srv.close()
        sp.unlink(missing_ok=True)
        pid_p.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "start-bg", "stop", "ping", "status"))
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--timeout-s", type=float, default=90.0)
    args = parser.parse_args(argv)
    if args.action == "start":
        serve(state_root=args.state_root)
    elif args.action == "start-bg":
        ok = start_background(
            state_root=args.state_root,
            log_path=args.log_path,
            timeout_s=args.timeout_s,
        )
        print("started" if ok else f"not ready; see {args.log_path}")
        return 0 if ok else 1
    elif args.action == "stop":
        print("stopped" if stop(args.state_root) else "not running")
    elif args.action == "ping":
        print("alive" if ping(args.state_root) else "no response")
    elif args.action == "status":
        sp = _socket_path(args.state_root)
        print(f"socket exists: {sp.exists()}, ping: {ping(args.state_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
