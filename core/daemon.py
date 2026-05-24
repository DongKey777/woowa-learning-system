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
import sys
import time
from pathlib import Path

DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
DAEMON_TIMEOUT = 30  # seconds per request
PID_FILE = "rag-daemon.pid"
SOCKET_FILE = "rag-daemon.sock"


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
) -> list[dict] | None:
    """Returns hits via daemon. None if daemon unavailable (caller falls back)."""
    sp = _socket_path(state_root)
    if not sp.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_TIMEOUT)
        sock.connect(str(sp))
        req = json.dumps({"action": "search", "query": query, "top_k": top_k,
                          "relations_expand": relations_expand}) + "\n"
        sock.sendall(req.encode("utf-8"))
        # read until newline
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(8192)
            if not chunk:
                break
            data += chunk
        sock.close()
        resp = json.loads(data.decode("utf-8").strip())
        return resp.get("hits", [])
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


# ── server (daemon) ───────────────────────────────────────────────────────


def serve(state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """Daemon main loop. Loads BGE-M3 once, serves search requests forever."""
    sp = _socket_path(state_root)
    pid_p = _pid_path(state_root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.unlink(missing_ok=True)

    pid_p.write_text(str(os.getpid()))

    # Pre-load encoder + corpus
    from rag.search import search as rag_search
    from rag.corpus_loader import load_corpus
    corpus = load_corpus(strict=True)
    # warm encoder
    from rag.encoder import encode_query
    _ = encode_query("warm-up")
    print(f"[daemon] ready (pid={os.getpid()}, socket={sp})", flush=True)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sp))
    srv.listen(1)
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
                        corpus=corpus,
                    )
                    payload = {"hits": [{
                        "concept_id": h.concept_id, "score": round(h.score, 4),
                        "category": h.category, "title": h.title, "source": h.source,
                    } for h in hits]}
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
    parser.add_argument("action", choices=("start", "stop", "ping", "status"))
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    args = parser.parse_args(argv)
    if args.action == "start":
        serve(state_root=args.state_root)
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
