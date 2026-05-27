"""Phase T7: session-start — orchestrator wrapping
  assess-learner-state → profile-recompute → daemon ping → ask

Wall-clock targets (per plan):
  cold session (no recent assess) p95 ≤ 50s
  warm session (assess cached <1h) p95 ≤ 800ms

Public API:
  start_session(repo, prompt, mission_path, ...) -> dict
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from core.learner_state import assess_learner_state
from core.profile import recompute_profile
from core.state import DEFAULT_STATE_ROOT

ASSESS_CACHE_SECS = 3600.0    # 1 hour — warm path threshold
PROFILE_CACHE_SECS = 600.0     # 10 minutes — quick re-derive
DAEMON_TIMEOUT = 30


def _ask_via_daemon(prompt: str, repo: str | None, learner_id: str,
                     state_root: Path) -> tuple[dict | None, str | None]:
    sock_path = state_root / "rag-daemon.sock"
    if not sock_path.exists():
        return None, "daemon down"
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_TIMEOUT)
        sock.connect(str(sock_path))
        req = {
            "action": "ask",
            "query": prompt,
            "learner_id": learner_id,
            "mode": os.environ.get("WOOWA_SESSION_MODE", "learning"),
        }
        if repo:
            req["repo"] = repo
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while True:
            c = sock.recv(65536)
            if not c:
                break
            data += c
        sock.close()
        return json.loads(data.decode("utf-8").strip()), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:120]


def _assess_cache_fresh(repo: str, state_root: Path, now: float) -> bool:
    p = state_root / "repos" / repo / "contexts" / "learner-state.json"
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return (now - float(data.get("computed_at") or 0)) < ASSESS_CACHE_SECS
    except Exception:
        return False


def _profile_cache_fresh(state_root: Path, now: float) -> bool:
    p = state_root / "learner" / "profile.json"
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return (now - float(data.get("computed_at") or 0)) < PROFILE_CACHE_SECS
    except Exception:
        return False


def start_session(
    repo: str | None,
    prompt: str,
    mission_path: Path | None = None,
    *,
    learner_id: str = "default",
    learner_login: str | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
    context: str = "auto",
    force_refresh: bool = False,
    now: float | None = None,
) -> dict:
    """Orchestrate cold or warm boot, then ask.

    learner_login defaults to auto-detection via core.identity (gh CLI + cache).

    Returns: {boot_type, assess_ran, profile_ran, ask_ok, ask_latency_ms,
              markdown, mode, errors[...]}
    """
    from core.identity import resolve_learner_login
    if learner_login is None:
        learner_login = resolve_learner_login(state_root)
    ts = now or time.time()
    errors: list[str] = []
    boot_type = "warm"
    assess_ran = False
    profile_ran = False

    # 1. assess-learner-state (skip if cached + not forced)
    if repo and mission_path and (force_refresh or
                                    not _assess_cache_fresh(repo, state_root, ts)):
        assess_ran = True
        boot_type = "cold"
        try:
            assess_learner_state(
                repo=repo, mission_path=mission_path,
                state_root=state_root, learner_login=learner_login,
                budget_secs=60.0,
            )
        except Exception as e:
            errors.append(f"assess: {type(e).__name__}: {e}"[:120])

    # 2. profile-recompute (skip if cached + not forced)
    if force_refresh or not _profile_cache_fresh(state_root, ts):
        profile_ran = True
        boot_type = "cold" if boot_type == "cold" else "warm-profile-refresh"
        try:
            recompute_profile(learner_id, state_root=state_root, now=ts)
        except Exception as e:
            errors.append(f"profile: {type(e).__name__}: {e}"[:120])

    # 3. ask
    t0 = time.perf_counter()
    response, ask_err = _ask_via_daemon(prompt, repo, learner_id, state_root)
    ask_ms = (time.perf_counter() - t0) * 1000
    if ask_err:
        errors.append(f"ask: {ask_err}")

    return {
        "boot_type": boot_type,
        "assess_ran": assess_ran,
        "profile_ran": profile_ran,
        "ask_ok": response is not None,
        "ask_latency_ms": round(ask_ms, 1),
        "mode": (response or {}).get("mode"),
        "markdown": (response or {}).get("markdown", ""),
        "event_id": (response or {}).get("event_id"),
        "context_requested": context,
        "errors": errors,
    }
