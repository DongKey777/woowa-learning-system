"""bin/corpus-gate — one-shot corpus-expansion gate runner.

Verifies the ported gates (orphan §6.1 / filler §6.2 / path v6 §4 / contract v4 Gate A):
each subcommand runs on the LIVE corpus with its intended exit code, and the filler/path
detection logic is exercised synthetically (so a regression in the matcher is caught
without depending on the live corpus carrying a violation). The live 3654-concept corpus
is maintained to pass orphan/filler/path, so on a clean worktree they are no-op PASSes.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_PATH = REPO_ROOT / "bin" / "corpus-gate"


def _load_gate_module():
    """Import bin/corpus-gate (extension-less executable) as a module."""
    spec = importlib.util.spec_from_loader(
        "corpus_gate", loader=None, origin=str(GATE_PATH))
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(GATE_PATH)
    exec(compile(GATE_PATH.read_text(encoding="utf-8"), str(GATE_PATH), "exec"),
         module.__dict__)
    return module


gate = _load_gate_module()


# ─────────────────────────────────────────────────────────────────
# Live-corpus end-to-end: each subcommand returns its intended exit code.
# ─────────────────────────────────────────────────────────────────
def test_all_passes_on_live_corpus(capsys) -> None:
    """orphan+filler+path are hard gates; the maintained live corpus passes all → exit 0.
    A non-zero here is a REAL finding (corpus regression), not a tool default."""
    rc = gate.main(["all"])
    out = capsys.readouterr().out
    assert rc == 0, f"corpus-gate all failed on live corpus:\n{out}"
    assert "corpus-gate all: PASS" in out


def test_each_subcommand_runs_on_live(capsys) -> None:
    for sub in ("orphan", "filler", "path", "contract"):
        rc = gate.main([sub])
        out = capsys.readouterr().out
        assert rc == 0, f"{sub} returned {rc} on live corpus:\n{out}"
        assert out.strip(), f"{sub} produced no output"


def test_contract_is_report_only_never_hard_fails(capsys) -> None:
    """contract REPORTs the distribution (alias/EQ/title/relation) but never hard-fails —
    the live corpus already carries pre-existing items (title-exact-alias) that must not
    block a batch."""
    rc = gate.main(["contract"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "contract REPORT" in out
    assert "title-exact-alias:" in out


def test_all_json_shape(capsys) -> None:
    rc = gate.main(["all", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert payload["gate"] == "all"
    assert payload["hard_failed"] is False
    assert set(payload["results"]) == {"orphan", "filler", "path", "surface", "contract"}
    assert payload["results"]["surface"]["status"] == "pass"
    assert payload["results"]["contract"]["status"] == "report"
    assert payload["results"]["contract"]["concepts"] > 3000


def test_contract_reports_id_relation_fields_only() -> None:
    """forbidden_for_queries holds QUERY strings, not concept ids — it must be excluded
    from the relation missing-target count, else the live corpus shows 417 false misses."""
    from rag.corpus_loader import load_corpus
    corpus = load_corpus()
    r = gate.gate_contract(corpus)
    assert r["relation_missing_target"] == 0, (
        "id-bearing relation fields should resolve cleanly on the live corpus; "
        "a non-zero here means forbidden_for_queries leaked into the id check"
    )
    assert r["relation_self"] == 0


# ─────────────────────────────────────────────────────────────────
# Graceful skip when the git base ref is absent.
# ─────────────────────────────────────────────────────────────────
def test_orphan_skips_on_missing_base(capsys) -> None:
    rc = gate.main(["orphan", "--base", "definitely-not-a-ref-xyz"])
    out = capsys.readouterr().out
    assert rc == 0  # graceful — never a hard fail on a missing base
    assert "orphan SKIP" in out


def test_path_skips_on_missing_base(capsys) -> None:
    rc = gate.main(["path", "--base", "definitely-not-a-ref-xyz"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "path SKIP" in out


# ─────────────────────────────────────────────────────────────────
# Synthetic filler detection — the §6.2 three layers.
# ─────────────────────────────────────────────────────────────────
def test_filler_same_stem_suffix_pair_detected() -> None:
    """Two suffixed aliases sharing a stem = the deterministic machine-template tell."""
    assert gate._FILLER_SUFFIX.search("낙관적 락 개념")
    assert gate._FILLER_SUFFIX.search("낙관적 락 정리")
    assert gate._filler_stem("낙관적 락 개념") == gate._filler_stem("낙관적 락 정리")


def test_filler_malformed_dangling_detected() -> None:
    """dangling vs/↔/· + suffix is a hard-fail malformed alias."""
    assert gate._FILLER_MALFORMED.search("정규화 vs 반정규화 vs 개념")
    assert gate._FILLER_MALFORMED.search("낙관락 ↔ 개념")
    assert gate._FILLER_MALFORMED.search("A · 정리")


def test_filler_natural_language_not_suffix_matched() -> None:
    """A genuine learner phrase that merely ends in ~정리 is NOT a same-stem template; the
    suffix regex matches the trailing token (reported, not hard-failed) but a lone single
    must NOT trip the hard same-stem rule."""
    al = ["session guarantee 차이 한 번에 정리"]  # single raw-suffix, no sibling stem
    bystem: dict = {}
    for a in al:
        if gate._FILLER_SUFFIX.search(a):
            bystem.setdefault(gate._filler_stem(a), []).append(a)
    assert all(len(v) < 2 for v in bystem.values())  # no hard same-stem trigger


def test_gate_filler_runs_and_reports_raw_singles(tmp_path) -> None:
    """gate_filler returns the structured report shape used by the renderer / --json."""
    from rag.corpus_loader import load_corpus
    corpus = load_corpus()  # noqa: F841 — gate_filler reads files directly, but assert load OK
    r = gate.gate_filler()
    assert r["status"] in ("pass", "fail")
    assert set(r) >= {"same_stem", "malformed", "raw_single",
                      "same_stem_examples", "malformed_examples"}


# ─────────────────────────────────────────────────────────────────
# Synthetic path (Gate F) — cid derivation + dead-path detection over an isolated fixture.
# ─────────────────────────────────────────────────────────────────
def test_cid_of_flat_structure() -> None:
    """Live ids are flat <category>/<slug>; cid_of strips contents/ prefix and .md."""
    assert gate._cid_of("contents/spring/bean-di-basics.md") == "spring/bean-di-basics"
    assert gate._cid_of("spring/bean-di-basics.md") == "spring/bean-di-basics"
    assert gate._cid_of("spring/bean-di-basics") == "spring/bean-di-basics"


def test_path_detects_dead_path_synthetically(monkeypatch, tmp_path) -> None:
    """A fixture query whose primary path is NOT a real concept id → dead path (fail).
    Drives gate_path with a stubbed corpus + git scope so it's hermetic (no real diff)."""

    class _Corpus:
        concepts = {"spring/bean-di-basics": {}, "database/lock-basics": {}}

    fixture = tmp_path / "y14_expansion_synthetic.json"
    fixture.write_text(json.dumps({
        "queries": [
            {"query": "good", "primary_paths": ["spring/bean-di-basics"]},
            {"query": "bad", "primary_paths": ["spring/does-not-exist"],
             "acceptable_paths": ["database/lock-basics"]},
            {"query": "deadforbidden", "forbidden_paths": ["language/java/nested-dead"]},
        ]
    }), encoding="utf-8")

    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines",
                        lambda args: [str(fixture)] if "diff" in args else [])

    # Point the relative-existence check at the fixture's absolute path.
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    r = gate.gate_path("origin/main", _Corpus())

    assert r["status"] == "fail"
    dead = {p for _, _, p in r["dead_paths"]}
    assert "spring/does-not-exist" in dead
    assert "language/java/nested-dead" in dead  # the 2-level infix mismatch class
    assert "spring/bean-di-basics" not in dead   # real id passes
    assert "database/lock-basics" not in dead


