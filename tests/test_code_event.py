"""Unit tests for core.code_event — Phase T2."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.code_event import infer_concepts_from_file, record_code_attempt  # noqa: E402


def test_infer_concepts_from_real_spring_file(tmp_path: Path) -> None:
    """Real Spring annotations should map to spring/* concept ids."""
    java = tmp_path / "FooController.java"
    java.write_text("""
package roomescape;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.transaction.annotation.Transactional;
@RestController
public class FooController {
    @Transactional
    public void foo() {}
}
""", encoding="utf-8")
    concepts = infer_concepts_from_file(str(java))
    # @RestController + @Transactional are both in mission.extract ANNOTATION_TO_CONCEPT
    assert any("spring/" in c for c in concepts), f"got: {concepts}"
    # Sanity: distinct
    assert len(concepts) == len(set(concepts))


def test_infer_concepts_from_missing_file_returns_empty(tmp_path: Path) -> None:
    assert infer_concepts_from_file(str(tmp_path / "nope.java")) == []


def test_record_code_attempt_appends_event(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    java = tmp_path / "Foo.java"
    java.write_text("@RestController\npublic class Foo {}", encoding="utf-8")
    event = record_code_attempt(
        file_path=str(java), summary="add controller", repo="demo",
        lines_added=5, state_root=state,
    )
    assert event["event_type"] == "code_attempt"
    assert event["payload"]["concept_source"] == "auto_inferred"
    assert event["payload"]["file_path"] == str(java)
    assert event["payload"]["lines_added"] == 5
    # history.jsonl appended
    lines = [l for l in (state / "learner" / "history.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "code_attempt"


def test_record_code_attempt_explicit_concepts_skip_inference(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    event = record_code_attempt(
        file_path="/nonexistent.java", summary="manual",
        explicit_concept_ids=["spring/manual-pick"], state_root=state,
    )
    assert event["payload"]["concept_source"] == "explicit"
    assert event["payload"]["concept_ids"] == ["spring/manual-pick"]


def test_record_code_attempt_development_mode_skips_record_turn(tmp_path: Path) -> None:
    """Mode B events should NOT pollute mastery autoloop."""
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    java = tmp_path / "Foo.java"
    java.write_text("@RestController\npublic class Foo {}", encoding="utf-8")
    event = record_code_attempt(
        file_path=str(java), summary="dev work",
        mode="development", state_root=state,
    )
    assert event["mode"] == "development"
    # mastery_graph.sqlite should NOT exist (record_turn was skipped)
    assert not (state / "learner" / "mastery_graph.sqlite").exists()


def test_record_code_attempt_event_id_format(tmp_path: Path) -> None:
    state = tmp_path
    (state / "learner").mkdir(parents=True)
    event = record_code_attempt(
        file_path="/missing.java", summary="x",
        explicit_concept_ids=[], state_root=state,
    )
    assert event["event_id"].startswith("code_attempt-")
    assert len(event["event_id"]) >= 25  # prefix + ts + uuid suffix
