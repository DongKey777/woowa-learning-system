from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_next_action_survives_array_form_json(tmp_path: Path) -> None:
    # A corrupt array-form state file must not crash next-action (.get on a list).
    learner = tmp_path / "state" / "learner"
    learner.mkdir(parents=True)
    (learner / "pending_triggers.json").write_text(json.dumps(["corrupt", "array"]))
    (learner / "profile.json").write_text(json.dumps(["also", "wrong"]))
    proc = subprocess.run(
        [sys.executable, "bin/next-action", "--state-root", str(tmp_path / "state"),
         "--learner-id", "x"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
