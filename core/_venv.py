"""Transparent .venv re-exec for repo bin entry points.

A fresh clone runs ``bin/setup``, which creates ``.venv/`` and installs the
runtime deps there. macOS Homebrew / Debian system Python is PEP 668
"externally-managed" and refuses ``pip install``, so the deps land in the
venv, not the system interpreter. Without this shim a bin launched under the
dep-less system Python would fail with ModuleNotFoundError.

So when a bin is launched under a non-venv interpreter and a repo-local
``.venv`` exists, re-exec into ``.venv/bin/python``. It is a no-op when there
is no ``.venv`` (a system-Python setup with deps already installed is left
untouched) or when already running inside any venv. Gated to argv[0] living
under ``bin/`` so importing these packages from pytest, a REPL, or another
tool never re-execs. Set ``WOOWA_NO_VENV_REEXEC=1`` to disable entirely.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN_DIR = os.path.realpath(os.path.join(_REPO_ROOT, "bin"))
_VENV_PY = os.path.join(_REPO_ROOT, ".venv", "bin", "python")


def maybe_reexec() -> None:
    try:
        if os.environ.get("WOOWA_NO_VENV_REEXEC"):
            return
        # Already inside a venv (our .venv or any other) — trust it.
        if sys.prefix != sys.base_prefix:
            return
        if not os.path.exists(_VENV_PY):
            return
        argv0 = sys.argv[0] if sys.argv else ""
        if not argv0:
            return
        # Only re-exec when launched as a repo bin script, so importing these
        # packages from pytest / a REPL / another tool never re-execs.
        if os.path.dirname(os.path.realpath(argv0)) != _BIN_DIR:
            return
        os.execv(_VENV_PY, [_VENV_PY] + sys.argv)
    except Exception:
        # A bootstrap shim must never break an otherwise-working interpreter.
        return
