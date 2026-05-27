"""Phase Y11 PR-D — `bin/ask "..." --json` v2-native machine-readable mode.

This is NOT a legacy bin/rag-ask shim — v2 explicitly chose not to maintain
CLI byte-level compatibility (plan §G2/Out of scope). `--json` is purely a
machine-readable mode for tests/CI/automation.

Default (no --json) keeps emitting markdown + `# response_hints:` /
`# response_quality_hint:` comment lines for AI sessions.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASK = REPO_ROOT / "bin" / "ask"


def _run_ask(*args: str) -> tuple[str, str, int]:
    """Always use --no-daemon so tests are deterministic (no socket / no
    history side effects). cold path still emits response_hints; only
    response_quality_hint is None per F8/I4."""
    proc = subprocess.run(
        [sys.executable, str(ASK), *args, "--no-daemon"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.stdout, proc.stderr, proc.returncode


def test_json_mode_emits_single_json_object():
    out, err, rc = _run_ask("DI가 뭐야?", "--json")
    assert rc == 0, err
    assert out.count("\n") <= 2, "json mode must not mix multi-line markdown"
    payload = json.loads(out)
    assert payload["mode"] == "cs_qa"
    assert "markdown" in payload
    assert "response_hints" in payload
    # cold path → no response_quality_hint
    assert payload["response_quality_hint"] is None


def test_json_mode_contains_response_hints_citation():
    out, _, rc = _run_ask("DI가 뭐야?", "--json")
    assert rc == 0
    payload = json.loads(out)
    hints = payload["response_hints"]
    assert hints["citation_markdown"]  # paste-ready
    assert hints["citation_paths"]      # at least one cid
    assert hints["tier_downgrade"] is None


def test_json_mode_tier_0_fallback_for_non_cs():
    out, _, rc = _run_ask("오늘 날씨 어때", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["mode"] == "tier_0_fallback"
    hints = payload["response_hints"]
    assert hints["citation_markdown"] is None
    assert hints["fallback_disclaimer"]
    assert hints["tier_downgrade"]


def test_default_mode_keeps_markdown_with_comments():
    """Without --json, AI session reads markdown + leading `# response_hints:`
    comment line. This is the everyday format — not JSON-only."""
    out, _, rc = _run_ask("DI가 뭐야?")
    assert rc == 0
    lines = out.splitlines()
    assert any(l.startswith("# response_hints: ") for l in lines)
    assert any("Woowa Learning Hub v2" in l for l in lines)
    # JSON one-line mode would not include the markdown header.


def test_reformulated_query_flows_into_hints():
    out, _, rc = _run_ask(
        "그게 뭐야",
        "--reformulated-query", "DI가 뭐야?",
        "--json",
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["mode"] == "cs_qa"
    assert payload["response_hints"]["reformulated_query"] == "DI가 뭐야?"
    assert payload["response_hints"]["tier_downgrade"] is None
    assert payload["response_hints"]["citation_paths"]
