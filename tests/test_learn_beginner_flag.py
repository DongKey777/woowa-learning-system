"""CLI test for bin/learn-beginner-flag (W7)."""
from __future__ import annotations

import importlib.machinery as machinery
import importlib.util as util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load():
    ld = machinery.SourceFileLoader("lbf", str(REPO_ROOT / "bin" / "learn-beginner-flag"))
    sp = util.spec_from_loader("lbf", ld)
    m = util.module_from_spec(sp)
    ld.exec_module(m)
    return m


def test_flag_then_clear(tmp_path: Path) -> None:
    from core.state import load_beginner_flags
    m = _load()
    assert m.main(["--concept", "spring/x", "--state-root", str(tmp_path), "--silent"]) == 0
    assert load_beginner_flags(tmp_path) == ["spring/x"]
    # idempotent add
    assert m.main(["--concept", "spring/x", "--state-root", str(tmp_path), "--silent"]) == 0
    assert load_beginner_flags(tmp_path) == ["spring/x"]
    # clear
    assert m.main(["--concept", "spring/x", "--clear", "--state-root", str(tmp_path), "--silent"]) == 0
    assert load_beginner_flags(tmp_path) == []


def test_requires_concept_or_list(tmp_path: Path) -> None:
    m = _load()
    assert m.main(["--state-root", str(tmp_path), "--silent"]) == 2  # no --concept/--list
