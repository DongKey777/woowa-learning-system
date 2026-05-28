"""Multi-Agent labeled-section composition (D-B).

ONE LLM call carries [MENTOR] + [REVIEWER] + [SOCRATIC] sections. AI session
returns an integrated answer that weaves personas based on the route's
persona list. Saves ×3 token cost vs separate calls while preserving the
2024-25 multi-agent tutoring perspective benefit.

Public API (Phase Y11):
  compose(route, artifacts, prompt, repo, learner_id, ...)
    → (markdown_prompt, response_hints, response_quality_hint, effective_route)

`effective_route` is the post-downgrade route (so daemon/payload/markdown
header stay consistent when a non-CS prompt or low-score RAG hit triggers
tier_0_fallback). `response_quality_hint` is None on cold paths where no
history append happens (bin/ask --no-daemon).
"""
from __future__ import annotations

import dataclasses
import shlex
from pathlib import Path
from typing import Any

from core.response import render_citation_block
from core.router import (
    PERSONA_MENTOR,
    PERSONA_REVIEWER,
    PERSONA_SOCRATIC,
    RouteDecision,
    get_refusal_threshold,
)
from core.state import DEFAULT_STATE_ROOT


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


FALLBACK_DISCLAIMER = (
    "코퍼스에 이 주제의 신뢰할 만한 자료가 없어 일반 지식 기반으로 답한다. "
    "정확성 검증이 필요하면 알려줘."
)


