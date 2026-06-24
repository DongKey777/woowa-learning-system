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
REVIEWER_INSTRUCTIONS_NO_XCREW = (
    "실제 reviewer 시각. 코드의 가독성·일관성·관례 위반·놓친 edge case를 짚어줘. "
    "이번 turn에는 다른 크루 리뷰(cross_crew_review_graph)나 review_anchors 데이터가 "
    "로드되지 않았으니 없는 리뷰를 지어내 인용하지 마. 동일 패턴에 trade-off가 있으면 명시."
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


def _reorder_must_skip_by_relevance(must_skip, rag_hits):
    """Order must_skip so concepts retrieved for THIS query come first.

    The prompt cap (_user_section [:10]) otherwise keeps an arbitrary alphabetical
    slice; relevance-first means the mastered basics OF the current topic survive
    the cap instead of being dropped, so the session doesn't re-explain a concept
    the learner already knows. No-op (same order) when ≤10 — the cap is inactive
    and all are shown anyway, so the rendered prompt stays byte-identical — or when
    no rag_hits carry concept_ids (no relevance signal). Stable within each group.
    """
    ms = list(must_skip or [])
    if len(ms) <= 10:
        return ms
    rag_cids = {
        h.get("concept_id")
        for h in (rag_hits or [])
        if isinstance(h, dict) and h.get("concept_id")
    }
    if not rag_cids:
        return ms
    return [c for c in ms if c in rag_cids] + [c for c in ms if c not in rag_cids]


RERANK_GATE_TAU = 0.05  # raw dense top1-top2 cosine margin; <τ → session reranks


def _rerank_gate(dense_margin: float | None) -> str:
    """trust_top1 = session may trust dense top-1 (skip rerank reasoning, also
    protected from rerank-breakage). rerank = low/unknown confidence → session
    reorders top-K by the injected summaries. τ=0.05 (empirical: margin weakly
    separates correct/wrong top-1, so gate is conservative — breakage protection
    on the most confident, rerank otherwise)."""
    if dense_margin is None:
        return "rerank"
    return "trust_top1" if dense_margin >= RERANK_GATE_TAU else "rerank"


def _build_response_hints(
    route: RouteDecision,
    valid_hits: list[dict],
    downgrade_reason: str | None,
    reformulated_query: str | None,
    dense_margin: float | None = None,
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
            "dense_margin": dense_margin,
            "rerank_gate": _rerank_gate(dense_margin),
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
        "dense_margin": dense_margin,
        "rerank_gate": _rerank_gate(dense_margin),
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
    parts.append(_persona_section(route, artifacts))
    parts.append(_artifact_section(route, artifacts, repo))
    if recent_history:
        parts.append(_recent_history(recent_history))
    if learner_context and learner_context.get("must_skip_explanations_of"):
        # Relevance-order must_skip (only changes anything when >10) so the prompt
        # cap keeps the query-relevant mastered concepts, not an alphabetical slice.
        learner_context = {
            **learner_context,
            "must_skip_explanations_of": _reorder_must_skip_by_relevance(
                learner_context["must_skip_explanations_of"], rag_hits),
        }
    parts.append(_user_section(prompt, repo, learner_id, learner_context))
    parts.append(_answer_format(route, downgrade_reason))
    markdown = "\n\n".join(p for p in parts if p)

    reformulated_query = (artifacts or {}).get("reformulated_query")
    dense_margin = (artifacts or {}).get("rag_dense_margin")
    response_hints = _build_response_hints(
        route, valid_hits, downgrade_reason, reformulated_query, dense_margin
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


def _persona_section(route: RouteDecision, artifacts: dict | None = None) -> str:
    if not route.personas:
        return ""
    # REVIEWER cites cross_crew_review_graph + review_anchors. If neither slot is
    # loaded this turn, swap in a variant that explicitly forbids fabricating
    # those reviews — gating on slot PRESENCE (name in artifacts), not content,
    # so f11_anchor (which loads the slots even when unbuilt) keeps the full text.
    arts = artifacts or {}
    has_review_data = "cross_crew_review_graph" in arts or "review_anchors" in arts
    lines = ["## Personas (한 답변에 통합, 부적절 persona는 생략)"]
    for p in route.personas:
        label, instr = PERSONA_INSTRUCTIONS.get(p, ("[?]", ""))
        if p == PERSONA_REVIEWER and not has_review_data:
            instr = REVIEWER_INSTRUCTIONS_NO_XCREW
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
    if "pr_diff_evolution" in artifacts:
        parts.append(_render_pr_diff_evolution(artifacts["pr_diff_evolution"]))
    if "cross_mission" in artifacts:
        parts.append(_render_cross_mission(artifacts["cross_mission"]))
    if "memory_review" in artifacts:
        parts.append(_render_memory_review(artifacts["memory_review"]))
    if "pr_review" in artifacts:
        parts.append(_render_pr_review(artifacts["pr_review"]))
    if "reviewer_profile" in artifacts:
        parts.append(_render_reviewer_profile(artifacts["reviewer_profile"]))
    if "learning_path" in artifacts:
        parts.append(_render_learning_path(artifacts["learning_path"]))
    if "pr_meta" in artifacts:
        parts.append(_render_pr_meta(artifacts["pr_meta"]))
    if "thread_recon" in artifacts:
        parts.append(_render_thread_recon(artifacts["thread_recon"]))
    if "temporal" in artifacts:
        parts.append(_render_temporal(artifacts["temporal"]))
    if "meta_analytics" in artifacts:
        parts.append(_render_meta_analytics(artifacts["meta_analytics"]))
    if "cohort" in artifacts:
        parts.append(_render_cohort(artifacts["cohort"]))
    if "predict" in artifacts:
        parts.append(_render_predict(artifacts["predict"]))
    if "peer_pr" in artifacts:
        parts.append(_render_peer_pr(artifacts["peer_pr"]))
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
                if h.get("summary"):
                    parts.append(f"      ↳ {h['summary']}")
                if h.get("body"):
                    parts.append(f"      ↳본문: {h['body']}")
    return "\n".join(parts)


def _short_path(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else path


def _render_pr_diff_evolution(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### pr_diff_evolution (NOT YET BUILT — bin/pr-diff-evolution-build pending)"
    lines = [
        f"### pr_diff_evolution (PRs={art.get('pr_count', 0)}, "
        f"clone={art.get('clone_available')})",
        "데이터 100% archive+clone 실측 — 없는 라운드/커밋/링크 지어내지 마.",
    ]
    for pr in art.get("prs", []):
        lines.append(
            f"- PR#{pr.get('number')} merged={pr.get('merged')} "
            f"rounds={pr.get('round_count', 0)}")
        for r in pr.get("rounds", []):
            lines.append(
                f"  · R{r['index']} {r['reviewer']} {r['state']} "
                f"({r['submitted_at']}) comments={r['comment_count']} "
                f"fixes={len(r.get('fix_commits', []))}")
            for fc in r.get("fix_commits", []):
                lines.append(f"      ↳ {fc['sha']} {fc['subject']}")
        if pr.get("review_to_fix"):
            lines.append("  리뷰→수정 링크 (멘토 지적 → 그 파일을 고친 커밋):")
            for l in pr["review_to_fix"]:
                lines.append(
                    f"    · {_short_path(l['path'])}: \"{l['comment_excerpt']}\" "
                    f"→ {l['fix_sha']} {l['fix_subject']}")
        if pr.get("hotspots"):
            hs = ", ".join(f"{h['comments']}×{_short_path(h['path'])}"
                           for h in pr["hotspots"])
            lines.append(f"  핫스팟(리뷰 몰린 파일): {hs}")
        if pr.get("smells"):
            sm = ", ".join(f"{_short_path(s['path'])}:{s['line']}→{s['concept_id']}"
                           for s in pr["smells"][:6])
            lines.append(f"  최신 diff 패턴(push 전 점검): {sm}")
    return "\n".join(lines)


def _render_cross_mission(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### cross_mission (NOT YET BUILT — bin/learn-cross-mission-build pending)"
    lines = [
        "### cross_mission (미션 간 학습 흐름 — 전 미션 실측)",
        "데이터 100% 학습자 evidence+패턴 실측 — 없는 미션/개념 연결 지어내지 마.",
    ]
    order = art.get("repos_analyzed", [])
    if order:
        lines.append(f"- 분석한 미션(시간순): {' → '.join(order)}")
    carry = art.get("carryover", [])
    if carry:
        lines.append("- 개념 carryover (앞 미션에서 증명 → 뒤 미션에서 다시 사용):")
        for c in carry:
            lines.append(
                f"    · {c['concept_id']}: {c['proven_in']} → {c['reused_in']}")
    else:
        lines.append("- 개념 carryover: 아직 미션 간 재사용 신호 없음 (데이터 축적 중)")
    recurring = art.get("recurring_across_missions", [])
    if recurring:
        lines.append("- 미션 넘어 반복된 멘토 지적:")
        for r in recurring:
            lines.append(
                f"    · '{r['topic']}' — {len(r['repos'])}개 미션, "
                f"코멘트 {r['total_comments']}건 ({', '.join(r['repos'])})")
    diff = art.get("difficulty", [])
    if diff:
        lines.append("- 미션별 난이도(평균 리뷰 라운드, 높을수록 빡셈):")
        for d in diff:
            lines.append(
                f"    · {d['repo']}: avg_rounds={d['avg_review_rounds']} "
                f"(PR {d['prs_merged']}/{d['prs_total']} merged)")
    return "\n".join(lines)


def _render_memory_review(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### memory_review (NOT YET BUILT — bin/learn-memory-review-build pending)"
    lines = [
        "### memory_review (복습 — 전 미션 실측)",
        "데이터 100% 학습자 코드+질문기록+멘토 리뷰 실측 — 없는 개념/카드 지어내지 마.",
    ]
    blind = art.get("blind_spots", [])
    if blind:
        lines.append("- 사각지대 (코드엔 썼는데 한 번도 안 물어본 개념, 미숙한 것부터):")
        for b in blind:
            lvl = b.get("bloom_level") or "미학습"
            lines.append(
                f"    · {b['concept_id']} (수준={lvl}, 사용 미션={', '.join(b['repos'])})")
    else:
        lines.append("- 사각지대: 코드에 쓰는 개념은 모두 한 번씩은 물어봄 (사각지대 없음)")
    forget = art.get("forgetting", [])
    if forget:
        lines.append("- 복습 주기 지난 개념 (familiar 이상인데 30일+ 안 봄):")
        for f in forget:
            lines.append(
                f"    · {f['concept_id']} (수준={f['bloom_level']}, "
                f"{f['days_since_seen']}일 전 마지막)")
    else:
        lines.append("- 복습 주기 지난 개념: 없음 (최근 학습 활발)")
    cards = art.get("review_cards", [])
    if cards:
        lines.append("- 복습 카드 (내 코드에 달린 실제 멘토 리뷰 → 연결 개념):")
        for c in cards:
            lines.append(
                f"    · {c['repo']} PR#{c['pr_number']} {_short_path(c.get('path') or '')}: "
                f"\"{c['mentor_prompt']}\"")
            lines.append(f"      ↳ 연결 개념: {', '.join(c['concept_ids'])}")
    return "\n".join(lines)


def _render_pr_review(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### pr_review (NOT YET BUILT — bin/learn-pr-review-build pending)"
    counts = art.get("counts", {})
    lines = [
        f"### pr_review (받은 리뷰 — 실측 멘토/피어 코멘트, anchors={counts.get('anchors', 0)})",
        "데이터 100% archive 실측 — 없는 리뷰/멘토 코멘트 지어내지 마.",
    ]
    unresolved = art.get("unresolved_threads", [])
    if unresolved:
        lines.append("- 미해결 스레드 (아직 내 답글 없음):")
        for t in unresolved:
            lines.append(
                f"    · PR#{t.get('pr_number')} {_short_path(t.get('path') or '')}:{t.get('line')} "
                f"(멘토={t.get('mentor_login')}, {t.get('open_days')}일 열림): "
                f"\"{t.get('body_excerpt')}\"")
    else:
        lines.append("- 미해결 스레드: 없음 (받은 멘토 코멘트에 모두 답글 달림)")
    linked = [a for a in art.get("anchors", []) if a.get("concept_ids")]
    if linked:
        lines.append("- 개념 연결된 리뷰 (해당 개념 학습으로 이어가기):")
        for a in linked:
            lines.append(
                f"    · PR#{a.get('pr_number')} {_short_path(a.get('path') or '')}:{a.get('line')} "
                f"(멘토={a.get('mentor_login')}): \"{a.get('comment_excerpt')}\"")
            lines.append(f"      ↳ 연결 개념: {', '.join(a['concept_ids'])}")
    else:
        lines.append("- 개념 연결된 리뷰: 아직 개념 링크된 리뷰 없음 (anchors-build 후 채워짐)")
    overview = art.get("pr_overview", [])
    if overview:
        lines.append("- PR 총평 (이슈 코멘트):")
        for o in overview:
            lines.append(
                f"    · PR#{o.get('pr_number')} ({o.get('user_login')}): "
                f"\"{o.get('body_excerpt')}\"")
    else:
        lines.append("- PR 총평: 이번 PR엔 본문 총평 코멘트 없음")
    return "\n".join(lines)


def _render_reviewer_profile(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### reviewer_profile (NOT YET BUILT — bin/reviewer-profile-build pending)"
    status = art.get("status")
    if status in ("missing_archive", "no_data", "unknown_reviewer"):
        who = art.get("reviewer_login") or "리뷰어"
        return (f"### reviewer_profile ({who})\n"
                f"- 이 리뷰어의 코멘트 데이터가 아직 없어 (status={status}).")
    login = art.get("reviewer_login")
    nick = art.get("nickname")
    who = f"{login}" + (f" ({nick})" if nick and nick != login else "")
    states = art.get("review_states", {})
    lines = [
        f"### reviewer_profile ({who} — 내 PR에 달린 실측 리뷰)",
        "데이터 100% archive 실측 — 없는 코멘트/성향 지어내지 마.",
        (f"- 활동: 전체 코멘트 {art.get('comments_total', 0)}개 "
         f"(내 PR에 {art.get('comments_on_learner_pr', 0)}개), "
         f"리뷰한 PR {art.get('prs_reviewed', 0)}개 "
         f"[APPROVED {states.get('APPROVED', 0)} / "
         f"CHANGES_REQUESTED {states.get('CHANGES_REQUESTED', 0)} / "
         f"COMMENTED {states.get('COMMENTED', 0)}]"),
    ]
    topics = art.get("recurring_topics", [])
    if topics:
        lines.append("- 자주 짚는 주제 (코멘트 빈도순):")
        lines.append("    " + ", ".join(
            f"{t.get('topic')}({t.get('n')})" for t in topics))
    files = art.get("hotspot_files", [])
    if files:
        lines.append("- 리뷰 몰린 파일:")
        lines.append("    " + ", ".join(
            f"{f.get('file')}({f.get('n')})" for f in files))
    samples = art.get("sample_threads", [])
    if samples:
        lines.append("- 대표 코멘트:")
        for s in samples:
            loc = _short_path(s.get("path") or "")
            ln = s.get("line")
            loc = f"{loc}:{ln}" if loc and ln else loc
            lines.append(f"    · {loc} \"{s.get('body_excerpt')}\"")
    return "\n".join(lines)


def _render_learning_path(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### learning_path (NOT YET BUILT — bin/learn-learning-path-build pending)"
    status = art.get("status")
    if status in ("missing_mastery", "missing_graph"):
        return (f"### learning_path\n"
                f"- 추천에 필요한 데이터가 아직 없어 (status={status}).")
    dist = art.get("mastery_distribution", {})
    lines = [
        "### learning_path (다음 학습 추천 — mastery + 개념 그래프 실측)",
        "데이터 100% 학습자 mastery + corpus 개념 그래프 실측 — 없는 개념/순서 지어내지 마.",
        (f"- 현재 숙련도: proficient {dist.get('proficient', 0)} / "
         f"familiar {dist.get('familiar', 0)} / attempted {dist.get('attempted', 0)} "
         f"(추적 {dist.get('total', 0)})"),
    ]
    nxt = art.get("next_concepts", [])
    ready = [c for c in nxt if c.get("ready")]
    not_ready = [c for c in nxt if not c.get("ready")]
    if ready:
        lines.append("- 바로 배울 수 있는 개념 (선수 개념 충족, 아는 개념에서 이어짐):")
        for c in ready:
            lines.append(
                f"    · {c.get('concept_id')} ({c.get('category')}) "
                f"← {c.get('from_known')}")
    if not_ready:
        lines.append("- 곧 배울 개념 (선수 개념 일부 미충족):")
        for c in not_ready:
            lines.append(
                f"    · {c.get('concept_id')} ({c.get('category')}) "
                f"[선수 {c.get('prereqs_met')}/{c.get('prereqs_total')}]")
    if not nxt:
        lines.append("- 다음 개념: 아는 개념에서 이어지는 후속 개념을 찾지 못함 (mastery 더 쌓이면 채워짐)")
    unmet = art.get("unmet_prereqs", [])
    if unmet:
        lines.append("- 먼저 채워야 할 선수 개념:")
        for u in unmet:
            lines.append(
                f"    · {u.get('concept_id')} → 먼저 {u.get('missing_prereq')} "
                f"(현재 {u.get('prereq_level')})")
    conf = art.get("confusable_pairs", [])
    if conf:
        lines.append("- 헷갈리기 쉬운 개념 쌍 (구분해서 학습):")
        for p in conf:
            lines.append(f"    · {p.get('a')} ↔ {p.get('b')}")
    return "\n".join(lines)


_PR_META_FLAG_LABELS = {
    "no_body": "본문 없음",
    "thin_body": "본문 빈약",
    "large_pr": "PR 과대",
    "low_commit_cohesion": "커밋 응집 낮음",
}


def _render_pr_meta(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### pr_meta (NOT YET BUILT — bin/learn-pr-meta-build pending)"
    status = art.get("status")
    if status == "missing_archive":
        return ("### pr_meta\n"
                "- PR 메타를 분석할 archive가 아직 없어 (status=missing_archive).")
    cohort = art.get("cohort", {})
    prs = art.get("prs", [])
    counts = art.get("counts", {})
    lines = [
        "### pr_meta (내 PR 메타 위생 — 본문/크기/커밋 응집, archive 컬럼 실측)",
        "데이터 100% archive 실측 — 없는 PR/수치 지어내지 마.",
        (f"- 코호트 평균(동료 {cohort.get('n', 0)}건): "
         f"+{cohort.get('avg_additions', 0)} 라인 / "
         f"파일 {cohort.get('avg_changed_files', 0)}개 / "
         f"커밋 {cohort.get('avg_commits', 0)}개 — 내 PR 크기 잣대"),
    ]
    if not prs:
        lines.append("- 내 PR: 아직 수집된 PR이 없어.")
        return "\n".join(lines)
    lines.append(f"- 내 PR ({counts.get('prs', len(prs))}개 중 "
                 f"{counts.get('flagged', 0)}개에 위생 플래그):")
    for pr in prs:
        flags = pr.get("flags", [])
        flag_str = (" ⚠ " + ", ".join(_PR_META_FLAG_LABELS.get(f, f) for f in flags)
                    if flags else " (플래그 없음)")
        merged = "merged" if pr.get("merged") else "open"
        lines.append(
            f"    · PR#{pr.get('number')} [{merged}] "
            f"본문 {pr.get('body_chars', 0)}자 / "
            f"+{pr.get('additions', 0)}-{pr.get('deletions', 0)} 라인 / "
            f"파일 {pr.get('changed_files', 0)}개 / "
            f"커밋 {pr.get('commits_count', 0)}개 "
            f"(커밋당 {pr.get('files_per_commit', 0)}파일){flag_str}")
    return "\n".join(lines)


def _render_thread_recon(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return ("### thread_recon (NOT YET BUILT — "
                "bin/learn-thread-recon-build pending)")
    status = art.get("status")
    if status == "missing_archive":
        return ("### thread_recon\n"
                "- 리뷰 스레드를 복원할 archive가 아직 없어 (status=missing_archive).")
    threads = art.get("threads", [])
    counts = art.get("counts", {})
    lines = [
        "### thread_recon (받은 리뷰 대화 복원 — reply 체인, archive 실측)",
        "데이터 100% archive 실측 — 없는 댓글/대화 지어내지 마.",
    ]
    if not threads:
        lines.append(
            f"- 복원할 reply 대화가 아직 없어 "
            f"(reply가 달린 스레드 {counts.get('threads', 0)}개). "
            f"받은 리뷰가 전부 단발 댓글이면 여기 비어 있는 게 정상이야.")
        return "\n".join(lines)
    lines.append(
        f"- reply 달린 스레드 {counts.get('threads', len(threads))}개 / "
        f"총 메시지 {counts.get('total_messages', 0)}개:")
    for th in threads:
        loc = th.get("path") or "?"
        line_no = th.get("line")
        loc_str = f"{loc}:{line_no}" if line_no else loc
        lines.append(
            f"    · PR#{th.get('pr_number')} {loc_str} "
            f"(reply {th.get('reply_count', 0)}개, "
            f"참여 {', '.join(th.get('participants', []))})")
        for msg in th.get("messages", []):
            who = "나" if msg.get("is_learner") else msg.get("author", "?")
            lines.append(f"        - {who}: {msg.get('body_excerpt', '')}")
    return "\n".join(lines)


def _render_temporal(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return ("### temporal (NOT YET BUILT — "
                "bin/learn-temporal-build pending)")
    status = art.get("status")
    if status == "missing_archive":
        return ("### temporal\n"
                "- 리뷰 시간 동학을 분석할 archive가 아직 없어 (status=missing_archive).")
    prs = art.get("prs", [])
    agg = art.get("aggregate", {})
    counts = art.get("counts", {})
    lines = [
        "### temporal (리뷰 사이클 시간 동학 — 첫 리뷰까지/머지까지/정체, archive 실측)",
        "데이터 100% archive 실측 — 없는 시각/지연 지어내지 마.",
    ]
    if not prs:
        lines.append("- 분석할 PR이 아직 없어.")
        return "\n".join(lines)
    avg_ttfr = agg.get("avg_time_to_first_review_h")
    avg_ttm = agg.get("avg_time_to_merge_h")
    avg_ttfr_str = f"{avg_ttfr}h" if avg_ttfr is not None else "데이터 없음"
    avg_ttm_str = f"{avg_ttm}h" if avg_ttm is not None else "미머지(데이터 없음)"
    lines.append(
        f"- 평균(내 PR {agg.get('n_prs', len(prs))}개): "
        f"첫 리뷰까지 {avg_ttfr_str} / "
        f"머지까지 {avg_ttm_str} / "
        f"리뷰 라운드 {agg.get('avg_review_rounds', 0)}회 / "
        f"정체 구간 총 {agg.get('total_stalls', 0)}건")
    for pr in prs:
        stalls = pr.get("stalls") or []
        stall_str = (f" ⚠ 정체 {len(stalls)}건" if stalls else "")
        ttm = pr.get("time_to_merge_h")
        ttm_str = f"{ttm}h" if ttm is not None else "미머지"
        ttfr = pr.get("time_to_first_review_h")
        ttfr_str = f"{ttfr}h" if ttfr is not None else "리뷰 없음"
        lines.append(
            f"    · PR#{pr.get('number')} "
            f"첫 리뷰까지 {ttfr_str} / "
            f"머지까지 {ttm_str} / "
            f"라운드 {pr.get('review_rounds', 0)}회{stall_str}")
    return "\n".join(lines)


def _render_meta_analytics(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return ("### meta_analytics (NOT YET BUILT — "
                "bin/learn-meta-analytics-build pending)")
    status = art.get("status")
    if status == "missing_history":
        return ("### meta_analytics\n"
                "- 학습 메타를 분석할 ask 이력이 아직 없어 (status=missing_history).")
    repeated = art.get("repeated_concepts", [])
    mode_mix = art.get("mode_mix", [])
    md = art.get("mastery_distribution", {})
    counts = art.get("counts", {})
    lines = [
        "### meta_analytics (내 학습 메타 — 반복 질문/질문 유형/숙련 분포, 이력 실측)",
        "데이터 100% 내 ask 이력 실측 — 없는 개념/수치 지어내지 마.",
        (f"- ask 이벤트 {counts.get('rag_ask_events', 0)}건, "
         f"서로 다른 개념 {counts.get('distinct_concepts_asked', 0)}개, "
         f"그중 반복 질문 {counts.get('repeated_concepts', 0)}개"),
    ]
    if repeated:
        lines.append("- 반복해서 다시 물어본 개념 (sticky):")
        for c in repeated:
            repos = ", ".join(c.get("repos", []))
            lines.append(
                f"    · {c.get('concept_id')} — {c.get('times_asked')}회"
                f"{f' ({repos})' if repos else ''}")
    if mode_mix:
        top = ", ".join(f"{m.get('mode')} {m.get('pct')}%"
                        for m in mode_mix[:5])
        lines.append(f"- 질문 유형 분포: {top}")
    if md:
        lines.append(
            f"- 숙련 분포: attempted {md.get('attempted', 0)} / "
            f"familiar {md.get('familiar', 0)} / "
            f"proficient {md.get('proficient', 0)} / "
            f"mastered {md.get('mastered', 0)} "
            f"(추적 {md.get('total', 0)}개)")
    return "\n".join(lines)


_COHORT_METRIC_LABELS = {
    "total_changes": "변경 라인",
    "changed_files": "변경 파일",
    "commits_count": "커밋 수",
    "reviews_received": "받은 리뷰 수",
    "review_rounds": "리뷰 라운드",
}


def _render_cohort(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### cohort (NOT YET BUILT — bin/learn-cohort-build pending)"
    status = art.get("status")
    if status == "missing_archive":
        return ("### cohort\n"
                "- 동료 비교용 archive가 아직 없어 (status=missing_archive).")
    if status == "no_cohort":
        return ("### cohort\n"
                "- 비교할 동료 PR이 아직 없어 (status=no_cohort).")
    if status == "no_learner_pr":
        return ("### cohort\n"
                "- 비교할 내 PR이 아직 없어 (status=no_learner_pr).")
    cohort = art.get("cohort", {})
    comparison = art.get("comparison", [])
    counts = art.get("counts", {})
    lines = [
        "### cohort (동료 대비 내 PR 위치 — 크기/리뷰/라운드 percentile, archive 실측)",
        "데이터 100% archive 실측 — 없는 동료/순위 지어내지 마.",
        (f"- 코호트 {counts.get('cohort_prs', cohort.get('n', 0))}개 PR 기준, "
         f"내 최근 PR을 중앙값과 비교 (percentile = 나보다 작거나 같은 동료 비율):"),
    ]
    for row in comparison:
        label = _COHORT_METRIC_LABELS.get(row.get("metric"), row.get("metric"))
        lines.append(
            f"    · {label}: 나 {row.get('learner_value')} vs "
            f"동료 중앙값 {row.get('cohort_median')} "
            f"(상위 percentile {row.get('percentile')})")
    return "\n".join(lines)


def _render_predict(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### predict (NOT YET BUILT — bin/learn-predict-build pending)"
    if art.get("status") == "missing_inputs":
        return ("### predict\n"
                "- 예측에 쓸 C/F/H 분석이 아직 없어 (status=missing_inputs). "
                "reviewer-profile / cohort / pr-diff-evolution 빌드가 먼저 필요해.")
    inputs = art.get("inputs", {})
    topics = art.get("likely_review_topics", [])
    files = art.get("likely_hotspot_files", [])
    smells = art.get("smell_warnings", [])
    proj = art.get("review_load_projection", {})
    avail = [name for name, ok in (
        ("리뷰어 프로필", inputs.get("reviewer_profile")),
        ("코호트", inputs.get("cohort")),
        ("코드 진화", inputs.get("pr_diff_evolution"))) if ok]
    lines = [
        "### predict (푸시 전 리뷰 예측 — 리뷰어 프로필+코호트+코드 진화 합성, 과거 실측 기반)",
        "데이터 100% 과거 분석 실측 합성 — 미래는 추정이니 단정 말고, 없는 지적/파일 지어내지 마.",
        f"- 합성에 쓴 입력: {', '.join(avail) if avail else '없음'}",
    ]
    if topics:
        top = ", ".join(f"{t.get('topic')}({t.get('mentions')}회)" for t in topics[:8])
        lines.append(f"- 멘토가 반복 지적해온 주제(또 나올 가능성): {top}")
    if files:
        lines.append("- 리뷰가 몰릴 가능성이 높은 파일:")
        for f in files[:8]:
            src = "+".join(f.get("sources", []))
            lines.append(f"    · {f.get('file')} (과거 리뷰 코멘트 {f.get('review_comments')}건, 근거 {src})")
    if smells:
        lines.append(f"- 푸시 전 코드 스멜 선검출 ({len(smells)}건):")
        for s in smells[:8]:
            loc = s.get("file") or "?"
            line_no = s.get("line")
            loc_str = f"{loc}:{line_no}" if line_no else loc
            lines.append(
                f"    · {s.get('concept_id')} — `{s.get('value')}` @ {loc_str}")
    if proj:
        proj_text = proj.get("projection")
        if proj_text:
            lines.append(f"- 리뷰 부담 전망: {proj_text}")
    return "\n".join(lines)


_PEER_PR_FILE_CAP = 12
_PEER_PR_THREAD_CAP = 3
_PEER_PR_BODY_CAP = 400


def _render_peer_pr(art: dict) -> str:
    if art.get("missing") or not art.get("ready"):
        return "### peer_pr (NOT YET BUILT — bin/peer-pr-build pending)"
    status = art.get("status")
    if status == "missing_archive":
        return ("### peer_pr\n"
                "- 동기 비교용 archive가 아직 없어 (status=missing_archive).")
    if status == "no_learner_pr":
        return ("### peer_pr\n"
                "- 비교할 내 PR이 아직 없어 (status=no_learner_pr).")
    if status == "no_peer":
        return ("### peer_pr\n"
                "- 비교할 동기 PR이 아직 없어 (status=no_peer).")

    learner = art.get("learner_pr", {})
    lnick = learner.get("nickname") or "나"
    lines = [
        "### peer_pr (동기 PR 비교 — 같은 미션에서 가장 비슷한 동기 코드/리뷰, archive 실측)",
        "데이터 100% archive 실측 — 없는 PR/파일/멘토 코멘트 지어내지 마. 동기 코드는 참고용이지 베껴 쓰라는 게 아님.",
        (f"- 내 PR #{learner.get('number')} '{lnick}' "
         f"(+{learner.get('additions', 0)}/-{learner.get('deletions', 0)}, "
         f"파일 {len(art.get('learner_files', []))}개)"),
        (f"- 비교 대상 = 내 PR과 파일 구성이 가장 비슷한 동기(Jaccard) "
         f"{len(art.get('peer_prs', []))}명"),
    ]
    common = art.get("common_paths", [])
    if common:
        lines.append(
            f"- 공통으로 건드린 파일 ({len(common)}개): "
            + ", ".join(_short_path(p) for p in common[:_PEER_PR_FILE_CAP]))
    lonly = art.get("learner_only_paths", [])
    if lonly:
        lines.append(
            f"- 나만 건드린 파일: "
            + ", ".join(_short_path(p) for p in lonly[:_PEER_PR_FILE_CAP]))

    threads = art.get("representative_threads") or {}
    peer_only_files = art.get("peer_only_files") or {}
    for p in art.get("peer_prs", []):
        num = p.get("number")
        nick = p.get("nickname") or p.get("author_login")
        # selection rationale (jaccard / common file count) when available
        sel = next((s for s in art.get("selection", [])
                    if s.get("number") == num), {})
        rationale = ""
        if sel:
            rationale = (f" (Jaccard {sel.get('jaccard')}, "
                         f"공통 {sel.get('common_files')}파일)")
        lines.append(
            f"- 동기 #{num} {nick}({p.get('author_login')}) "
            f"(+{p.get('additions', 0)}/-{p.get('deletions', 0)}){rationale}")
        # peer-only = files this peer touched that the learner did NOT.
        # Precomputed by the builder against the FULL learner path set — do NOT
        # re-derive from the capped learner_files (a shared file beyond the cap
        # would be mislabeled as peer-only under the "지어내지 마" framing).
        peer_only = peer_only_files.get(str(num)) or peer_only_files.get(num) or []
        if peer_only:
            lines.append(
                f"    이 동기만 건드린 파일: "
                + ", ".join(_short_path(f) for f in peer_only[:_PEER_PR_FILE_CAP]))
        ts = threads.get(str(num)) or threads.get(num) or []
        for t in ts[:_PEER_PR_THREAD_CAP]:
            root = t.get("root", {})
            body = (root.get("body") or "")[:_PEER_PR_BODY_CAP].replace("\n", " ")
            loc = _short_path(root.get("path") or "")
            ln = root.get("line")
            loc = f"{loc}:{ln}" if loc and ln else loc
            rc = t.get("reply_count", 0)
            rc_str = f" (reply {rc})" if rc else ""
            lines.append(
                f"    멘토 리뷰({root.get('author_login')}) {loc}{rc_str}: \"{body}\"")
    return "\n".join(lines)


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
