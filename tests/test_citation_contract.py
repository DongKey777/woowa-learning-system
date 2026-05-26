"""Phase Y11 PR-C — citation_markdown / citation_paths contract.

compose() returns response_hints with paste-ready citation. When the AI session
hand-writes concept_id paths it hallucinates (e.g. ``testing/web-mvc-slice-boundary``
which doesn't exist in the corpus); this contract forces the AI to read the
hint instead.
"""
from __future__ import annotations

from core.coach import compose
from core.router import (
    ARTIFACT_CONCEPT_GRAPH,
    PERSONA_MENTOR,
    PERSONA_SOCRATIC,
    RouteDecision,
)


def _cs_qa_route() -> RouteDecision:
    return RouteDecision(
        mode="cs_qa", need_rag=True, need_mission_ctx=False, need_anchors=False,
        personas=[PERSONA_MENTOR, PERSONA_SOCRATIC],
        budget_tokens=4500,
        lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH],
        reason="test", confidence=0.6,
    )


def _hits(*concept_ids, score=0.8):
    return [
        {"concept_id": cid, "score": score, "source": "dense",
         "category": cid.split("/", 1)[0], "title": cid}
        for cid in concept_ids
    ]


def test_citation_markdown_emits_paste_ready_block():
    route = _cs_qa_route()
    artifacts = {"rag_hits": _hits("spring/di-basics", "spring/ioc")}

    _, hints, _, eff = compose(route, artifacts, "DI 어떻게")

    assert eff.mode == "cs_qa"
    assert hints["citation_markdown"].startswith("참고:\n- ")
    assert "spring/di-basics" in hints["citation_markdown"]
    assert "spring/ioc" in hints["citation_markdown"]
    # citation_paths mirrors citation_concept_ids (v2 schema parity)
    assert hints["citation_paths"] == hints["citation_concept_ids"]
    assert hints["citation_paths"] == ["spring/di-basics", "spring/ioc"]


def test_citation_capped_at_three():
    route = _cs_qa_route()
    artifacts = {"rag_hits": _hits(
        "a/1", "a/2", "a/3", "a/4", "a/5", "a/6", "a/7"
    )}

    _, hints, _, _ = compose(route, artifacts, "test")

    assert len(hints["citation_paths"]) == 3
    assert hints["citation_paths"] == ["a/1", "a/2", "a/3"]
    assert len(hints["citation_trace"]) == 3


def test_citation_dedupes_concept_ids():
    route = _cs_qa_route()
    artifacts = {"rag_hits": _hits("dup/x", "dup/x", "dup/y")}

    _, hints, _, _ = compose(route, artifacts, "test")

    assert hints["citation_paths"] == ["dup/x", "dup/y"]


def test_citation_filters_error_and_missing_concept_id_hits():
    route = _cs_qa_route()
    artifacts = {"rag_hits": [
        {"error": "rag offline"},
        {"score": 0.9, "source": "dense"},  # no concept_id
        {"concept_id": "ok/one", "score": 0.7, "source": "dense",
         "category": "ok", "title": "ok"},
    ]}

    _, hints, _, _ = compose(route, artifacts, "test")

    assert hints["citation_paths"] == ["ok/one"]


def test_tier_0_fallback_clears_citation():
    route = RouteDecision(
        mode="tier_0_fallback", need_rag=False, need_mission_ctx=False,
        need_anchors=False, personas=[], budget_tokens=2000, lazy_artifacts=[],
        reason="guard: non-CS", confidence=0.7,
    )

    md, hints, _, eff = compose(route, {"rag_hits": []}, "오늘 날씨")

    assert eff.mode == "tier_0_fallback"
    assert hints["citation_markdown"] is None
    assert hints["citation_paths"] == []
    assert hints["citation_concept_ids"] == []
    assert hints["tier_downgrade"] is not None
    assert hints["fallback_disclaimer"]
    # Markdown instructs AI to skip 참고 block
    assert "참고:` 블록 출력 금지" in md


def test_no_valid_citation_downgrades_route():
    """RAG was attempted (rag_hits key present) but only error hits arrived."""
    route = _cs_qa_route()
    artifacts = {"rag_hits": [{"error": "offline"}]}

    md, hints, _, eff = compose(route, artifacts, "DI 어떻게")

    assert eff.mode == "tier_0_fallback"
    assert hints["tier_downgrade"] == "no_valid_citation"
    assert "[Mode: tier_0_fallback]" in md
