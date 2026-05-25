"""Multi-Agent labeled-section composition (D-B).

ONE LLM call carries [MENTOR] + [REVIEWER] + [SOCRATIC] sections. AI session
returns an integrated answer that weaves personas based on the route's
persona list. Saves ×3 token cost vs separate calls while preserving the
2024-25 multi-agent tutoring perspective benefit.

Public API:
  compose(route, artifacts, prompt, repo, learner_id) → markdown_prompt
"""
from __future__ import annotations

from typing import Any

from core.router import (
    PERSONA_MENTOR,
    PERSONA_REVIEWER,
    PERSONA_SOCRATIC,
    RouteDecision,
)


MENTOR_INSTRUCTIONS = (
    "원칙/Best-practice 시각. OOP, SOLID, Spring container 의도, 코드 quality. "
    "이 prompt의 mission_patterns + concept_graph에서 학습자가 사용 중인 패턴의 "
    "prerequisite 누락을 짚어줘."
)
REVIEWER_INSTRUCTIONS = (
    "실제 reviewer 시각. cross_crew_review_graph + review_anchors에서 비슷한 코드에 "
    "다른 reviewer가 한 의견 비교. 동일 패턴에 reviewer 의견이 갈리면 trade-off 명시."
)
SOCRATIC_INSTRUCTIONS = (
    "답을 직접 주지 않고 leading question으로 학습자가 직접 사고하도록. "
    "단 학습자가 막힌 경우(질문이 'X가 뭐야' 같은 정의형) hint만 던지고 정답 유도."
)

PERSONA_INSTRUCTIONS = {
    PERSONA_MENTOR: ("[MENTOR]", MENTOR_INSTRUCTIONS),
    PERSONA_REVIEWER: ("[REVIEWER]", REVIEWER_INSTRUCTIONS),
    PERSONA_SOCRATIC: ("[SOCRATIC]", SOCRATIC_INSTRUCTIONS),
}


def compose(
    route: RouteDecision,
    artifacts: dict[str, Any],
    prompt: str,
    repo: str | None = None,
    learner_id: str = "default",
    recent_history: list[dict] | None = None,
) -> str:
    """Build the full multi-agent prompt for the AI session."""
    parts: list[str] = []
    parts.append(_system_header(route))
    parts.append(_persona_section(route))
    parts.append(_artifact_section(route, artifacts, repo))
    if recent_history:
        parts.append(_recent_history(recent_history))
    parts.append(_user_section(prompt, repo, learner_id))
    parts.append(_answer_format(route))
    return "\n\n".join(p for p in parts if p)


def _system_header(route: RouteDecision) -> str:
    return (
        "# Woowa Learning Hub v2 — Coach prompt\n"
        f"**mode**: `{route.mode}` (reason: {route.reason}, conf={route.confidence:.2f})\n"
        f"**token budget**: {route.budget_tokens} (avg ≤5K, F11 ≤15K)"
    )


def _persona_section(route: RouteDecision) -> str:
    if not route.personas:
        return ""
    lines = ["## Personas (한 답변에 통합, 부적절 persona는 생략)"]
    for p in route.personas:
        label, instr = PERSONA_INSTRUCTIONS.get(p, ("[?]", ""))
        lines.append(f"- {label}: {instr}")
    return "\n".join(lines)


