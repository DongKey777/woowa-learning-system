"""Learner identity — auto-detected from gh CLI on first use, cached in
state/learner/identity.json. All wrappers consult resolve_learner_login()
instead of hardcoding `DongKey777`.

Resolve precedence (first non-None wins):
  1. Explicit CLI flag (--learner-login)
  2. WOOWA_LEARNER_LOGIN env var
  3. state/learner/identity.json `github_login`
  4. `gh api user --jq .login` (auto-detect + cache)
  5. fallback "default" + warning

Public API:
  resolve_learner_login(state_root, explicit=None) -> str
  detect_and_cache(state_root, force_refresh=False) -> dict | None
  load_identity(state_root) -> dict | None
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

DEFAULT_FALLBACK = "default"
IDENTITY_FILE = "identity.json"


def _identity_path(state_root: Path) -> Path:
    return state_root / "learner" / IDENTITY_FILE


def load_identity(state_root: Path) -> dict | None:
    p = _identity_path(state_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _detect_from_gh() -> tuple[str | None, str | None]:
    """Return (login, source) via `gh api user`. None if gh unavailable."""
    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", "{login, email}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None, None
        data = json.loads(r.stdout.strip())
        login = data.get("login")
        return (login, "gh_cli") if login else (None, None)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None, None


def _detect_from_git() -> tuple[str | None, str | None]:
    """git config user.email fallback (legacy, weak signal)."""
    try:
        r = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True, timeout=5,
        )
        email = (r.stdout or "").strip()
        if email and "@" in email:
            # Use email prefix as weak login hint
            return email.split("@")[0], "git_config_fallback"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None, None


def detect_and_cache(state_root: Path, force_refresh: bool = False) -> dict | None:
    """Detect learner identity (gh→git fallback) and write identity.json.

    Skips refresh if cache exists and not force_refresh. Returns cached/new
    identity dict or None on detection failure.
    """
    if not force_refresh:
        existing = load_identity(state_root)
        if existing and existing.get("github_login"):
            return existing

    login, source = _detect_from_gh()
    if not login:
        login, source = _detect_from_git()
    if not login:
        return None

    record = {
        "github_login": login,
        "learner_id": login,  # paradigm-v2 internal id = github login
        "source": source,
        "detected_at": time.time(),
    }
    p = _identity_path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def resolve_learner_login(
    state_root: Path,
    explicit: str | None = None,
    auto_detect: bool = True,
) -> str:
    """Return effective learner_login per precedence above."""
    if explicit:
        return explicit
    env = os.environ.get("WOOWA_LEARNER_LOGIN")
    if env:
        return env
    cached = load_identity(state_root)
    if cached and cached.get("github_login"):
        return cached["github_login"]
    if auto_detect:
        detected = detect_and_cache(state_root)
        if detected and detected.get("github_login"):
            return detected["github_login"]
    return DEFAULT_FALLBACK
