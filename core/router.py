"""Self-Routing decision layer (D-A). Wraps intent.detect_mode + adds
artifact/persona/budget guidance for lazy_loader and coach.

Deterministic Python — no extra LLM call. The AI session is only invoked
later in core/coach.py with the final composed prompt.

Output `RouteDecision` tells the rest of the system:
- which mode (cs_qa, coaching, drill, retro, self_assess, tool_only, f11_anchor)
- which lazy artifacts to load (subset of 5)
- which multi-agent personas to compose (subset of 3)
- token budget for this turn (avg 5K, F11 up to 15K)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.intent import IntentDecision, detect_mode

# F11 explicit triggers — line-level cross-crew/reviewer analysis intent
F11_KEYWORDS = (
    "정밀 비교", "정밀비교", "정밀", "review thread", "리뷰 의견", "리뷰어 의견",
    "다른 크루 리뷰", "다른 크루 의견", "다른 크루는", "다른 사람들은",
    "cross-crew", "anchor", "앵커", "비교 의견", "다른 reviewer",
    "다양한 시각", "여러 의견",
)
F11_PR_LINE_PATTERN = re.compile(r"pr\s*#?\s*\d+.*(line|줄|thread)", re.IGNORECASE)

ARTIFACT_CONCEPT_GRAPH = "concept_graph"
ARTIFACT_MISSION_PATTERNS = "mission_patterns"
ARTIFACT_MASTERY = "mastery_graph"
ARTIFACT_ANCHORS = "review_anchors"
ARTIFACT_CROSS_CREW = "cross_crew_review_graph"

PERSONA_MENTOR = "mentor"
PERSONA_REVIEWER = "reviewer"
PERSONA_SOCRATIC = "socratic"


@dataclass(frozen=True)
class RouteDecision:
    mode: str
    need_rag: bool
    need_mission_ctx: bool
    need_anchors: bool
    personas: list[str]
    budget_tokens: int
    lazy_artifacts: list[str]
    reason: str
    confidence: float = field(default=0.0)


def route(
    prompt: str,
    repo: str | None = None,
    pending_self_assessment: dict | None = None,
    pending_drill: dict | None = None,
) -> RouteDecision:
    """Decide mode + artifact/persona/budget. Combines intent fast-path
    with F11 trigger detection and budget shaping."""
    # F11 takes priority over coaching/retro when explicit
    if _is_f11_trigger(prompt):
        return RouteDecision(
            mode="f11_anchor",
            need_rag=False,
            need_mission_ctx=True,
            need_anchors=True,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=12000,
            lazy_artifacts=[ARTIFACT_ANCHORS, ARTIFACT_CROSS_CREW, ARTIFACT_MISSION_PATTERNS],
            reason="F11 explicit trigger (cross-crew review analysis)",
            confidence=0.9,
        )

    intent = detect_mode(
        prompt,
        repo=repo,
        pending_self_assessment=pending_self_assessment,
        pending_drill=pending_drill,
    )
    return _from_intent(intent)


def _from_intent(intent: IntentDecision) -> RouteDecision:
    mode = intent.mode

    if mode == "tool_only":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[], budget_tokens=1500, lazy_artifacts=[],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "cs_qa":
        return RouteDecision(
            mode=mode, need_rag=True, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_SOCRATIC],
            budget_tokens=4500,
            lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "coaching":
        return RouteDecision(
            mode=mode, need_rag=True, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_REVIEWER, PERSONA_SOCRATIC],
            budget_tokens=5500,
            lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH, ARTIFACT_MISSION_PATTERNS, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "drill":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_SOCRATIC],
            budget_tokens=3000,
            lazy_artifacts=[ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "retro":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_REVIEWER],
            budget_tokens=5000,
            lazy_artifacts=[ARTIFACT_MISSION_PATTERNS, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "self_assess":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[],
            budget_tokens=2000,
            lazy_artifacts=[ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    # fallback: treat as cs_qa minimal
    return RouteDecision(
        mode="cs_qa", need_rag=True, need_mission_ctx=False, need_anchors=False,
        personas=[PERSONA_MENTOR],
        budget_tokens=4000,
        lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH],
        reason=f"fallback (unknown mode {mode!r})", confidence=0.3,
    )


def _is_f11_trigger(prompt: str) -> bool:
    pl = prompt.lower()
    if any(kw.lower() in pl for kw in F11_KEYWORDS):
        return True
    if F11_PR_LINE_PATTERN.search(prompt):
        return True
    return False
