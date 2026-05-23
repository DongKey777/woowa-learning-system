"""Tests for bin/eval-compare end-to-end (skip-legacy, no ML needed)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_eval_compare_writes_batch_and_prompts(tmp_path: Path) -> None:
    """Use tool_only queries to avoid BGE-M3 encoder download in test path."""
    query_set = tmp_path / "queries.jsonl"
    query_set.write_text(
        "\n".join([
            json.dumps({"query": "gradle 빌드 어떻게"}),
            json.dumps({"query": "git rebase 충돌 어떻게"}),
        ]) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "batch" / "result.json"

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "eval-compare"),
         "--query-set", str(query_set),
         "--out", str(out),
         "--skip-legacy"],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO_ROOT)},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()

    data = json.loads(out.read_text())
    assert len(data["samples"]) == 2  # skip-legacy = next-only
    assert data["samples"][0]["sample_id"].endswith("-next")
    assert data["samples"][0]["query"] == "gradle 빌드 어떻게"
    assert data["judgments"] == []

    prompts = out.with_suffix(".prompts.md").read_text()
    assert "gradle" in prompts
    assert "rebase" in prompts
    assert "equivalent" in prompts

    runs = json.loads(out.with_suffix(".runs.json").read_text())
    assert len(runs) == 2
    assert runs[0]["next"]["returncode"] == 0
    assert runs[0]["legacy"] is None


def test_eval_compare_supports_legacy_skip_when_path_missing(tmp_path: Path) -> None:
    query_set = tmp_path / "queries.jsonl"
    query_set.write_text(json.dumps({"query": "gradle daemon stop"}) + "\n", encoding="utf-8")
    out = tmp_path / "result.json"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "eval-compare"),
         "--query-set", str(query_set),
         "--out", str(out),
         "--legacy-hub", str(tmp_path / "nonexistent")],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO_ROOT)},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    runs = json.loads(out.with_suffix(".runs.json").read_text())
    assert runs[0]["legacy"]["returncode"] == -1
    assert "not found" in runs[0]["legacy"]["stderr"]
