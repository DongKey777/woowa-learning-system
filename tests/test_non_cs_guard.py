"""Phase Y11 PR-B — non-CS guard via router.route() integration.

Verifies the 10 acceptance scenarios from plan §Acceptance Criteria.
"""
from __future__ import annotations

import pytest

from core.router import route


@pytest.mark.parametrize("prompt, repo, expected_mode", [
    # Non-CS prompts → tier_0_fallback (P0.5 guard fires)
    ("오늘 날씨 어때", None, "tier_0_fallback"),
    ("오늘 날씨 왜 이래", None, "tier_0_fallback"),
    ("식당 예약 어떻게", None, "tier_0_fallback"),
    ("dinner 추천", None, "tier_0_fallback"),
    # CS prompts → cs_qa (guard 통과)
    ("Spring DI 어떻게", None, "cs_qa"),
    ("DI", None, "cs_qa"),
    ("DI가 뭐야", None, "cs_qa"),
    ("@Transactional 알려줘", None, "cs_qa"),
    # Tool fast-path bypasses guard
    ("git rebase 어떻게", None, "tool_only"),
])
def test_route_guard_acceptance_cases(prompt, repo, expected_mode):
    decision = route(prompt, repo=repo)
    assert decision.mode == expected_mode, (
        f"{prompt!r} → got {decision.mode}, expected {expected_mode}; "
        f"reason={decision.reason}"
    )


def test_tier_0_fallback_has_no_rag():
    decision = route("오늘 날씨", repo=None)
    assert decision.mode == "tier_0_fallback"
    assert decision.need_rag is False
    assert decision.lazy_artifacts == []


def test_pending_drill_bypasses_guard():
    """Phase Y11 H1 — guard는 cs_qa 분기 안에서만. pending drill/self_assess/
    tool/retro/F11은 guard와 무관하게 자기 mode 유지.

    "메모리 누수는 ..." 의 "수는" + 6+ char가
    DRILL_ANSWER_HINT_PATTERN ``(은|는|이|가)\\s.{6,}`` 매치."""
    decision = route(
        "메모리 누수는 GC root에 의한 reference holding 때문이지",
        repo=None,
        pending_drill={"concept_id": "x", "question": "?"},
    )
    assert decision.mode == "drill"


def test_repo_context_admits_needs_repo_tokens():
    # "예약" alone (no repo) → guard fires
    assert route("예약 흐름", repo=None).mode == "tier_0_fallback"
    # With repo → mission signal → cs_qa default
    assert route("예약 흐름", repo="myrepo").mode == "cs_qa"
