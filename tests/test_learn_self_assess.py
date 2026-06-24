"""CLI tests for bin/learn-self-assess (W4: score normalization + bounds).

First dedicated tests for this CLI — previously a MEDIUM coverage gap.
"""
from __future__ import annotations

import importlib.machinery as machinery
import importlib.util as util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_cli():
    loader = machinery.SourceFileLoader(
        "learn_self_assess_cli", str(REPO_ROOT / "bin" / "learn-self-assess")
    )
    spec = util.spec_from_loader("learn_self_assess_cli", loader)
    mod = util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _seed_pending(state_root: Path, trigger_session_id: str = "s1") -> None:
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pending_triggers.json").write_text(json.dumps({
        "self_assessment": {
            "trigger_session_id": trigger_session_id,
            "payload": {"concept_ids": ["spring/di"]},
        }
    }), encoding="utf-8")


def _run(mod, args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mod.main(args)
    return rc, out.getvalue(), err.getvalue()


def test_score_1_of_10_normalizes_to_point_1(tmp_path: Path) -> None:
    # The bug: "1 out of 10" (--score 1.0) used to store 1.0 (perfect).
    mod = _load_cli()
    _seed_pending(tmp_path)
    rc, out, _ = _run(mod, [
        "--trigger-session-id", "s1", "--score", "1.0",
        "--state-root", str(tmp_path), "--learner-id", "testuser",
    ])
    assert rc == 0
    assert json.loads(out)["score_normalized"] == 0.1


def test_score_8_normalizes_to_point_8(tmp_path: Path) -> None:
    mod = _load_cli()
    _seed_pending(tmp_path)
    rc, out, _ = _run(mod, [
        "--trigger-session-id", "s1", "--score", "8",
        "--state-root", str(tmp_path), "--learner-id", "testuser",
    ])
    assert rc == 0
    assert json.loads(out)["score_normalized"] == 0.8


def test_out_of_range_score_rejected(tmp_path: Path) -> None:
    mod = _load_cli()
    _seed_pending(tmp_path)
    rc, _, err = _run(mod, [
        "--trigger-session-id", "s1", "--score", "50",
        "--state-root", str(tmp_path), "--learner-id", "testuser",
    ])
    assert rc == 2
    assert "out of range" in err
    hist = tmp_path / "learner" / "history.jsonl"
    assert not hist.exists() or "self_assessment" not in hist.read_text(encoding="utf-8")


def test_no_pending_trigger_rejected_regardless_of_score(tmp_path: Path) -> None:
    # v4 guard runs before score validation: no pending -> rc 1 even for a valid score.
    mod = _load_cli()
    (tmp_path / "learner").mkdir(parents=True)
    rc, _, err = _run(mod, [
        "--trigger-session-id", "nope", "--score", "8",
        "--state-root", str(tmp_path), "--learner-id", "testuser",
    ])
    assert rc == 1
    assert "no matching pending" in err


def test_self_assess_records_mastery_evidence(tmp_path: Path) -> None:
    # The canonical CLI must emit self_assess mastery evidence (record_turn), not
    # just append history — previously a self-assessment never became corroborating
    # evidence toward a promotion (diverged from the learn-event path).
    import sqlite3
    mod = _load_cli()
    _seed_pending(tmp_path)
    rc, _out, _ = _run(mod, [
        "--trigger-session-id", "s1", "--score", "8",
        "--state-root", str(tmp_path), "--learner-id", "testuser",
    ])
    assert rc == 0
    db = tmp_path / "learner" / "mastery_graph.sqlite"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT concept_id, source FROM evidence WHERE source='self_assess'").fetchall()
    conn.close()
    assert ("spring/di", "self_assess") in rows
