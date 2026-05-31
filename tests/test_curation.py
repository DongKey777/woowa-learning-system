"""Tests for curation.{mine_history, propose_changes, apply_changes}."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from curation.apply_changes import apply
from curation.mine_history import mine
from curation.propose_changes import (
    ProposedChange,
    build_proposal_prompt,
    parse_proposals,
)


# ── mine_history ────────────────────────────────────────────────────────────


def _write_history(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "history.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def test_mine_citation_mismatch_pattern(tmp_path: Path) -> None:
    history = _write_history(tmp_path, [
        {"mode": "learning", "payload": {"prompt": "DI", "citations": ["spring/bean"], "quality_flags": ["citation_mismatch"]}},
        {"mode": "learning", "payload": {"prompt": "IoC", "citations": ["spring/bean"], "quality_flags": ["citation_mismatch"]}},
        {"mode": "learning", "payload": {"prompt": "X", "citations": ["spring/component"], "quality_flags": []}},
    ])
    signals = mine(history)
    mismatch = [s for s in signals if s.pattern == "citation_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].target_concept_id == "spring/bean"
    assert mismatch[0].frequency == 2


def test_mine_high_uncertain_pattern(tmp_path: Path) -> None:
    history = _write_history(tmp_path, [
        {"mode": "learning", "payload": {"uncertain": ["database/index"]}} for _ in range(4)
    ])
    signals = mine(history)
    hu = [s for s in signals if s.pattern == "high_uncertain"]
    assert hu and hu[0].target_concept_id == "database/index"
    assert hu[0].frequency == 4


def test_mine_low_hit_pattern(tmp_path: Path) -> None:
    history = _write_history(tmp_path, [
        {"mode": "learning", "payload": {"prompt": "obscure topic", "hits": [{"concept_id": "x/y", "score": 0.3}]}},
        {"mode": "learning", "payload": {"prompt": "another", "hits": []}},  # 0 hits excluded
    ])
    signals = mine(history)
    lh = [s for s in signals if s.pattern == "low_hit"]
    assert len(lh) == 1
    assert lh[0].frequency == 1
    assert "obscure topic" in lh[0].queries


def test_mine_filters_mode_development(tmp_path: Path) -> None:
    history = _write_history(tmp_path, [
        {"mode": "development", "payload": {"citations": ["x/y"], "quality_flags": ["citation_mismatch"]}},
        {"mode": "development", "payload": {"citations": ["x/y"], "quality_flags": ["citation_mismatch"]}},
    ])
    assert mine(history) == []


def test_mine_returns_empty_when_history_missing(tmp_path: Path) -> None:
    assert mine(tmp_path / "missing.jsonl") == []


def test_mine_recognizes_real_extra_citation_flag(tmp_path: Path) -> None:
    # Real response-quality emits extra_citation/missing_citation, not citation_mismatch.
    history = _write_history(tmp_path, [
        {"mode": "learning", "payload": {"prompt": "DI", "citations": ["spring/bean"], "quality_flags": ["extra_citation"]}},
        {"mode": "learning", "payload": {"prompt": "IoC", "citations": ["spring/bean"], "quality_flags": ["missing_citation"]}},
    ])
    signals = mine(history)
    mismatch = [s for s in signals if s.pattern == "citation_mismatch"]
    assert mismatch and mismatch[0].target_concept_id == "spring/bean"
    assert mismatch[0].frequency == 2


# ── join adapter (real learner state) ───────────────────────────────────────


def test_build_joined_events_projects_hits_and_joins_quality(tmp_path: Path) -> None:
    from curation.mine_history import build_joined_events

    history = _write_history(tmp_path, [
        {"event_type": "rag_ask", "event_id": "ask-1", "mode": "learning",
         "payload": {"prompt": "DI 뭐야", "top_concept_ids": ["spring/bean"]}},
        {"event_type": "rag_ask", "event_id": "ask-2", "mode": "development",
         "payload": {"prompt": "dev", "top_concept_ids": ["x/y"]}},
    ])
    rq = tmp_path / "response-quality.jsonl"
    rq.write_text(json.dumps({
        "source_event_id": "ask-1", "quality_flags": ["extra_citation"],
        "citation_paths_declared": ["spring/bean"],
    }) + "\n", encoding="utf-8")

    joined = build_joined_events(history, rq, mode_filter="learning")
    assert len(joined) == 1  # development event filtered out
    p = joined[0]["payload"]
    assert p["hits"] == [{"concept_id": "spring/bean"}]
    assert p["quality_flags"] == ["extra_citation"]
    assert p["citations"] == ["spring/bean"]


def test_mine_real_surfaces_profile_uncertain(tmp_path: Path) -> None:
    from curation.mine_history import mine_real

    (tmp_path / "history.jsonl").write_text(
        json.dumps({"event_type": "rag_ask", "event_id": "ask-1", "mode": "learning",
                    "payload": {"prompt": "q", "top_concept_ids": ["a/b", "c/d", "e/f"]}}) + "\n",
        encoding="utf-8")
    (tmp_path / "profile.json").write_text(
        json.dumps({"concepts": {"uncertain": ["database/lock-basics", "spring/bean-di-basics"]}}),
        encoding="utf-8")

    signals = mine_real(tmp_path, mode_filter="learning")
    hu = {s.target_concept_id for s in signals if s.pattern == "high_uncertain"}
    assert hu == {"database/lock-basics", "spring/bean-di-basics"}


# ── propose_changes ────────────────────────────────────────────────────────


def test_build_proposal_prompt_includes_all_signals() -> None:
    from curation.mine_history import MinedSignal

    sigs = [
        MinedSignal("citation_mismatch", "spring/bean", ["DI 뭐야"], 3),
        MinedSignal("low_hit", None, ["obscure"], 1),
    ]
    snippets = {"spring/bean": {"aliases": ["빈"], "expected_queries": ["DI"], "summary": "Spring container managed object"}}
    md = build_proposal_prompt(sigs, snippets)
    assert "citation_mismatch" in md
    assert "spring/bean" in md
    assert "DI 뭐야" in md
    assert "Spring container managed" in md
    assert "출력 형식" in md


def test_parse_proposals_extracts_json() -> None:
    text = """```json
[
  {"target_concept_id": "spring/bean", "operation": "add_alias",
   "payload": {"alias": "스프링빈"}, "rationale": "Korean alias missing"}
]
```"""
    out = parse_proposals(text)
    assert len(out) == 1
    assert out[0].operation == "add_alias"
    assert out[0].payload == {"alias": "스프링빈"}


def test_parse_proposals_no_fence() -> None:
    text = '[{"target_concept_id": "x/y", "operation": "add_alias", "payload": {"alias": "Y"}, "rationale": "r"}]'
    assert len(parse_proposals(text)) == 1


# ── apply_changes ──────────────────────────────────────────────────────────


def _seed_corpus(tmp_path: Path) -> Path:
    cdir = tmp_path / "concepts" / "spring"
    cdir.mkdir(parents=True)
    today = str(date.today())
    entity = {
        "id": "spring/bean", "title": "Bean", "category": "spring", "level": "beginner",
        "summary": "Spring container managed object", "body_markdown": "Body of Bean " * 5,
        "aliases": ["bean"], "expected_queries": ["Bean이 뭐야"],
        "relations": {"prerequisites": [], "next_docs": [], "confusable_with": [], "forbidden_for_queries": []},
        "metadata": {"schema_version": "v2", "created_at": today, "last_modified": today, "revision_history": []},
    }
    (cdir / "bean.json").write_text(json.dumps(entity, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path / "concepts"


def test_apply_add_alias_appends_and_updates_timestamp(tmp_path: Path) -> None:
    corpus_dir = _seed_corpus(tmp_path)
    p = ProposedChange("spring/bean", "add_alias", {"alias": "스프링빈"}, "korean")
    result = apply([p], corpus_dir=corpus_dir)
    assert len(result.applied) == 1
    updated = json.loads((corpus_dir / "spring" / "bean.json").read_text())
    assert "스프링빈" in updated["aliases"]
    assert updated["metadata"]["last_modified"] == str(date.today())


def test_apply_idempotent_skips_duplicate_alias(tmp_path: Path) -> None:
    corpus_dir = _seed_corpus(tmp_path)
    p = ProposedChange("spring/bean", "add_alias", {"alias": "bean"}, "dup")
    result = apply([p], corpus_dir=corpus_dir)
    assert result.applied == []
    assert len(result.skipped) == 1


def test_apply_new_concept_creates_file(tmp_path: Path) -> None:
    corpus_dir = _seed_corpus(tmp_path)
    p = ProposedChange("", "new_concept", {
        "id": "spring/component", "title": "Component",
        "summary": "Spring stereotype annotation",
        "body_markdown": "Body of Component longer than ten characters",
    }, "missing")
    result = apply([p], corpus_dir=corpus_dir)
    assert len(result.applied) == 1
    new_path = corpus_dir / "spring" / "component.json"
    assert new_path.exists()
    data = json.loads(new_path.read_text())
    assert data["id"] == "spring/component"
    assert data["category"] == "spring"


def test_apply_skips_unknown_target(tmp_path: Path) -> None:
    corpus_dir = _seed_corpus(tmp_path)
    p = ProposedChange("spring/ghost", "add_alias", {"alias": "g"}, "x")
    result = apply([p], corpus_dir=corpus_dir)
    assert result.applied == []
    assert "not found" in result.skipped[0][1]


def test_apply_dry_run_does_not_write(tmp_path: Path) -> None:
    corpus_dir = _seed_corpus(tmp_path)
    original = (corpus_dir / "spring" / "bean.json").read_text()
    p = ProposedChange("spring/bean", "add_alias", {"alias": "스프링빈"}, "r")
    apply([p], corpus_dir=corpus_dir, dry_run=True)
    assert (corpus_dir / "spring" / "bean.json").read_text() == original
