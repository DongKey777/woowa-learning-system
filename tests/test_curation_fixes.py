"""Tests for the curation automation hardening (pipeline friction #1, #3, #6).

Covers three fixes layered onto curation/apply_changes + bin/corpus-curate:
  #3  add_confusable now registers the relation reciprocally (both endpoints).
  #1  bin/corpus-curate --apply surfaces ApplyResult.ownership_new_steals and exits 1.
  #6  apply() flushes the outcome to state/curation/applied-<ts>.jsonl (rollback log).

These run index-free (load_corpus over corpus/concepts/*.json fields only, ~1s) — no
daemon / dense index needed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from curation.apply_changes import apply
from curation.propose_changes import ProposedChange

REPO_ROOT = Path(__file__).resolve().parent.parent


def _seed_concept(corpus_dir: Path, cid: str, *, aliases=None, expected_queries=None,
                  confusable_with=None) -> Path:
    """Write a minimal v2 concept JSON under corpus_dir and return its path."""
    category, slug = cid.split("/", 1)
    today = str(date.today())
    entity = {
        "id": cid,
        "title": slug.replace("-", " ").title(),
        "category": category,
        "level": "beginner",
        "summary": f"Summary of {cid} long enough to validate",
        "body_markdown": f"Body of {cid} " * 5,
        "aliases": list(aliases or []),
        "expected_queries": list(expected_queries or []),
        "symptoms": [],
        "relations": {
            "prerequisites": [], "next_docs": [],
            "confusable_with": list(confusable_with or []),
            "forbidden_for_queries": [],
        },
        "learner_query_patterns": [],
        "metadata": {
            "schema_version": "v2", "created_at": today, "last_modified": today,
            "revision_history": [],
        },
    }
    path = corpus_dir / (cid + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entity, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _confusable_of(corpus_dir: Path, cid: str) -> list[str]:
    entity = json.loads((corpus_dir / (cid + ".json")).read_text(encoding="utf-8"))
    return entity.get("relations", {}).get("confusable_with", [])


# ── #3 reciprocal add_confusable ────────────────────────────────────────────


def test_add_confusable_registers_both_endpoints(tmp_path: Path) -> None:
    """add_confusable must append the relation to BOTH the target and the `with`
    concept so the confusable graph stays symmetric (was one-directional)."""
    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "spring/bean-di-basics")
    _seed_concept(corpus_dir, "software-engineering/dependency-injection-basics")

    p = ProposedChange(
        "spring/bean-di-basics", "add_confusable",
        {"with": "software-engineering/dependency-injection-basics"}, "siblings",
    )
    result = apply([p], corpus_dir=corpus_dir)

    assert len(result.applied) == 1
    # Both files appear in touched_paths.
    touched = {p.name for p in result.touched_paths}
    assert "bean-di-basics.json" in touched
    assert "dependency-injection-basics.json" in touched
    # Reciprocal: each side carries the other.
    assert _confusable_of(corpus_dir, "spring/bean-di-basics") == \
        ["software-engineering/dependency-injection-basics"]
    assert _confusable_of(corpus_dir, "software-engineering/dependency-injection-basics") == \
        ["spring/bean-di-basics"]


def test_add_confusable_idempotent_when_already_symmetric(tmp_path: Path) -> None:
    """If the edge already exists on both sides, the operation is a no-op skip."""
    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "a/one", confusable_with=["a/two"])
    _seed_concept(corpus_dir, "a/two", confusable_with=["a/one"])

    p = ProposedChange("a/one", "add_confusable", {"with": "a/two"}, "dup")
    result = apply([p], corpus_dir=corpus_dir)

    assert result.applied == []
    assert len(result.skipped) == 1
    assert "idempotent" in result.skipped[0][1]


def test_add_confusable_completes_one_sided_edge(tmp_path: Path) -> None:
    """A pre-existing one-directional edge gets completed (only the missing side is
    written), and only that side is touched."""
    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "a/one", confusable_with=["a/two"])  # forward edge present
    _seed_concept(corpus_dir, "a/two")                            # back edge missing

    p = ProposedChange("a/one", "add_confusable", {"with": "a/two"}, "complete")
    result = apply([p], corpus_dir=corpus_dir)

    assert len(result.applied) == 1
    assert _confusable_of(corpus_dir, "a/one") == ["a/two"]          # unchanged
    assert _confusable_of(corpus_dir, "a/two") == ["a/one"]          # back-filled
    # Only the changed side is touched.
    assert {p.name for p in result.touched_paths} == {"two.json"}


def test_add_confusable_skips_missing_with_target(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "a/one")

    p = ProposedChange("a/one", "add_confusable", {"with": "a/ghost"}, "missing")
    result = apply([p], corpus_dir=corpus_dir)

    assert result.applied == []
    assert "not found" in result.skipped[0][1]
    assert _confusable_of(corpus_dir, "a/one") == []  # untouched


# ── #6 applied-log flush ────────────────────────────────────────────────────


def test_apply_flushes_applied_log(tmp_path: Path, monkeypatch) -> None:
    """apply(timestamp=ts) writes state/curation/applied-<ts>.jsonl with one record
    per change plus a summary line (rollback source of truth)."""
    import curation.apply_changes as ac

    log_dir = tmp_path / "state" / "curation"
    monkeypatch.setattr(ac, "_APPLIED_LOG_DIR", log_dir)

    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "spring/bean", aliases=["bean"])
    proposals = [
        ProposedChange("spring/bean", "add_alias", {"alias": "스프링빈"}, "korean"),
        ProposedChange("spring/ghost", "add_alias", {"alias": "x"}, "missing"),  # skipped
    ]
    apply(proposals, corpus_dir=corpus_dir, timestamp="20260625T000000Z")

    log_path = log_dir / "applied-20260625T000000Z.jsonl"
    assert log_path.exists(), "applied-log not flushed"
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    statuses = [r["status"] for r in records]
    assert statuses.count("applied") == 1
    assert statuses.count("skipped") == 1
    assert statuses.count("summary") == 1
    summary = next(r for r in records if r["status"] == "summary")
    assert summary["applied"] == 1
    assert summary["skipped"] == 1
    assert summary["touched_paths"]  # the written bean.json path


def test_apply_without_timestamp_writes_no_log(tmp_path: Path, monkeypatch) -> None:
    """No timestamp → no flush (the default path stays write-free)."""
    import curation.apply_changes as ac

    log_dir = tmp_path / "state" / "curation"
    monkeypatch.setattr(ac, "_APPLIED_LOG_DIR", log_dir)

    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "spring/bean")
    apply([ProposedChange("spring/bean", "add_alias", {"alias": "x"}, "r")],
          corpus_dir=corpus_dir)

    assert not log_dir.exists()


def test_apply_dry_run_writes_no_log(tmp_path: Path, monkeypatch) -> None:
    """dry_run never flushes even with a timestamp (no corpus mutation → no log)."""
    import curation.apply_changes as ac

    log_dir = tmp_path / "state" / "curation"
    monkeypatch.setattr(ac, "_APPLIED_LOG_DIR", log_dir)

    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "spring/bean")
    apply([ProposedChange("spring/bean", "add_alias", {"alias": "x"}, "r")],
          corpus_dir=corpus_dir, dry_run=True, timestamp="20260625T000000Z")

    assert not (log_dir / "applied-20260625T000000Z.jsonl").exists()


# ── #1 bin/corpus-curate surfaces ownership_new_steals + exits 1 ─────────────


def _norm_for(query: str) -> str:
    from rag.search import _norm_exact
    return _norm_exact(query)


# A synthetic phrase that is NOT in the live frozen baseline, so a fire+shadow on it is
# a genuine NEW steal even when the CLI uses the real fixture (verified not present).
_STEAL_PHRASE = "좀비스틸테스트전용문구XYZ123"


def test_corpus_curate_apply_exits_1_on_ownership_steal(tmp_path: Path) -> None:
    """End-to-end: a proposal that steals an existing canonical owner's exact-shortcut
    must make bin/corpus-curate --apply print the steal and exit 1 (Gate E surfaced).

    Runs the real CLI against the live fixture baseline; the phrase is chosen so it is
    absent from that baseline → the introduced fire+shadow is a NEW steal."""
    import os

    corpus_dir = tmp_path / "concepts"
    # Canonical owner carries the phrase only as an ALIAS (lower tier).
    _seed_concept(corpus_dir, "spring/bean-di-basics", aliases=[_STEAL_PHRASE])

    # Proposal: a NEW concept claims the same phrase as an expected_query (HIGHER tier) →
    # it becomes the single shortcut winner, shadowing the canonical owner → new steal.
    proposals = [
        {"target_concept_id": "", "operation": "new_concept",
         "payload": {"id": "software-engineering/di-thief", "title": "DI Thief",
                     "summary": "A concept that steals the shortcut for the test corpus",
                     "body_markdown": "Body long enough to validate the schema here.",
                     "expected_queries": [_STEAL_PHRASE]},
         "rationale": "inject steal"},
    ]
    proposals_path = tmp_path / "proposals.json"
    proposals_path.write_text(json.dumps(proposals), encoding="utf-8")

    full_env = {**os.environ, "WOOWA_SESSION_MODE": "development"}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "corpus-curate"),
         "--apply", str(proposals_path), "--corpus-dir", str(corpus_dir)],
        capture_output=True, text=True, env=full_env,
    )
    assert proc.returncode == 1, f"expected exit 1 on steal; stdout=\n{proc.stdout}\nstderr=\n{proc.stderr}"
    assert "ownership regression" in proc.stdout
    assert _norm_for(_STEAL_PHRASE) in proc.stdout


def test_corpus_curate_apply_exits_0_when_no_steal(tmp_path: Path) -> None:
    """A benign batch (no ownership steal, no skip) exits 0 — the guard is not noisy."""
    import os

    corpus_dir = tmp_path / "concepts"
    _seed_concept(corpus_dir, "spring/bean")

    proposals = [{"target_concept_id": "spring/bean", "operation": "add_alias",
                  "payload": {"alias": "유니크별칭테스트XYZ"}, "rationale": "benign"}]
    proposals_path = tmp_path / "proposals.json"
    proposals_path.write_text(json.dumps(proposals), encoding="utf-8")

    full_env = {**os.environ, "WOOWA_SESSION_MODE": "development"}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "corpus-curate"),
         "--apply", str(proposals_path), "--corpus-dir", str(corpus_dir)],
        capture_output=True, text=True, env=full_env,
    )
    assert proc.returncode == 0, f"expected exit 0; stdout=\n{proc.stdout}\nstderr=\n{proc.stderr}"
    assert "ownership regression" not in proc.stdout


def test_apply_reports_ownership_steal_at_api_level(tmp_path: Path, monkeypatch) -> None:
    """apply() populates ownership_new_steals when a written concept steals an existing
    canonical owner's exact shortcut (the data bin/corpus-curate surfaces + exits 1 on)."""
    import curation.apply_changes as ac

    # Empty frozen baseline so any fire+shadow is a NEW steal.
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"norms": {}}), encoding="utf-8")
    monkeypatch.setattr(ac, "_BASELINE_PATH", baseline_path)

    corpus_dir = tmp_path / "concepts"
    # Canonical owner: only an ALIAS carries the phrase (lower tier).
    _seed_concept(corpus_dir, "spring/bean-di-basics", aliases=["DI가 뭐야"])
    # Thief concept: claims the same phrase as an expected_query (HIGHER tier) → it wins
    # the shortcut, shadowing the canonical owner → a fire+shadow steal.
    proposals = [
        ProposedChange("", "new_concept", {
            "id": "software-engineering/di-thief", "title": "DI Thief",
            "summary": "Concept that steals the DI shortcut for the test",
            "body_markdown": "Body long enough to validate the schema here.",
            "expected_queries": ["DI가 뭐야"],
        }, "inject steal"),
    ]
    result = apply(proposals, corpus_dir=corpus_dir)

    assert result.ownership_new_steals, "expected a new steal to be reported"
    assert _norm_for("DI가 뭐야") in result.ownership_new_steals
