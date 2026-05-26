"""Intent dispatcher — choose mode for unified bin/ask entry (D13+D14).

Heuristic 1st pass returns one of:
  "self_assess" | "drill" | "retro" | "coaching" | "cs_qa" | "tool_only"

AI session in Phase 6.5 may override if it disagrees, but 95% accuracy on
the 50-sample falsification set (D13) must come from this layer.

Priority order (first match wins):
  1. pending self-assessment + short score-like reply → self_assess
  2. pending drill + answer-shaped reply → drill
  3. retro keywords (회고/반복/내 PR/정밀) → retro
  4. repo specified + coaching keywords → coaching
  5. TOOL_TOKENS in prompt → tool_only
  6. default → cs_qa
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# D6: ONLY tool tokens kept (legacy CS_DOMAIN/LEARNING/DEPTH lexicons removed)
TOOL_TOKENS = (
    "gradle", "maven", "git ", "github actions", "intellij", "vscode",
    "homebrew", "brew install", "brew ", "npm install", "npm ", "pip install", "pip ",
    "docker run", "docker-compose", "docker ", "kubectl",
)

# Phase Y11 P0.5 — non-CS prompt guard. Minimum CS/MISSION/LEARNING token
# set so `should_use_rag()` can reject prompts like "오늘 날씨 어때" before
# they reach the cs_qa default route and attach unrelated corpus hits.
CS_TOKENS = (
    "transactional", "트랜잭션", "isolation",
    "bean", "di", "ioc", "dependency injection",
    "mvc", "controller", "service", "dao", "repository",
    "jpa", "mybatis", "n+1", "lazy", "optimistic",
    "spring", "@autowired", "@component", "@configuration",
    "@webmvctest", "@springboottest", "@jdbctest",
    "test slice", "테스트 전략", "test double",
)
# Explicit mission tokens — always count as mission signal.
MISSION_TOKENS_EXPLICIT = ("미션", "내 코드", "내 pr", "페어")
# Tokens that only count when a repo context is available — "예약/대기"
# alone is ambiguous (could be 식당 예약) so it must be paired with --repo.
MISSION_TOKENS_NEEDS_REPO = ("예약", "대기", "방탈출", "리뷰", "pr")
LEARNING_INTENT = (
    "어떻게", "왜", "차이", "비교", "어떤 방식", "최선",
    "trade-off", "장단점", "설명", "이해", "알려줘",
    # CS-concept intents that aren't generic "어떻게"
    "종류", "원리", "설정", "메커니즘", "처리법", "vs",
    "생명주기", "benefits", "어디서", "구조", "흐름",
)
# Short CS tokens whose plain `in` match would false-positive (e.g. "di" in
# "dinner") — matched via ASCII-boundary lookaround instead. Also covers
# `@`-prefixed annotations where Python `\b` fails at the `@` edge.
DEFINITION_TOKENS_SHORT_OK = (
    "di", "ioc", "@transactional", "n+1", "jpa", "mvc",
    "@autowired", "@component",
)
_CS_SHORT_TOKENS = {"di", "ioc", "jpa", "mvc", "dao", "n+1"}


def _ascii_boundary_pattern(token: str) -> re.Pattern:
    """Match `token` only when bordered by non-`[A-Za-z0-9_]` (or string
    edge). Python's `\\b` fails on `@`-prefixed tokens and matches inside
    Korean (which we want); this lookaround keeps the Korean-josa behaviour
    ("DI가" matches "di") while rejecting "dinner"."""
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )


_CS_PATTERNS = [
    _ascii_boundary_pattern(t)
    if (t in _CS_SHORT_TOKENS or len(t) <= 3 or t.startswith("@"))
    else None
    for t in CS_TOKENS
]


def has_cs_domain(prompt: str) -> bool:
    """Short tokens use ASCII-boundary regex; longer tokens use plain
    substring (already unambiguous in Korean/English context)."""
    lp = prompt.lower()
    for token, pattern in zip(CS_TOKENS, _CS_PATTERNS):
        if pattern is not None:
            if pattern.search(prompt):
                return True
        elif token in lp:
            return True
    return False


def has_mission_signal(prompt: str, repo: str | None = None) -> bool:
    """Explicit mission token OR (needs-repo token AND repo present).
    Without a repo context, "예약" alone doesn't count as mission signal."""
    lp = prompt.lower()
    if any(t in lp for t in MISSION_TOKENS_EXPLICIT):
        return True
    if repo is not None and any(t in lp for t in MISSION_TOKENS_NEEDS_REPO):
        return True
    return False