def _artifact_section(route: RouteDecision, artifacts: dict, repo: str | None) -> str:
    if not artifacts:
        return ""
    parts = ["## Loaded artifacts (router-decided lazy load)"]
    if "mastery_graph" in artifacts:
        m = artifacts["mastery_graph"]
        parts.append(
            f"### mastery_graph (tracked={m['summary']['total_tracked']})\n"
            f"counts={m['summary']['counts']}\n"
            f"by_level={_truncate_dict(m.get('by_level', {}), 5)}"
        )
    if "concept_graph" in artifacts:
        cg = artifacts["concept_graph"]
        if cg.get("version") != "missing":
            n_nodes = len(cg.get("nodes", {}))
            parts.append(f"### concept_graph (v={cg.get('version')}, nodes={n_nodes})")
        else:
            parts.append("### concept_graph (NOT YET BUILT — Phase B pending)")
    if "mission_patterns" in artifacts:
        mp = artifacts["mission_patterns"]
        parts.append(
            f"### mission_patterns (repo={mp.get('repo')}, patterns={len(mp.get('patterns', []))})"
            + ("\n(NOT YET BUILT — Phase B pending)" if mp.get("missing") else "")
        )
    if "review_anchors" in artifacts:
        anc = artifacts["review_anchors"]
        parts.append(
            f"### review_anchors (count={len(anc.get('anchors', []))})"
            + ("\n(NOT YET BUILT — Phase C pending)" if anc.get("missing") else "")
        )
    if "cross_crew_review_graph" in artifacts:
        cc = artifacts["cross_crew_review_graph"]
        if cc.get("ready"):
            total = cc.get("total_rows", 0)
            parts.append(f"### cross_crew_review_graph (total={total}, top-{len(cc.get('top_matches', []))} by embed_cosine)")
            for m in cc.get("top_matches", []):
                parts.append(
                    f"  - PR#{m['anchor_pr']} {m['anchor_path']} (mentor={m['anchor_mentor']}) "
                    f"↔ PR#{m['candidate_pr']} crew={m['crew_login']} reviewer={m['candidate_reviewer']} "
                    f"jaccard={m['jaccard']} cos={m['embed_cosine']}"
                )
                snip = m.get("comment_snippet", "").replace("\n", " ")[:160]
                if snip:
                    parts.append(f"    ↳ {snip}")
        else:
            parts.append("### cross_crew_review_graph (NOT YET BUILT — Phase C pending)")
    if "drill_offer" in artifacts:
        do = artifacts["drill_offer"]
        parts.append(f"### drill_offer (concept={do.get('concept_id')})")
        parts.append(f"  question: {do.get('question')}")
        if do.get("expected_terms"):
            parts.append(f"  expected_terms: {do['expected_terms'][:5]}")
    if "rag_hits" in artifacts:
        hits = artifacts["rag_hits"]
        parts.append("### rag_hits (BGE-M3 dense + relations walk, top-5)")
        for h in hits[:5]:
            if "error" in h:
                parts.append(f"  ⚠ {h['error']}")
            else:
                parts.append(f"  - [{h.get('category')}] `{h.get('concept_id')}` — {h.get('title')} "
                             f"(score {h.get('score'):.3f}, src={h.get('source')})")
    return "\n".join(parts)


def _recent_history(history: list[dict]) -> str:
    """At most 20 most-recent events, compact one-line each."""
    items = history[-20:]
    lines = ["## Recent history (≤20 events)"]
    for ev in items:
        et = ev.get("event_type", "?")
        eid = ev.get("event_id", "?")
        lines.append(f"- {et} ({eid})")
    return "\n".join(lines)


def _user_section(prompt: str, repo: str | None, learner_id: str) -> str:
    parts = [f"## Learner turn", f"**learner_id**: {learner_id}"]
    if repo:
        parts.append(f"**repo**: {repo}")
    parts.append(f"**prompt**: {prompt}")
    return "\n".join(parts)


def _answer_format(route: RouteDecision) -> str:
    rules = [
        "## 답변 지침",
        "- 한국어 자연스럽게, 학습자 수준에 맞춰",
        f"- 첫 줄 header: `[Mode: {route.mode}]`",
    ]
    if route.need_rag:
        rules.append("- 답변 마지막에 `참고:\\n- <concept_id>` 형식 인용 (최대 3개)")
    if route.need_mission_ctx:
        rules.append("- 학습자가 자기 코드 직접 쓰도록 (자동 코드 수정 ❌)")
        rules.append("- mission_patterns에 추출된 학습자 코드 위치 인용")
    if route.need_anchors:
        rules.append("- review_anchors + cross_crew 의견 차이 narrate (reviewer별/크루별)")
    if PERSONA_SOCRATIC in route.personas:
        rules.append("- 답 직접 X, leading question 1개 끼워넣어")
    return "\n".join(rules)


def _truncate_dict(d: dict, n: int) -> dict:
    """Keep top-n entries per key for prompt budget."""
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = v[:n]
        else:
            out[k] = v
    return out
