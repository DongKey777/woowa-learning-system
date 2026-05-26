"""Unit tests for scripts/mining/jsonl_iter — Phase Y8."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.mining.jsonl_iter import iter_jsonl, sample_jsonl  # noqa: E402


def _seed(tmp_path: Path) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join([
        json.dumps({"event_type": "rag_ask", "mode": "learning", "ts": 100}),
        json.dumps({"event_type": "rag_ask", "mode": "development", "ts": 200}),
        json.dumps({"event_type": "code_attempt", "mode": "learning", "ts": 300}),
        json.dumps({"event_type": "drill_answer", "mode": "learning", "ts": 400}),
        "",  # blank line — should be skipped
        "not json — should be skipped",
        json.dumps({"event_type": "rag_ask", "mode": "learning", "ts": 500}),
    ]) + "\n", encoding="utf-8")
    return p


def test_iter_jsonl_basic_yield_all_valid(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    events = list(iter_jsonl(p))
    assert len(events) == 5  # 5 valid (blank + bad skipped)


def test_iter_jsonl_event_type_filter(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    rag = list(iter_jsonl(p, event_type="rag_ask"))
    assert len(rag) == 3


def test_iter_jsonl_mode_filter(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    learning = list(iter_jsonl(p, mode="learning"))
    assert len(learning) == 4
    assert all(e["mode"] == "learning" for e in learning)


def test_iter_jsonl_since_ts(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    recent = list(iter_jsonl(p, since_ts=250))
    assert len(recent) == 3
    assert all(e["ts"] >= 250 for e in recent)


def test_iter_jsonl_limit(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    capped = list(iter_jsonl(p, limit=2))
    assert len(capped) == 2


def test_iter_jsonl_missing_returns_empty(tmp_path: Path) -> None:
    assert list(iter_jsonl(tmp_path / "missing.jsonl")) == []


def test_sample_jsonl_returns_n_or_all(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    s3 = sample_jsonl(p, n=3, seed=42)
    assert len(s3) == 3
    s100 = sample_jsonl(p, n=100, seed=42)
    assert len(s100) == 5  # all valid events


def test_iter_jsonl_combined_filters(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    out = list(iter_jsonl(p, event_type="rag_ask", mode="learning", since_ts=300))
    assert len(out) == 1
    assert out[0]["ts"] == 500