def test_path_passes_when_all_paths_real(monkeypatch, tmp_path) -> None:
    class _Corpus:
        concepts = {"spring/bean-di-basics": {}}

    fixture = tmp_path / "y14_expansion_clean.json"
    fixture.write_text(json.dumps({
        "queries": [{"query": "q", "primary_paths": ["spring/bean-di-basics"]}]
    }), encoding="utf-8")
    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines",
                        lambda args: [str(fixture)] if "diff" in args else [])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    r = gate.gate_path("origin/main", _Corpus())
    assert r["status"] == "pass"
    assert r["changed_fixtures"] == 1
    assert r["dead_paths"] == []


# ─────────────────────────────────────────────────────────────────
# Synthetic surface preservation (Gate S) — old alias/EQ deletion cannot silently grow.
# ─────────────────────────────────────────────────────────────────
def test_surface_detects_new_exact_surface_loss(monkeypatch, tmp_path) -> None:
    concepts_dir = tmp_path / "corpus" / "concepts" / "spring"
    concepts_dir.mkdir(parents=True)
    cur_file = concepts_dir / "bean-di-basics.json"
    cur_file.write_text(json.dumps({
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": [], "expected_queries": [],
    }), encoding="utf-8")

    old_doc = {
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": ["constructor injection"], "expected_queries": ["DI가 뭐야?"],
    }

    class _Corpus:
        concepts = {
            "spring/bean-di-basics": {
                "id": "spring/bean-di-basics", "title": "Bean DI",
                "aliases": [], "expected_queries": [],
            }
        }

    monkeypatch.setattr(gate, "CONCEPTS_DIR", tmp_path / "corpus" / "concepts")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines",
                        lambda args: ["corpus/concepts/spring/bean-di-basics.json"])
    monkeypatch.setattr(gate, "_show_at", lambda base, path: old_doc)

    r = gate.gate_surface("origin/main", _Corpus(), baseline_path=tmp_path / "baseline.json")
    assert r["status"] == "fail"
    lost = {row["surface"] for row in r["new_losses"]}
    assert {"constructor injection", "DI가 뭐야?"} <= lost


