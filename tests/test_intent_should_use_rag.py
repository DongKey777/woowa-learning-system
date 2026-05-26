"""Phase Y11 PR-B — should_use_rag truth table.

Guard formula:
  (has_cs_domain AND has_learning_intent) OR has_mission_signal OR has_definition_signal

TOOL_TOKENS는 RAG 신호 아님 (별도 fast-path).
"""
from __future__ import annotations

import pytest

from core.intent import (
    has_cs_domain,
    has_definition_signal,
    has_learning_intent,
    has_mission_signal,
    should_use_rag,
)


@pytest.mark.parametrize("prompt, repo, expected", [
    # CS domain + learning intent → 통과
    ("Spring DI 어떻게 동작해", None, True),
    ("트랜잭션 isolation level 종류", None, True),
    ("@Transactional 원리 설명", None, True),
    # 정의형 (1-3 token, definition signal) → 통과
    ("DI", None, True),
    ("DI가 뭐야", None, True),
    ("@Transactional 알려줘", None, True),
    ("ioc", None, True),
    # mission signal (explicit) → 통과
    ("내 코드 어떻게 짜야", None, True),
    ("페어 의견 줘", None, True),
    # mission needs-repo: repo 있으면 통과
    ("예약 흐름 봐줘", "myrepo", True),
    ("리뷰 분석 부탁", "myrepo", True),
    # non-CS prompt — 차단
    ("오늘 날씨 어때", None, False),
    ("오늘 날씨 왜 이래", None, False),
    ("요리 어떻게 해", None, False),
    ("food 추천", None, False),
    # mission needs-repo: repo 없으면 차단
    ("예약 흐름 봐줘", None, False),
    ("식당 예약 어떻게", None, False),
    # short token false-positive 차단 (G5 boundary)
    ("dinner 추천", None, False),
    ("ipod 추천", None, False),
])
def test_should_use_rag_truth_table(prompt, repo, expected):
    assert should_use_rag(prompt, repo) is expected, (
        f"prompt={prompt!r}, repo={repo!r}: "
        f"cs_domain={has_cs_domain(prompt)} "
        f"intent={has_learning_intent(prompt)} "
        f"mission={has_mission_signal(prompt, repo)} "
        f"definition={has_definition_signal(prompt)}"
    )


def test_has_cs_domain_boundary_rejects_substring_false_positive():
    # "di" should not match "dinner"
    assert has_cs_domain("dinner 추천") is False
    # but should match "DI가 뭐야" (Korean josa)
    assert has_cs_domain("DI가 뭐야") is True


def test_has_cs_domain_at_prefix_token():
    # `\b@transactional\b` would fail on the `@` edge; ASCII boundary works.
    assert has_cs_domain("@Transactional 알려줘") is True
    assert has_cs_domain("@transactional이 뭐야") is True


def test_has_mission_signal_requires_repo_for_ambiguous_token():
    # "예약" alone is ambiguous (식당 예약 가능성)
    assert has_mission_signal("예약 흐름 봐줘", repo=None) is False
    assert has_mission_signal("예약 흐름 봐줘", repo="myrepo") is True
    # explicit mission tokens always pass
    assert has_mission_signal("내 코드 어떻게", repo=None) is True


def test_has_definition_signal_only_for_short_prompts():
    # 1-3 tokens with CS definition token → True
    assert has_definition_signal("DI") is True
    assert has_definition_signal("DI 가 뭐야") is True
    # >3 tokens → not a definition prompt
    assert has_definition_signal("DI 가 정확히 무엇인지 자세히 알려줘") is False
