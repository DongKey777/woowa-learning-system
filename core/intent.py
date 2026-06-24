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
    "mvc", "controller", "service", "dao", "repository", "jdbctemplate",
    "jpa", "mybatis", "n+1", "lazy", "optimistic",
    "spring", "@autowired", "@component", "@configuration",
    "@webmvctest", "@springboottest", "@jdbctest", "java", "optional", "stream api", "lambda",
    "test slice", "테스트 전략", "test double",
    "컴퓨터공학", "알고리즘", "자료구조", "데이터베이스",
    "db", "네트워크", "운영체제", "os", "보안", "security",
    "design pattern", "디자인 패턴", "system design", "시스템 설계",
)
# Explicit mission tokens — always count as mission signal.
MISSION_TOKENS_EXPLICIT = ("미션", "내 코드", "내 pr", "페어")
# Tokens that only count when a repo context is available — "예약/대기"
# alone is ambiguous (could be 식당 예약) so it must be paired with --repo.
MISSION_TOKENS_NEEDS_REPO = ("예약", "대기", "방탈출", "리뷰", "pr")
LEARNING_INTENT = (
    "어떻게", "왜", "언제", "차이", "비교", "어떤 방식", "최선",
    "trade-off", "장단점", "설명", "이해", "알려줘",
    # CS-concept intents that aren't generic "어떻게"
    "종류", "원리", "설정", "메커니즘", "처리법", "vs",
    "생명주기", "benefits", "어디서", "어디에", "구조", "흐름",
    "쓰고", "써야", "피해야", "피하", "사용", "언제 쓰", "when to", "avoid",
)
LEARNING_REQUEST_TOKENS = (
    "확인 질문", "질문 하나", "퀴즈", "연습 문제",
)
# Short CS tokens whose plain `in` match would false-positive (e.g. "di" in
# "dinner") — matched via ASCII-boundary lookaround instead. Also covers
# `@`-prefixed annotations where Python `\b` fails at the `@` edge.
DEFINITION_TOKENS_SHORT_OK = (
    "di", "ioc", "@transactional", "n+1", "jpa", "mvc",
    "@autowired", "@component",
)
_CS_SHORT_TOKENS = {"di", "ioc", "jpa", "mvc", "dao", "db", "os", "n+1"}


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


def has_learning_request(prompt: str) -> bool:
    lp = prompt.lower()
    return any(t in lp for t in LEARNING_REQUEST_TOKENS)


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
        or has_learning_request(prompt)
    )