def _pick_top3(rag_hits: list[dict]) -> list[dict]:
    """Return up to 3 hits with concept_id + no error, deduped + ordered."""
    seen: set[str] = set()
    out: list[dict] = []
    for h in rag_hits or []:
        if not isinstance(h, dict):
            continue
        if "error" in h:
            continue
        cid = h.get("concept_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(h)
        if len(out) == 3:
            break
    return out


def _build_response_hints(
    route: RouteDecision,
    valid_hits: list[dict],
    downgrade_reason: str | None,
    reformulated_query: str | None,
) -> dict:
    """Build the response_hints dict that the AI session pastes verbatim.

    On downgrade we surface fallback_disclaimer + empty citation; otherwise
    we emit paste-ready `참고:` markdown so the AI doesn't hand-write
    concept_id paths (a known hallucination vector).
    """
    if downgrade_reason or route.mode == "tier_0_fallback":
        return {
            "citation_markdown": None,
            "citation_paths": [],
            "citation_concept_ids": [],
            "citation_trace": [],
            "tier_downgrade": downgrade_reason or "tier_0_fallback",
            "fallback_disclaimer": FALLBACK_DISCLAIMER,
            "reformulated_query": reformulated_query,
        }
    cids = [h["concept_id"] for h in valid_hits]
    trace = [
        {
            "concept_id": h["concept_id"],
            "score": h.get("score"),
            "source": h.get("source"),
            "category": h.get("category"),
        }
        for h in valid_hits
    ]
    return {
        "citation_markdown": render_citation_block(cids) or None,
        "citation_paths": list(cids),
        "citation_concept_ids": list(cids),
        "citation_trace": trace,
        "tier_downgrade": None,
        "fallback_disclaimer": None,
        "reformulated_query": reformulated_query,
    }


def _build_response_quality_hint(
    source_event_id: str | None,
    expected_citation_paths: list[str],
    state_root: Path | None,
) -> dict | None:
    """Build response_quality_hint; None on cold paths with no event id."""
    if source_event_id is None:
        return None
    expected_args = " ".join(f"--expected-citation {shlex.quote(p)}"
                             for p in expected_citation_paths)
    citation_args = f" {expected_args}" if expected_args else ""
    state_root_arg = (f" --state-root {shlex.quote(str(state_root))}"
                      if state_root is not None
                      and Path(state_root) != Path(DEFAULT_STATE_ROOT) else "")
    base = (f"bin/learn-response-quality --source-event-id {source_event_id}"
            f"{citation_args}")
    path_cmd = (f"{base} --response-path <answer.md> --silent{state_root_arg}")
    stdin_cmd = (f"{base} --response-file - --silent{state_root_arg}")
    summary_cmd = (
        f"{base} --summary-only --contract-flag body_not_captured "
        f"--contract-flag token_efficient_summary_only --silent{state_root_arg}"
    )
    return {
        "command_template": stdin_cmd,
        "wrapper_cmd": stdin_cmd,
        "full_body_path_template": path_cmd,
        "stdin_fallback_cmd": stdin_cmd,
        "summary_only_cmd": summary_cmd,
        "source_event_id": source_event_id,
        "expected_citation_paths": list(expected_citation_paths),
        "body_required": True,
        "body_capture_preferred": True,
        "capture_policy": "full_body_required_path_preferred",
        "body_contract": (
            "Every learner-facing answer should be captured as full body, "
            "but learning UX comes first. Hook capture is preferred. If hooks "
            "are unavailable, prefer --response-path when the host can "
            "materialize the exact final answer without echoing it into the "
            "AI session transcript. If path capture is unavailable, use "
            "--response-file -. Use summary-only only when full-body capture "
            "is impossible."
        ),
        "declared_citations": "auto-extracted from response body 참고 block when omitted",
        "obligation": (
            "AI SHOULD record full response telemetry after answer. Capture "
            "failure must not block the learner-facing answer; enqueue repair "
            "or give only a short learner notice."
        ),
    }

def compose(
    route: RouteDecision,
    artifacts: dict[str, Any],
    prompt: str,
    repo: str | None = None,
    learner_id: str = "default",
    recent_history: list[dict] | None = None,
    *,
    source_event_id: str | None = None,
    state_root: Path | None = None,
    learner_context: dict | None = None,
) -> tuple[str, dict, dict | None, RouteDecision]:
    """Build the full multi-agent prompt + side-band hints.

    Returns ``(markdown, response_hints, response_quality_hint, effective_route)``.

    ``effective_route`` is the post-downgrade route — daemon should use it
    when assembling the response payload so markdown's ``[Mode: …]`` header
    matches ``payload.mode`` and ``history.payload.router_mode``.
    """
    # 1. valid-hit + downgrade selection BEFORE we render any prompt section
    #    so _answer_format sees the effective tier_0_fallback mode.
    rag_attempted = bool(artifacts) and "rag_hits" in artifacts
    rag_hits = artifacts.get("rag_hits") if rag_attempted else None
    valid_hits = _pick_top3(rag_hits or [])
    threshold = get_refusal_threshold()
    downgrade_reason: str | None = None
    # Only downgrade when RAG was actually attempted (artifacts carries
    # `rag_hits`) but returned nothing usable. Bare unit-test scaffolding
    # without `rag_hits` keeps the original cs_qa rendering.
    if route.need_rag and rag_attempted and not valid_hits:
        downgrade_reason = "no_valid_citation"
    elif threshold is not None and route.need_rag and valid_hits:
        if (valid_hits[0].get("score") or 0.0) < threshold:
            downgrade_reason = "corpus_gap_no_confident_match"
    if route.mode == "tier_0_fallback" and downgrade_reason is None:
        downgrade_reason = "non_cs_prompt"

    if downgrade_reason and route.mode != "tier_0_fallback":
        route = dataclasses.replace(
            route,
            mode="tier_0_fallback",
            need_rag=False,
            need_mission_ctx=False,
            need_anchors=False,
            personas=[],
            lazy_artifacts=[],
            reason=f"tier downgrade: {downgrade_reason}",
        )

    parts: list[str] = []
    parts.append(_system_header(route))
    parts.append(_persona_section(route))
    parts.append(_artifact_section(route, artifacts, repo))
    if recent_history:
        parts.append(_recent_history(recent_history))
    parts.append(_user_section(prompt, repo, learner_id, learner_context))
    parts.append(_answer_format(route, downgrade_reason))
    markdown = "\n\n".join(p for p in parts if p)

    reformulated_query = (artifacts or {}).get("reformulated_query")
    response_hints = _build_response_hints(
        route, valid_hits, downgrade_reason, reformulated_query
    )
    response_quality_hint = _build_response_quality_hint(
        source_event_id,
        response_hints["citation_paths"],
        state_root,
    )
    return markdown, response_hints, response_quality_hint, route


def _system_header(route: RouteDecision) -> str:
    return (
        "# Woowa Learning System — Coach prompt\n"
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
            parts.append("### stage4_ai_veto_runtime: keep only same code-intent matches; drop path/token-only matches")
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
    if "cognitive_trigger" in artifacts:
        trigger = artifacts["cognitive_trigger"]
        parts.append(
            "### cognitive_trigger "
            f"(type={trigger.get('trigger_type')}, reason={trigger.get('reason')})"
        )
        if trigger.get("markdown"):
            parts.append(str(trigger["markdown"]))
        if trigger.get("trigger_session_id"):
            parts.append(f"  trigger_session_id: {trigger['trigger_session_id']}")
    if "personalization" in artifacts:
        p = artifacts["personalization"]
        parts.append(
            "### personalization "
            f"(enabled={p.get('enabled')}, "
            f"mastered={len(p.get('mastered_applied', []))}, "
            f"uncertain={len(p.get('uncertain_applied', []))})"
        )
        if p.get("mastered_applied"):
            parts.append(f"  mastered_applied: {p['mastered_applied'][:5]}")
        if p.get("uncertain_applied"):
            parts.append(f"  uncertain_applied: {p['uncertain_applied'][:5]}")
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


def _user_section(prompt: str, repo: str | None, learner_id: str,
                    learner_context: dict | None = None) -> str:
    parts = [f"## Learner turn", f"**learner_id**: {learner_id}"]
    if repo:
        parts.append(f"**repo**: {repo}")
    parts.append(f"**prompt**: {prompt}")
    if learner_context:
        must_skip = learner_context.get("must_skip_explanations_of") or []
        must_include = learner_context.get("must_include_phrases") or []
        must_offer = learner_context.get("must_offer_next_action") or None
        if must_skip or must_include or must_offer:
            parts.append("**learner_context**:")
            if must_skip:
                parts.append(f"- must_skip_explanations_of: {list(must_skip)[:10]}")
            if must_include:
                parts.append(f"- must_include_phrases: {list(must_include)[:5]}")
            if must_offer:
                parts.append(f"- must_offer_next_action: {must_offer}")
    return "\n".join(parts)


def _answer_format(route: RouteDecision,
                    downgrade_reason: str | None = None) -> str:
    rules = [
        "## 답변 지침",
        "- 한국어 자연스럽게, 학습자 수준에 맞춰",
        f"- 첫 줄 header: `[Mode: {route.mode}]`",
    ]
    if route.mode == "tier_0_fallback":
        # tier_0_fallback overrides citation guidance entirely.
        rules.append(
            f"- 둘째 줄에 fallback_disclaimer 그대로: "
            f"`{FALLBACK_DISCLAIMER}`"
        )
        rules.append("- `참고:` 블록 출력 금지 (인용할 corpus 없음)")
        rules.append("- 본문은 일반 지식 기반 답변 + 마지막에 출처 검증 안내")
        return "\n".join(rules)
    if route.need_rag:
        rules.append("- 답변 마지막에 `참고:\\n- <concept_id>` 형식 인용 (최대 3개)")
        rules.append(
            "- response_hints.citation_markdown이 stdout에 있으면 "
            "그 문자열을 verbatim 복사 (직접 path 작성 금지)"
        )
    if route.need_mission_ctx:
        rules.append("- 학습자가 자기 코드 직접 쓰도록 (자동 코드 수정 ❌)")
        rules.append("- mission_patterns에 추출된 학습자 코드 위치 인용")
    if route.need_anchors:
        rules.append("- review_anchors + cross_crew 의견 차이 narrate (reviewer별/크루별)")
        rules.append("- cross_crew match는 먼저 Stage 4 veto로 같은 code intent인지 걸러")
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
