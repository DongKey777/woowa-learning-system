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
    "homebrew", "brew install", "npm install", "pip install",
    "docker run", "docker-compose", "kubectl",
)

RETRO_KEYWORDS = ("회고", "반복", "내 pr", "정밀", "내가 놓친", "pr 흐름", "내 흐름", "pr 타임라인")
COACHING_KEYWORDS = ("코칭", "리뷰", "어떻게 해야", "내 코드", "내 작업", "리팩토링", "고치", "PR", "어떻게 짜")

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