# Family H (pr_diff_evolution) — multi-word phrases only, checked BEFORE retro.
# None is a substring of the retro guard "내 PR 흐름 보여줘 리뷰" (test_intent_prompt
# :50), so the retro fixture still routes to retro.
PR_DIFF_EVOLUTION_KEYWORDS = (
    "라운드별 코드", "코드 변화", "코드 진화", "리뷰 반영", "리뷰 후 수정",
    "수정 이력", "리뷰 핫스팟", "자주 지적받은 파일", "코드 스멜", "리뷰 전 점검",
)
# Family L (cross_mission) — cross-repo learning. Natural learner phrasing only
# (no stiff "미션 간"/"개념 전이" translationese). Checked BEFORE retro; none is
# a substring of the retro guard "내 PR 흐름 보여줘 리뷰". "반복된/반복되는 실수"
# is more specific than the retro "반복" token and wins by order.
CROSS_MISSION_KEYWORDS = (
    "미션 사이", "미션끼리", "이전 미션", "지난 미션",
    "반복되는 실수", "반복된 실수", "계속 틀리는",
    "미션별 난이도", "미션마다 난이도", "이어서 배운",
)
# Family M (memory_review) — spaced-review / blind-spots. Natural learner
# phrasing (no stiff "기억 점검"/"개념 회상"). Checked BEFORE retro; none is a
# substring of the retro guard "내 PR 흐름 보여줘 리뷰". "사각지대"/"안 물어본"
# don't appear in cross_mission/retro/coaching keys, so order is safe.
MEMORY_REVIEW_KEYWORDS = (
    "복습할 개념", "복습 카드", "복습해야", "다시 봐야 할 개념",
    "까먹은", "잊어버린", "기억나지", "기억 안 나",
    "사각지대", "안 물어본", "한 번도 안 물어",
)
# Family A (pr_review) — received mentor/peer review comments + unresolved
# threads. Natural learner phrasing only. Checked BEFORE retro; none is a
# substring of the retro guard "내 PR 흐름 보여줘 리뷰" so that fixture still
# routes to retro. "받은 리뷰" is more specific than retro's "내가 받은" and
# claims the review-bodies intent (retro = activity timeline, no bodies).
PR_REVIEW_KEYWORDS = (
    "받은 리뷰", "멘토가 남긴", "멘토 코멘트", "리뷰 코멘트 모아",
    "답 안 단 리뷰", "안 단 리뷰", "미해결 리뷰", "아직 답 안",
    "리뷰 정리해", "pr 총평", "리뷰 총평",
)
# Family C (reviewer_profile) — "이 리뷰어/멘토 어떤 사람?" 스타일/성향 분석.
# repo-gated (per-repo archive scoped). Multi-word phrases only; checked after
# pr_review, before coaching, so "리뷰 스타일"/"리뷰 성향" claim the
# reviewer-profile intent rather than coaching's bare "리뷰". None is a
# substring of the retro guard "내 PR 흐름 보여줘 리뷰".
REVIEWER_PROFILE_KEYWORDS = (
    "이 멘토 어떤", "멘토 어떤 사람", "리뷰어 어떤 사람", "리뷰어 스타일",
    "리뷰 스타일", "리뷰 성향", "리뷰 습관", "어떤 리뷰어",
    "누가 주로 리뷰", "리뷰어 분석", "멘토 성향",
)
# Family D (learning_path) — "다음에 뭐 배우면 좋아?" 학습 순서/선수 개념 추천.
# Cross-repo, learner-scoped — NO repo gate. Multi-word phrases only; checked
# after reviewer_profile, before retro. None is a substring of the retro guard
# "내 PR 흐름 보여줘 리뷰". "이어서 배울"(미래)은 cross_mission "이어서 배운"(과거)과
# 글자가 달라 충돌 없음 (cross_mission이 먼저 검사되지만 매칭 안 됨).
LEARNING_PATH_KEYWORDS = (
    "다음에 뭐 배우", "다음에 뭐 공부", "다음에 뭘 공부", "뭐부터 공부",
    "학습 순서", "공부 순서", "선수 개념", "뭘 먼저 알아야",
    "이어서 배울", "다음 단계 개념",
)
# Family J (pr_meta) — PR 메타 위생(본문 품질 / PR 크기 / 커밋 응집). repo-gated
# (per-repo archive scoped). 자연스러운 학습자 표현만 (번역체 X). retro 앞에서
# 검사돼 "pr 너무 큰"/"pr 크기"가 retro의 "내 pr"보다 먼저 잡힌다. 어느 것도 retro
# 가드 "내 pr 흐름 보여줘 리뷰"의 부분문자열이 아니다. "변경량"은 pr_diff_evolution
# "코드 변화"와 글자가 달라 충돌 없음.
PR_META_KEYWORDS = (
    "pr 본문 잘", "pr 설명 잘", "pr 설명 충분", "pr 너무 큰", "pr 너무 커",
    "pr 크기", "변경량 너무", "커밋 너무 잘게", "커밋 잘게 쪼", "커밋 단위",
    "pr 위생",
)
# Family G (thread_recon) — 리뷰 댓글 reply 체인 복원. repo-gated. 다어절 구만.
# coaching의 bare "리뷰"보다 먼저 검사돼 "리뷰 대화"/"리뷰 스레드"가 thread_recon을
# claim한다. reviewer_profile "리뷰 스타일"/"리뷰 성향"과 글자가 달라 충돌 없음.
# retro 가드 "내 pr 흐름 보여줘 리뷰"의 부분문자열 아님.
# NB: "주고받은 리뷰"는 의도적으로 제외 — pr_review의 "받은 리뷰"가 부분문자열로
# 먼저 잡아 절대 thread_recon에 닿지 못한다. "주고받은 대화"로 같은 의미를 커버.
THREAD_RECON_KEYWORDS = (
    "리뷰 대화", "리뷰 스레드", "주고받은 대화",
    "리뷰 토론", "댓글 주고받", "대화 흐름 복원", "스레드 복원",
    "리뷰 대화 복원",
)
# Family I (temporal) — PR 리뷰 사이클의 시간 동학(첫 리뷰까지/머지까지/정체). repo-gated.
# 다어절 구만. pr_diff_evolution "라운드별 코드"와 "라운드 간격"은 글자가 달라 충돌 없음.
# retro 가드의 부분문자열 아님.
TEMPORAL_KEYWORDS = (
    "리뷰 얼마나 걸렸", "리뷰까지 얼마나", "머지까지 얼마나", "얼마나 오래 걸렸",
    "리뷰 주기", "리뷰 텀", "라운드 간격", "리뷰 대기 시간",
    "정체된 구간", "오래 멈춰", "얼마나 걸렸어",
)
# Family E (meta_analytics) — cross-repo 학습 메타: 내가 반복해 묻는 개념/질문 패턴/
# mastery 분포. repo-gate 없음(cross-repo). 다어절 구만. learning_path "다음에 뭐
# 배우"와 글자 달라 충돌 없음. retro 가드의 부분문자열 아님.
META_ANALYTICS_KEYWORDS = (
    "자주 물어본 개념", "반복해서 물어본", "내 학습 패턴", "어떤 질문 많이",
    "학습 메타", "내가 자주 묻는", "질문 패턴", "공부 패턴 분석", "학습 분석",
)
# Family F (cohort) — repo 내 동기/또래 대비 내 PR 위치(크기/리뷰 수/라운드 percentile).
# repo-gated. f11 fast-path("크루 비교"/"비교 의견"/"다른 사람들은")와 글자가 달라
# cross-crew로 새지 않는다. 다어절 구만. retro 가드의 부분문자열 아님.
COHORT_KEYWORDS = (
    "동료들에 비해", "동기들에 비해", "남들에 비해", "또래에 비해",
    "평균에 비해", "내 위치는", "내 순위", "동기들과 비교",
    "코호트 비교", "다른 학습자에 비해",
)
# Family P (peer_compare) — 같은 미션 동기/피어 PR과의 정성 코드 diff 비교(어떤 파일을
# 어떻게 짰나 + 동기가 받은 멘토 지적). repo-gated. cohort(F)의 "비해/위치/순위" 정량
# percentile과 구분되는 코드-비교 의도. cohort보다 먼저 검사돼 "동기 코드 비교"/"동기 풀이
# 비교"가 cohort의 "동기들과 비교"보다 먼저 잡힌다. "내 pr이랑 비교"/"내 코드랑 비교"는
# retro "내 pr"·coaching "내 코드"보다 먼저(더 구체적) 검사돼 비교 의도를 claim한다.
# ⚠ f11 fast-path(cross-crew anchor)는 route()에서 detect_mode보다 먼저 utterance 단위로
# 검사된다. 키워드 자체는 F11_KEYWORDS의 부분문자열이 아니지만, 발화가 별도의 broad F11
# 토큰을 품으면("같은 미션 다른 사람 코드"→F11 "다른 사람 코드"; "...비교 의견"→F11 "비교
# 의견") f11_anchor로 먼저 라우팅된다. 이 경계는 의도된 것(f11도 cross-crew 코드를 surface)
# 이고, AI 세션의 1차 경로인 --mode peer_compare override는 f11 fast-path보다 앞서므로
# 그런 발화도 명시 모드로는 정확히 peer_compare로 간다.
PEER_COMPARE_KEYWORDS = (
    "동기 pr", "피어 pr", "peer pr",
    "동기 코드 비교", "동기 코드랑", "동기들 코드", "동기들은 이 미션",
    "동기 풀이 비교", "같은 미션 다른 사람", "같은 미션 동기",
    "내 pr이랑 비교", "내 코드랑 비교", "피어 비교",
)
# Family K (predict) — C(reviewer_profile)+F(cohort)+H(pr_diff_evolution) 합성한
# 푸시 전 예측. repo-gated. pr_diff_evolution "리뷰 전 점검"/"자주 지적받은 파일"/
# "코드 스멜", pr_review "멘토가 남긴"/"멘토 코멘트", reviewer_profile "멘토 어떤"과
# 글자가 달라 충돌 없음(이들이 모두 predict보다 먼저 검사돼도 substring 안 됨).
PREDICT_KEYWORDS = (
    "멘토가 뭘 지적할", "어떤 지적 받을", "지적받을 만한", "리뷰 미리 예측",
    "올리기 전에 점검", "푸시 전에 점검", "리뷰 예상", "지적 예상",
    "리뷰 시뮬", "리뷰 예측",
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

    for kw in PR_DIFF_EVOLUTION_KEYWORDS:
        if kw in pl:
            return IntentDecision(
                "pr_diff_evolution", f"pr_diff_evolution keyword '{kw}'", 0.85)

    for kw in CROSS_MISSION_KEYWORDS:
        if kw in pl:
            return IntentDecision(
                "cross_mission", f"cross_mission keyword '{kw}'", 0.85)

    for kw in MEMORY_REVIEW_KEYWORDS:
        if kw in pl:
            return IntentDecision(
                "memory_review", f"memory_review keyword '{kw}'", 0.85)

    for kw in PR_REVIEW_KEYWORDS:
        if kw in pl:
            return IntentDecision(
                "pr_review", f"pr_review keyword '{kw}'", 0.85)

    if repo:
        for kw in REVIEWER_PROFILE_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "reviewer_profile", f"reviewer_profile keyword '{kw}'", 0.85)

    for kw in LEARNING_PATH_KEYWORDS:
        if kw in pl:
            return IntentDecision(
                "learning_path", f"learning_path keyword '{kw}'", 0.85)

    if repo:
        for kw in PR_META_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "pr_meta", f"pr_meta keyword '{kw}'", 0.85)

    if repo:
        for kw in THREAD_RECON_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "thread_recon", f"thread_recon keyword '{kw}'", 0.85)

    if repo:
        for kw in TEMPORAL_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "temporal", f"temporal keyword '{kw}'", 0.85)

    if repo:
        for kw in PEER_COMPARE_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "peer_compare", f"peer_compare keyword '{kw}'", 0.85)

    if repo:
        for kw in COHORT_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "cohort", f"cohort keyword '{kw}'", 0.85)

    if repo:
        for kw in PREDICT_KEYWORDS:
            if kw in pl:
                return IntentDecision(
                    "predict", f"predict keyword '{kw}'", 0.85)

    for kw in META_ANALYTICS_KEYWORDS:
        if kw in pl:
            return IntentDecision(
                "meta_analytics", f"meta_analytics keyword '{kw}'", 0.85)

    for kw in RETRO_KEYWORDS:
        if kw in pl:
            return IntentDecision("retro", f"retro keyword '{kw}'", 0.8)

    if repo and any(kw.lower() in pl for kw in COACHING_KEYWORDS):
        return IntentDecision("coaching", f"repo={repo} + coaching keyword matched", 0.85)

    for tok in TOOL_TOKENS:
        if tok in pl:
            return IntentDecision("tool_only", f"tool token '{tok}' (D6 fast-path)", 0.9)

    return IntentDecision("cs_qa", "default — concept/CS question", 0.6)