def has_learning_intent(prompt: str) -> bool:
    lp = prompt.lower()
    return any(t in lp for t in LEARNING_INTENT)


def has_definition_signal(prompt: str) -> bool:
    """Short, definition-form CS prompts pass even without an explicit
    learning intent.

    Two cases:
    1. 1-3 token prompts with a known short-token (DI, IOC, @Transactional…)
       → handles "DI", "DI가 뭐야", "@Transactional 알려줘".
    2. Short multi-token CS phrases (≤4 tokens) containing any CS domain token
       → handles "트랜잭션 isolation level", "Spring Bean 생명주기".
       Without this, CS concept lookups without explicit "어떻게/왜" intent
       would be wrongly downgraded to tier_0_fallback.
    """
    stripped = prompt.strip().lower()
    tokens = stripped.split()
    if len(tokens) <= 3:
        for t in DEFINITION_TOKENS_SHORT_OK:
            if _ascii_boundary_pattern(t).search(stripped):
                return True
    if len(tokens) <= 4 and has_cs_domain(prompt):
        return True
    return False


def should_use_rag(prompt: str, repo: str | None = None) -> bool:
    """Phase Y11 P0.5 guard: a prompt should drive RAG retrieval only when
    (CS domain AND learning intent) OR mission signal OR definition signal.

    TOOL_TOKENS are NOT treated as RAG signal here — they're a separate
    fast-path handled inside ``detect_mode``."""
    return (
        (has_cs_domain(prompt) and has_learning_intent(prompt))
        or has_mission_signal(prompt, repo)
        or has_definition_signal(prompt)
    )

RETRO_KEYWORDS = (
    "회고", "반복", "내 pr", "정밀", "내가 놓친", "pr 흐름", "내 흐름", "pr 타임라인",
    "사이클", "이전 pr", "내가 받은", "pr 시리즈", "활동 요약", "history",
)
COACHING_KEYWORDS = (
    "코칭", "리뷰", "어떻게 해야", "내 코드", "내 작업", "리팩토링", "고치",
    "어떻게 짜", "이 코드", "쓰는 게", "써야", "어떻게 써", "사용해야",
)

SCORE_LIKE_PATTERN = re.compile(r"^\s*(\d{1,2})\s*점\s*$|^\s*잘\s*몰라|^\s*모르겠어|^\s*([0-9]\s*/\s*10)\s*$")
DRILL_ANSWER_HINT_PATTERN = re.compile(r"(은|는|이|가)\s.{6,}", re.MULTILINE)


@dataclass(frozen=True)
class IntentDecision:
    mode: str
    reason: str
    confidence: float  # 0..1


def detect_mode(
    prompt: str,
    repo: str | None = None,
    pending_self_assessment: dict | None = None,
    pending_drill: dict | None = None,
) -> IntentDecision:
    pl = prompt.lower()

    # Short greeting / acknowledgement → tool_only (no RAG, no coaching)
    if len(prompt.strip()) <= 3 and not any(c.isalnum() for c in prompt[-1:]) or \
       prompt.strip() in {"안녕", "ㅋㅋ", "ㅎㅎ", "ok", "OK", "응", "넵", "넹", "ㅇㅇ"}:
        return IntentDecision("tool_only", "short greeting / ack — no retrieval", 0.7)

    if pending_self_assessment and SCORE_LIKE_PATTERN.search(prompt):
        return IntentDecision("self_assess", "pending self_assessment + score-like reply", 0.95)

    if pending_drill and (
        SCORE_LIKE_PATTERN.search(prompt) or DRILL_ANSWER_HINT_PATTERN.search(prompt)
    ):
        return IntentDecision("drill", "pending drill + answer-shaped reply", 0.85)

    for kw in RETRO_KEYWORDS:
        if kw in pl:
            return IntentDecision("retro", f"retro keyword '{kw}'", 0.8)

    if repo and any(kw.lower() in pl for kw in COACHING_KEYWORDS):
        return IntentDecision("coaching", f"repo={repo} + coaching keyword matched", 0.85)

    for tok in TOOL_TOKENS:
        if tok in pl:
            return IntentDecision("tool_only", f"tool token '{tok}' (D6 fast-path)", 0.9)

    return IntentDecision("cs_qa", "default — concept/CS question", 0.6)
