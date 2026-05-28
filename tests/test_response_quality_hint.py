"""Phase Y11 PR-C — response_quality_hint sibling contract.

daemon emits a top-level `response_quality_hint` so the AI session knows
which `bin/learn-response-quality --source-event-id …` to invoke after the
turn. Full body capture is required. Path capture is preferred for token
efficiency, while stdin remains the universal fallback.
"""
from __future__ import annotations

from pathlib import Path

from core.coach import _build_response_quality_hint, compose
from core.router import (
    ARTIFACT_CONCEPT_GRAPH,
    PERSONA_MENTOR,
    RouteDecision,
)
from core.state import DEFAULT_STATE_ROOT


def _cs_route():
    return RouteDecision(
        mode="cs_qa", need_rag=True, need_mission_ctx=False, need_anchors=False,
        personas=[PERSONA_MENTOR], budget_tokens=4500,
        lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH],
        reason="test", confidence=0.6,
    )


def test_quality_hint_present_with_event_id():
    artifacts = {"rag_hits": [
        {"concept_id": "spring/di", "score": 0.9, "source": "dense",
         "category": "spring", "title": "DI"}
    ]}

    _, hints, rq, _ = compose(
        _cs_route(), artifacts, "DI", source_event_id="ask-12345",
    )

    assert rq is not None
    assert rq["source_event_id"] == "ask-12345"
    assert "bin/learn-response-quality" in rq["command_template"]
    assert "--source-event-id ask-12345" in rq["command_template"]
    assert "--response-file -" in rq["command_template"]
    assert "--summary-only" not in rq["command_template"]
    assert "--response-path <answer.md>" in rq["full_body_path_template"]
    assert "--response-file -" in rq["stdin_fallback_cmd"]
    assert "--minimal" not in rq["command_template"]
    assert "--expected-citation spring/di" in rq["command_template"]
    assert "--silent" in rq["command_template"]
    assert rq["body_required"] is True
    assert rq["body_capture_preferred"] is True
    assert rq["capture_policy"] == "full_body_required_path_preferred"
    assert "Every learner-facing answer should be captured as full body" in rq["body_contract"]
    assert "minimal_fallback_cmd" not in rq
    # expected_citation_paths mirrors hints.citation_paths
    assert rq["expected_citation_paths"] == hints["citation_paths"]


def test_quality_hint_none_on_cold_path():
    """source_event_id=None (bin/ask --no-daemon cold path) ⇒ hint None
    (avoid emitting `--source-event-id None` shell command)."""
    _, _, rq, _ = compose(_cs_route(), {}, "DI", source_event_id=None)
    assert rq is None


def test_quality_hint_includes_non_default_state_root():
    """When daemon runs with non-default --state-root, the hint must echo it
    so the wrapper call resolves the same history.jsonl."""
    tmp = Path("/tmp/woowa-test-state-not-default")
    rq = _build_response_quality_hint(
        source_event_id="ask-77",
        expected_citation_paths=["a/b"],
        state_root=tmp,
    )
    assert rq is not None
    assert "--state-root" in rq["command_template"]
    assert "--state-root" in rq["full_body_path_template"]
    assert "--state-root" in rq["stdin_fallback_cmd"]
    assert str(tmp) in rq["command_template"]


def test_quality_hint_omits_state_root_for_default():
    rq = _build_response_quality_hint(
        source_event_id="ask-99",
        expected_citation_paths=[],
        state_root=Path(DEFAULT_STATE_ROOT),
    )
    assert rq is not None
    assert "--state-root" not in rq["command_template"]
    assert "--state-root" not in rq["full_body_path_template"]


def test_quality_hint_wrapper_cmd_alias_matches_command_template():
    rq = _build_response_quality_hint(
        source_event_id="ask-1",
        expected_citation_paths=["x/y"],
        state_root=None,
    )
    # wrapper_cmd remains an alias for the universal full-body command.
    assert rq["wrapper_cmd"] == rq["command_template"]
