"""Unit tests for scripts/migration/mode_reclassify_backfill.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "migration" / "mode_reclassify_backfill.py"
spec = importlib.util.spec_from_file_location("mode_reclassify_backfill", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TARGET_ID = "ask-1780000000000-1-aaaaaa"
CONCEPT_ID = "ask-1780000000001-2-bbbbbb"
ALREADY_DEV_ID = "ask-1780000000002-3-cccccc"


def _write_fixture(state_root: Path) -> None:
    learner = state_root / "learner"
    learner.mkdir(parents=True)
    history = [
        # contaminated dev-ops turn mislabeled learning
        {"event_id": TARGET_ID, "ts": 1.0, "event_type": "rag_ask",
         "mode": "learning", "learner_id": "DongKey777", "repo": None,
         "payload": {"prompt": "gradle daemon stop", "top_concept_ids": []}},
        {"event_id": "rq-1", "ts": 1.1, "event_type": "response_quality",
         "mode": "learning", "learner_id": "DongKey777",
         "payload": {"source_event_id": TARGET_ID, "summary": "x"}},
        # genuine learning concept question — must NOT be touched
        {"event_id": CONCEPT_ID, "ts": 2.0, "event_type": "rag_ask",
         "mode": "learning", "learner_id": "DongKey777", "repo": None,
         "payload": {"prompt": "트랜잭션 전파가 뭐야", "top_concept_ids": []}},
        {"event_id": "rq-2", "ts": 2.1, "event_type": "response_quality",
         "mode": "learning", "learner_id": "DongKey777",
         "payload": {"source_event_id": CONCEPT_ID, "summary": "y"}},
        # already development (env was set) — untouched, not a candidate
        {"event_id": ALREADY_DEV_ID, "ts": 3.0, "event_type": "rag_ask",
         "mode": "development", "mode_source": "env", "learner_id": "DongKey777",
         "repo": None, "payload": {"prompt": "core/daemon.py 고치자", "top_concept_ids": []}},
    ]
    (learner / "history.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in history) + "\n",
        encoding="utf-8",
    )
    quality = [
        {"schema_id": "assistant-response-quality-v1", "source_event_id": TARGET_ID,
         "mode": "learning", "learner_id": "DongKey777", "quality_flags": []},
        {"schema_id": "assistant-response-quality-v1", "source_event_id": CONCEPT_ID,
         "mode": "learning", "learner_id": "DongKey777", "quality_flags": []},
    ]
    (learner / "response-quality.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in quality) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_scan_surfaces_only_devops_candidate(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    result = mod.scan(tmp_path)
    assert result["n"] == 1
    assert result["candidates"][0]["event_id"] == TARGET_ID
    assert result["candidates"][0]["matched_patterns"] == ["gradle-daemon-stop"]
    assert result["paired_response_quality_events"] == 1
    assert result["paired_quality_rows"] == 1
    # scan must not mutate
    hist = _read_jsonl(tmp_path / "learner" / "history.jsonl")
    assert all(e["mode"] == ("development" if e["event_id"] == ALREADY_DEV_ID else "learning")
               for e in hist if e.get("event_type") == "rag_ask")


def test_apply_flips_only_paired_target_rows(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    result = mod.apply(tmp_path, {TARGET_ID}, dry_run=False)
    assert result["flipped_total"] == 3  # rag_ask + rq event + quality row

    hist = {e.get("event_id") or e["payload"]["source_event_id"]: e
            for e in _read_jsonl(tmp_path / "learner" / "history.jsonl")}
    # target paired rows flipped with provenance
    assert hist[TARGET_ID]["mode"] == "development"
    assert hist[TARGET_ID]["mode_source"] == "backfill_reclassified"
    rq_target = next(e for e in _read_jsonl(tmp_path / "learner" / "history.jsonl")
                     if e.get("event_type") == "response_quality"
                     and e["payload"]["source_event_id"] == TARGET_ID)
    assert rq_target["mode"] == "development"
    assert rq_target["mode_source"] == "backfill_reclassified"
    # genuine concept question untouched
    assert hist[CONCEPT_ID]["mode"] == "learning"
    assert "mode_source" not in hist[CONCEPT_ID]
    # already-dev env event untouched (still env provenance, not backfill)
    assert hist[ALREADY_DEV_ID]["mode"] == "development"
    assert hist[ALREADY_DEV_ID]["mode_source"] == "env"

    quality = {r["source_event_id"]: r
               for r in _read_jsonl(tmp_path / "learner" / "response-quality.jsonl")}
    assert quality[TARGET_ID]["mode"] == "development"
    assert quality[TARGET_ID]["mode_source"] == "backfill_reclassified"
    assert quality[CONCEPT_ID]["mode"] == "learning"


def test_apply_preserves_line_counts_and_other_fields(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    hist_path = tmp_path / "learner" / "history.jsonl"
    qual_path = tmp_path / "learner" / "response-quality.jsonl"
    before_hist = _read_jsonl(hist_path)
    before_qual = _read_jsonl(qual_path)

    mod.apply(tmp_path, {TARGET_ID}, dry_run=False)

    after_hist = _read_jsonl(hist_path)
    after_qual = _read_jsonl(qual_path)
    assert len(after_hist) == len(before_hist)
    assert len(after_qual) == len(before_qual)
    # every non mode/mode_source field is unchanged
    for b, a in zip(before_hist, after_hist):
        for k in set(b) | set(a):
            if k in ("mode", "mode_source"):
                continue
            assert b.get(k) == a.get(k)


def test_apply_is_idempotent(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    first = mod.apply(tmp_path, {TARGET_ID}, dry_run=False)
    assert first["flipped_total"] == 3
    second = mod.apply(tmp_path, {TARGET_ID}, dry_run=False)
    assert second["flipped_total"] == 0
    already = sum(int(f["already_reclassified"]) for f in second["files"])
    assert already == 3


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    hist_path = tmp_path / "learner" / "history.jsonl"
    before = hist_path.read_text(encoding="utf-8")
    result = mod.apply(tmp_path, {TARGET_ID}, dry_run=True)
    assert result["flipped_total"] == 3
    assert result["backup_root"] is None
    assert hist_path.read_text(encoding="utf-8") == before