def test_surface_allows_same_concept_reownership(monkeypatch, tmp_path) -> None:
    concepts_dir = tmp_path / "corpus" / "concepts" / "spring"
    concepts_dir.mkdir(parents=True)
    cur_file = concepts_dir / "bean-di-basics.json"
    cur_file.write_text(json.dumps({
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": ["constructor injection"], "expected_queries": [],
    }), encoding="utf-8")

    old_doc = {
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": [], "expected_queries": ["constructor injection"],
    }

    class _Corpus:
        concepts = {
            "spring/bean-di-basics": {
                "id": "spring/bean-di-basics", "title": "Bean DI",
                "aliases": ["constructor injection"], "expected_queries": [],
            }
        }

    monkeypatch.setattr(gate, "CONCEPTS_DIR", tmp_path / "corpus" / "concepts")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines",
                        lambda args: ["corpus/concepts/spring/bean-di-basics.json"])
    monkeypatch.setattr(gate, "_show_at", lambda base, path: old_doc)

    r = gate.gate_surface("origin/main", _Corpus(), baseline_path=tmp_path / "baseline.json")
    assert r["status"] == "pass"
    assert r["new_losses"] == []


def test_surface_baselined_loss_is_allowed_and_resolved_is_reported(monkeypatch, tmp_path) -> None:
    loss = {
        "concept_id": "spring/bean-di-basics",
        "path": "corpus/concepts/spring/bean-di-basics.json",
        "field": "aliases",
        "surface": "legacy shortcut",
        "norm": "legacy shortcut",
    }
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"losses": [loss]}), encoding="utf-8")

    class _Corpus:
        concepts = {
            "spring/bean-di-basics": {
                "id": "spring/bean-di-basics", "title": "Bean DI",
                "aliases": [], "expected_queries": [],
            }
        }

    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines", lambda args: [])

    r = gate.gate_surface("origin/main", _Corpus(), baseline_path=baseline)
    assert r["status"] == "pass"
    assert r["new_losses"] == []
    assert r["resolved"] == [loss]


# ─────────────────────────────────────────────────────────────────
# Synthetic orphan (Gate B) — protected surface deleted with no owner → fail.
# ─────────────────────────────────────────────────────────────────
def test_orphan_norm_preserves_question_mark() -> None:
    """Protected surfaces include the literal '?' (e.g. 'DI가 말야?') — the orphan norm
    must NOT strip it (unlike _norm_exact), or a real deletion would silently match."""
    assert gate._orphan_norm("  DI가  말야?  ") == "di가 말야?"
    assert "di가 말야?" in {gate._orphan_norm(v) for v in gate.PROTECTED}


def test_orphan_detects_protected_deletion(monkeypatch, tmp_path) -> None:
    """A protected surface present at base, absent now, and owned by NO concept → orphan."""
    concepts_dir = tmp_path / "corpus" / "concepts" / "spring"
    concepts_dir.mkdir(parents=True)
    # Current file: the protected EQ 'DI가 말야?' has been removed and nobody else owns it.
    cur_file = concepts_dir / "bean-di-basics.json"
    cur_file.write_text(json.dumps({
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": [], "expected_queries": ["스프링 빈 등록"],
    }), encoding="utf-8")

    old_doc = {
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": [], "expected_queries": ["DI가 말야?", "스프링 빈 등록"],
    }

    monkeypatch.setattr(gate, "CONCEPTS_DIR", tmp_path / "corpus" / "concepts")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines",
                        lambda args: ["corpus/concepts/spring/bean-di-basics.json"])
    monkeypatch.setattr(gate, "_show_at", lambda base, path: old_doc)

    r = gate.gate_orphan("origin/main")
    assert r["status"] == "fail"
    deleted = {v for _, _, v in r["orphan"]}
    assert "DI가 말야?" in deleted


def test_orphan_reowned_is_report_only(monkeypatch, tmp_path) -> None:
    """If the deleted protected surface is reowned by ANOTHER concept, it's REOWNED
    (report-only), not an orphan fail."""
    concepts_dir = tmp_path / "corpus" / "concepts" / "spring"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "bean-di-basics.json").write_text(json.dumps({
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": [], "expected_queries": ["스프링 빈 등록"],
    }), encoding="utf-8")
    # A different concept now owns 'DI가 말야?' → reowned, not orphaned.
    (concepts_dir / "di-primer.json").write_text(json.dumps({
        "id": "spring/di-primer", "title": "DI Primer",
        "aliases": [], "expected_queries": ["DI가 말야?"],
    }), encoding="utf-8")

    old_doc = {
        "id": "spring/bean-di-basics", "title": "Bean DI",
        "aliases": [], "expected_queries": ["DI가 말야?", "스프링 빈 등록"],
    }
    monkeypatch.setattr(gate, "CONCEPTS_DIR", tmp_path / "corpus" / "concepts")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_base_exists", lambda base: True)
    monkeypatch.setattr(gate, "_git_lines",
                        lambda args: ["corpus/concepts/spring/bean-di-basics.json"])
    monkeypatch.setattr(gate, "_show_at", lambda base, path: old_doc)

    r = gate.gate_orphan("origin/main")
    assert r["status"] == "pass"  # reowned ≠ orphan
    reowned = {v for _, _, v in r["reowned"]}
    assert "DI가 말야?" in reowned


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
