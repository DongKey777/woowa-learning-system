"""Phase Y11 PR-C — tier_downgrade route derive + threshold contract.

When compose() downgrades a cs_qa turn to tier_0_fallback (either via
non-CS guard, no-valid-citation, or threshold), the effective_route must
also flip (need_rag=False, mode='tier_0_fallback') so _answer_format
instructs the AI to skip citation block + emit fallback_disclaimer.
"""
from __future__ import annotations

import os

import pytest

from core.coach import compose
from core.router import (
    ARTIFACT_CONCEPT_GRAPH,
    PERSONA_MENTOR,
    RouteDecision,
    get_refusal_threshold,
)


def _cs_route():
    return RouteDecision(
        mode="cs_qa", need_rag=True, need_mission_ctx=False, need_anchors=False,
        personas=[PERSONA_MENTOR], budget_tokens=4500,
        lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH],
        reason="default — concept/CS question", confidence=0.6,
    )


def _hits(*pairs):
    return [
        {"concept_id": cid, "score": score, "source": "dense",
         "category": cid.split("/", 1)[0], "title": cid}
        for cid, score in pairs
    ]


def test_threshold_off_by_default(monkeypatch):
    monkeypatch.delenv("WOOWA_RAG_REFUSAL_THRESHOLD", raising=False)
    assert get_refusal_threshold() is None

    artifacts = {"rag_hits": _hits(("spring/di", 0.3))}
    _, hints, _, eff = compose(_cs_route(), artifacts, "DI")
    # 0.3 is low, but threshold off ⇒ no downgrade
    assert eff.mode == "cs_qa"
    assert hints["tier_downgrade"] is None


def test_threshold_forces_downgrade_when_enabled(monkeypatch):
    monkeypatch.setenv("WOOWA_RAG_REFUSAL_THRESHOLD", "0.5")
    assert get_refusal_threshold() == 0.5

    artifacts = {"rag_hits": _hits(("spring/di", 0.3))}
    _, hints, _, eff = compose(_cs_route(), artifacts, "DI")

    assert eff.mode == "tier_0_fallback"
    assert eff.need_rag is False
    assert hints["tier_downgrade"] == "corpus_gap_no_confident_match"
    assert hints["citation_markdown"] is None


def test_threshold_preserves_route_when_score_high(monkeypatch):
    monkeypatch.setenv("WOOWA_RAG_REFUSAL_THRESHOLD", "0.5")
    artifacts = {"rag_hits": _hits(("spring/di", 0.85))}

    _, hints, _, eff = compose(_cs_route(), artifacts, "DI")

    assert eff.mode == "cs_qa"
    assert hints["tier_downgrade"] is None
    assert hints["citation_markdown"]  # non-null paste-ready block


def test_downgrade_updates_effective_route_reason():
    artifacts = {"rag_hits": []}
    _, _, _, eff = compose(_cs_route(), artifacts, "DI")

    assert eff.mode == "tier_0_fallback"
    assert "tier downgrade" in eff.reason
    assert eff.need_rag is False
    assert eff.personas == []


def test_invalid_threshold_env_treated_as_off(monkeypatch):
    monkeypatch.setenv("WOOWA_RAG_REFUSAL_THRESHOLD", "not-a-float")
    assert get_refusal_threshold() is None


@pytest.mark.parametrize("value", ["off", "OFF", "none", "", " "])
def test_off_aliases(monkeypatch, value):
    monkeypatch.setenv("WOOWA_RAG_REFUSAL_THRESHOLD", value)
    assert get_refusal_threshold() is None
