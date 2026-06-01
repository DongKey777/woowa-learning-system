"""Self-routing layer: mode + artifacts + personas + token budget."""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.intent import IntentDecision, detect_mode, should_use_rag


def get_refusal_threshold() -> float | None:
    """Opt-in cosine threshold for tier_0 downgrade; default off."""
    raw = os.environ.get("WOOWA_RAG_REFUSAL_THRESHOLD", "off").strip().lower()
    if raw in ("off", "", "none"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# Tuned on real learning-mode tier_0 prompts (Pillar 2): the genuine CS cluster
# (MVCC / 락 / 격리 questions) sits at cosine 0.56-0.66, guard fixtures
# (weather/dinner) at 0.40-0.47, with the adversarial "식당 예약 어떻게" guard at
# 0.545. 0.56 clears that guard with margin while rescuing every labeled CS
# prompt. Gate only fires when route() is given a nearest_concept_cosine
# callback (daemon only), so direct callers stay backward-compatible regardless.
DEFAULT_SEMANTIC_ROUTER_THRESHOLD = "0.56"


def get_semantic_router_threshold() -> float | None:
    """Cosine threshold to rescue a lexically-guarded cs_qa prompt back into
    retrieval. When the lexical CS_TOKENS guard would drop a cs_qa prompt to
    tier_0_fallback, a nearest-concept cosine at or above this threshold
    overrides the drop. Set env to 'off' to disable (kill-switch)."""
    raw = os.environ.get(
        "WOOWA_SEMANTIC_ROUTER_THRESHOLD", DEFAULT_SEMANTIC_ROUTER_THRESHOLD
    ).strip().lower()
    if raw in ("off", "", "none"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None

F11_KEYWORDS = (
    "정밀 비교", "정밀비교", "정밀", "review thread", "리뷰 의견", "리뷰어 의견",
    "다른 크루 리뷰", "다른 크루 의견", "다른 크루는", "다른 사람들은",
    "크루 비교", "크루비교", "크루들 비교",
    "cross-crew", "anchor", "앵커", "비교 의견", "다른 reviewer",
    "다양한 시각", "여러 의견",
    # B 발견성: 자연어 진입 — 다른 크루/사람의 실제 "코드/풀이/구현"을 보고 싶은
    # 발화. cohort(F)의 "비해/위치/순위" 지표 비교와 구분되는 코드 비교 의도.
    "다른 크루 코드", "다른 사람 코드", "동료 코드", "다른 풀이",
    "다른 구현 보여", "남들은 어떻게 풀", "남들은 어떻게 짰", "다른 크루 풀이",
)
F11_PR_LINE_PATTERN = re.compile(r"pr\s*#?\s*\d+.*(line|줄|thread)", re.IGNORECASE)

ARTIFACT_CONCEPT_GRAPH = "concept_graph"
ARTIFACT_MISSION_PATTERNS = "mission_patterns"
ARTIFACT_MASTERY = "mastery_graph"
ARTIFACT_ANCHORS = "review_anchors"
ARTIFACT_CROSS_CREW = "cross_crew_review_graph"
ARTIFACT_PR_DIFF_EVOLUTION = "pr_diff_evolution"
ARTIFACT_CROSS_MISSION = "cross_mission"
ARTIFACT_MEMORY_REVIEW = "memory_review"
ARTIFACT_PR_REVIEW = "pr_review"
ARTIFACT_REVIEWER_PROFILE = "reviewer_profile"
ARTIFACT_LEARNING_PATH = "learning_path"
ARTIFACT_PR_META = "pr_meta"
ARTIFACT_THREAD_RECON = "thread_recon"
ARTIFACT_TEMPORAL = "temporal"
ARTIFACT_META_ANALYTICS = "meta_analytics"
ARTIFACT_COHORT = "cohort"
ARTIFACT_PREDICT = "predict"

PERSONA_MENTOR = "mentor"
PERSONA_REVIEWER = "reviewer"
PERSONA_SOCRATIC = "socratic"
OVERRIDE_TOKENS = {
    "force_coach": ("코치 모드", "coach mode"),
    "force_full": ("rag로 깊게", "심도 있게", "depth", "full rag"),
    "force_min_rag": ("rag로 답해", "근거 보여줘", "with rag"),
    "force_skip": ("그냥 답해", "rag 빼고", "skip rag"),
}


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
    nearest_concept_cosine: Callable[[str], float | None] | None = None,
    mode_override: str | None = None,
) -> RouteDecision:
    override_decision = _route_override(prompt, repo)
    if override_decision is not None:
        return override_decision

    # AI-session-driven routing: the AI reads the learner's message and may
    # pass an explicit mode. It wins over keyword detection, which generalizes
    # poorly to paraphrase (router_paraphrase_recall ≈ 3.8% on held-out
    # phrasing). Absent/unknown → fall through to keyword routing, so headless
    # callers, other agents, and every eval gate keep today's deterministic
    # behavior.
    forced = _mode_override_decision(mode_override)
    if forced is not None:
        return forced

    if _is_f11_trigger(prompt):
        return _f11_decision()

    intent = detect_mode(
        prompt,
        repo=repo,
        pending_self_assessment=pending_self_assessment,
        pending_drill=pending_drill,
    )

    # Phase Y11 P0.5 — only the cs_qa *default* branch is checked; pending
    # drill/self-assess, tool_only, retro, coaching, F11 (above) keep their
    # legitimate routes.
    if intent.mode == "cs_qa" and not should_use_rag(prompt, repo):
        threshold = get_semantic_router_threshold()
        if threshold is not None and nearest_concept_cosine is not None:
            cosine = nearest_concept_cosine(prompt)
            if cosine is not None and cosine >= threshold:
                return _from_intent(
                    intent,
                    reason_override=(
                        f"semantic rescue: lexical guard missed but nearest "
                        f"concept cosine {cosine:.3f} >= {threshold:.3f}"
                    ),
                )
        return RouteDecision(
            mode="tier_0_fallback",
            need_rag=False,
            need_mission_ctx=False,
            need_anchors=False,
            personas=[],
            budget_tokens=2000,
            lazy_artifacts=[],
            reason="guard: non-CS prompt (no domain+intent or mission signal)",
            confidence=0.7,
        )

    return _from_intent(intent)


def _from_intent(intent: IntentDecision, reason_override: str | None = None) -> RouteDecision:
    mode = intent.mode
    reason = reason_override if reason_override is not None else intent.reason

    if mode == "tool_only":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[], budget_tokens=1500, lazy_artifacts=[],
            reason=reason, confidence=intent.confidence,
        )
    if mode == "cs_qa":
        return RouteDecision(
            mode=mode, need_rag=True, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_SOCRATIC],
            budget_tokens=4500,
            lazy_artifacts=[ARTIFACT_CONCEPT_GRAPH, ARTIFACT_MASTERY],
            reason=reason, confidence=intent.confidence,
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
    if mode == "pr_diff_evolution":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=9000,
            lazy_artifacts=[ARTIFACT_PR_DIFF_EVOLUTION, ARTIFACT_MISSION_PATTERNS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "cross_mission":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_MENTOR],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_CROSS_MISSION, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "memory_review":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_MENTOR],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_MEMORY_REVIEW, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "pr_review":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=True,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=7000,
            lazy_artifacts=[ARTIFACT_PR_REVIEW, ARTIFACT_ANCHORS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "reviewer_profile":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_REVIEWER_PROFILE, ARTIFACT_MISSION_PATTERNS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "learning_path":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_SOCRATIC],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_LEARNING_PATH, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "pr_meta":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_PR_META, ARTIFACT_MISSION_PATTERNS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "thread_recon":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_THREAD_RECON, ARTIFACT_MISSION_PATTERNS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "temporal":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_REVIEWER],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_TEMPORAL, ARTIFACT_MISSION_PATTERNS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "meta_analytics":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=False, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_SOCRATIC],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_META_ANALYTICS, ARTIFACT_MASTERY],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "cohort":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_MENTOR, PERSONA_REVIEWER],
            budget_tokens=6000,
            lazy_artifacts=[ARTIFACT_COHORT, ARTIFACT_MISSION_PATTERNS],
            reason=intent.reason, confidence=intent.confidence,
        )
    if mode == "predict":
        return RouteDecision(
            mode=mode, need_rag=False, need_mission_ctx=True, need_anchors=False,
            personas=[PERSONA_REVIEWER, PERSONA_MENTOR],
            budget_tokens=7000,
            lazy_artifacts=[ARTIFACT_PREDICT, ARTIFACT_MISSION_PATTERNS],
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


def _f11_decision() -> RouteDecision:
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


# Router modes the AI session may force via an explicit mode_override. f11_anchor
# is built inline (route() fast-path), the rest flow through _from_intent's
# mode-keyed dispatch. tier_0_fallback is intentionally excluded — it's a guard
# outcome, never an intent the AI should select.
_VALID_OVERRIDE_MODES = frozenset({
    "tool_only", "cs_qa", "coaching", "drill", "retro", "self_assess",
    "pr_diff_evolution", "cross_mission", "memory_review", "pr_review",
    "reviewer_profile", "learning_path", "pr_meta", "thread_recon", "temporal",
    "meta_analytics", "cohort", "predict", "f11_anchor",
})


def _mode_override_decision(mode_override: str | None) -> RouteDecision | None:
    """Build a RouteDecision for an AI-session-supplied mode, or None to defer
    to keyword routing when the override is absent or unrecognized."""
    if not mode_override:
        return None
    mode = mode_override.strip()
    if mode not in _VALID_OVERRIDE_MODES:
        return None
    if mode == "f11_anchor":
        return _f11_decision()
    return _from_intent(IntentDecision(
        mode=mode,
        reason=f"ai-session mode override: {mode}",
        confidence=0.95,
    ))


def _is_f11_trigger(prompt: str) -> bool:
    pl = prompt.lower()
    if any(kw.lower() in pl for kw in F11_KEYWORDS):
        return True
    if F11_PR_LINE_PATTERN.search(prompt):
        return True
    return False


def _match_override(prompt: str) -> str | None:
    pl = prompt.lower()
    for kind, tokens in OVERRIDE_TOKENS.items():
        if any(token in pl for token in tokens):
            return kind
    return None


def strip_override_tokens(prompt: str) -> str:
    cleaned = prompt
    for tokens in OVERRIDE_TOKENS.values():
        for token in tokens:
            cleaned = re.sub(re.escape(token), " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" .,:;，。") or prompt


def _route_override(prompt: str, repo: str | None) -> RouteDecision | None:
    override = _match_override(prompt)
    if override == "force_skip":
        return RouteDecision("tier_0_fallback", False, False, False, [], 2000, [],
                             "user override: skip RAG", 0.95)
    if override in {"force_full", "force_min_rag"}:
        reason = "user override: full RAG" if override == "force_full" else "user override: with RAG"
        return RouteDecision("cs_qa", True, False, False,
                             [PERSONA_MENTOR, PERSONA_SOCRATIC],
                             5500 if override == "force_full" else 4500,
                             [ARTIFACT_CONCEPT_GRAPH, ARTIFACT_MASTERY],
                             reason, 0.95)
    if override == "force_coach":
        reason = "user override: coach mode" if repo else "user override: coach mode (repo missing)"
        return RouteDecision("coaching", True, True, False,
                             [PERSONA_MENTOR, PERSONA_REVIEWER, PERSONA_SOCRATIC],
                             5500, [ARTIFACT_CONCEPT_GRAPH, ARTIFACT_MISSION_PATTERNS, ARTIFACT_MASTERY],
                             reason, 0.95)
    return None
