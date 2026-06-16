"""CLI test for bin/learn-drill (W6: no_offer exit code)."""
from __future__ import annotations

import importlib.machinery as machinery
import importlib.util as util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_cli():
    loader = machinery.SourceFileLoader(
        "learn_drill_cli", str(REPO_ROOT / "bin" / "learn-drill")
    )
    spec = util.spec_from_loader("learn_drill_cli", loader)
    mod = util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_offer_no_due_exits_zero(tmp_path: Path) -> None:
    # W6: "nothing due" is a normal outcome -> exit 0 (not 1).
    mod = _load_cli()
    (tmp_path / "learner").mkdir(parents=True)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.main(["--state-root", str(tmp_path), "offer"])
    assert rc == 0
    assert json.loads(out.getvalue())["no_offer"] is True
