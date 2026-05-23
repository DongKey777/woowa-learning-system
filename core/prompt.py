"""Unified AI session prompt builder (D14+D18).

Each mode renders a markdown block the AI session reads to produce a single
learner-facing reply. Block headers match legacy UX (`## 상태 요약` etc.)
so learners don't experience UI churn (D18).

build(mode, context) → markdown_prompt
"""
from __future__ import annotations

from typing import Any

MAX_HIT_LINES = 8
MAX_PEER_FILE_LINES = 12
MAX_PATCH_PREVIEW_CHARS = 600


def build(mode: str, context: dict[str, Any]) -> str:
    builder = {
        "cs_qa": _build_cs_qa,
        "coaching": _build_coaching,
        "drill": _build_drill,
        "retro": _build_retro,
        "self_assess": _build_self_assess,
        "tool_only": _build_tool_only,
    }.get(mode)
    if builder is None:
        raise ValueError(f"unknown mode: {mode!r}")
    return builder(context)


def _build_cs_qa(ctx: dict) -> str:
    parts: list[str] = [
        "# Mode: CS Q&A",
        f"**Question**: {ctx['prompt']}",
        "",
        "## 이번 질문의 CS 근거",
    ]
    parts.append(_format_hits(ctx.get("hits", [])))
    personalization = ctx.get("personalization") or {}
    mastered = personalization.get("mastered_applied") or []
    uncertain = personalization.get("uncertain_applied") or []
    if mastered or uncertain:
        parts.extend(["", "## 학습자 컨텍스트 (참고만)"])
        if mastered:
            parts.append(f"- 이미 학습됨: {', '.join(f'`{c}`' for c in mastered)} (기본 정의 반복 ❌)")
        if uncertain:
            parts.append(f"- 약한 축: {', '.join(f'`{c}`' for c in uncertain)} (보충 설명 권장)")
    parts.extend([
        "",
        "## 답변 지침",
        "- 한국어 자연스럽게, 학습자 수준에 맞춰",
        "- 첫 줄 헤더: `[RAG: tier-N — <reason>]` 형식",
        "- 답변 마지막에 `참고:\\n- <concept_id>` 형식으로 인용 (최대 3개)",
    ])
    return "\n".join(parts)


def _build_coaching(ctx: dict) -> str:
    parts: list[str] = [
        "# Mode: Mission Coaching",
        f"**Repo**: {ctx['repo']}",
        f"**Question**: {ctx['prompt']}",
        "",
        "## 상태 요약",
    ]
    state = ctx.get("learner_state") or {}
    parts.append(_format_learner_state(state))

    peer = ctx.get("peer_pr_comparison")
    if peer:
        parts.extend(["", "## Peer PR 비교"])
        parts.append(_format_peer_pr(peer))

    rag = ctx.get("rag_augment") or []
    if rag:
        parts.extend(["", "## 관련 CS 개념"])
        parts.append(_format_hits(rag))

    parts.extend([
        "",
        "## 답변 지침",
        "- 학습자가 자기 코드 직접 쓰도록 (코드 직접 수정 ❌, feedback_no_auto_code_edit)",
        "- mentor 패턴이 보이면 짚어주되 학습자 시각으로 narrate",
        "- ambiguous/likely-fixed thread는 `git show` 직접 확인 후 narrate",
    ])
    return "\n".join(parts)


def _build_drill(ctx: dict) -> str:
    parts = ["# Mode: Drill Answer", ""]
    if ctx.get("pending_triggers", {}).get("review_drill"):
        rd = ctx["pending_triggers"]["review_drill"]
        parts.extend(["## 진행 중 drill", f"```json\n{rd}\n```", ""])
    if ctx.get("drill_due"):
        parts.extend(["## Due 일정", *(f"- {d}" for d in ctx["drill_due"][:5]), ""])
    if ctx.get("recent_mastered"):
        parts.extend([
            "## 최근 mastered (반복 정의 ❌)",
            ", ".join(f"`{c}`" for c in ctx["recent_mastered"]),
            "",
        ])
    parts.extend([
        "## 답변 지침",
        "- 학습자 답변을 4축(accuracy/depth/practicality/completeness)으로 채점",
        "- 점수 + 약점 dimension + 다음 review 시점 제안",
    ])
    return "\n".join(parts)


def _build_retro(ctx: dict) -> str:
    parts = [
        "# Mode: PR Retrospective",
        f"**Repo**: {ctx['repo']}  |  **Login**: {ctx['learner_login']}",
        f"**PR count**: {ctx['pr_count']}",
        "",
        "## 최근 PR timeline",
    ]
    for pr in ctx.get("pr_timeline", [])[:15]:
        parts.append(f"- #{pr['number']} {pr.get('title', '?')} (+{pr.get('additions', 0)}/-{pr.get('deletions', 0)})")
    parts.extend([
        "",
        "## 답변 지침",
        "- 반복 mentor 지적 패턴을 학습자 시각으로 narrate",
        "- 학습 진척 신호 (review rounds 감소 추이) 같이",
    ])
    return "\n".join(parts)


def _build_self_assess(ctx: dict) -> str:
    pending = ctx.get("pending_self_assessment")
    parts = ["# Mode: Self-Assessment Answer", ""]
    if pending:
        parts.extend([
            "## Pending trigger",
            f"- session: `{pending.get('trigger_session_id')}`",
            f"- concept_ids: {pending.get('payload', {}).get('concept_ids', [])}",
            "",
        ])
    recent = ctx.get("recent_history") or []
    if recent:
        parts.extend([
            "## 최근 event (참고)",
            *(f"- {e.get('type', '?')}: {e.get('event_id', '?')}" for e in recent[-5:]),
            "",
        ])
    parts.extend([
        "## 답변 지침",
        "- 학습자 점수 그대로 받되, mastery로 격상 ❌ (calibration only)",
        "- bin/learn-self-assess 호출 사실을 한국어 한 줄로만 보고",
    ])
    return "\n".join(parts)


def _build_tool_only(ctx: dict) -> str:
    return (
        "# Mode: Tool/Build Question\n"
        f"**Question**: {ctx.get('prompt', '')}\n\n"
        "## 답변 지침\n"
        "- RAG 호출 없이 훈련 지식으로 답변 (D6 fast-path)\n"
        "- 헤더: `[RAG: tier-0 — tool token, 훈련지식 기반]`\n"
        "- 답변 끝에 `참고:` 블록 출력 ❌\n"
    )


# ── formatters ─────────────────────────────────────────────────────────────


def _format_hits(hits: list[dict]) -> str:
    if not hits:
        return "(retrieval 결과 없음)"
    lines = []
    for h in hits[:MAX_HIT_LINES]:
        score = h.get("score", 0)
        lines.append(f"- [{h.get('category', '?')}] `{h.get('concept_id', '?')}` — {h.get('title', '?')} (score {score:.3f}, src={h.get('source', '?')})")
    return "\n".join(lines)


def _format_learner_state(state: dict) -> str:
    wc = state.get("working_copy") or {}
    target = state.get("target_pr_number")
    threads = state.get("threads") or []
    unresolved = sum(1 for t in threads if t.get("resolved") is False)
    return (
        f"- branch: `{wc.get('branch', '?')}` (dirty={wc.get('dirty', '?')})\n"
        f"- target PR: #{target}\n"
        f"- threads: total={len(threads)}, unresolved={unresolved}"
    )


def _format_peer_pr(peer: dict) -> str:
    learner = peer["learner_pr"]
    parts = [
        f"- 학습자 PR #{learner['number']} '{learner.get('title', '?')}' (+{learner.get('additions', 0)}/-{learner.get('deletions', 0)})",
        f"  - files: {', '.join(peer.get('learner_files', [])[:MAX_PEER_FILE_LINES])}",
    ]
    for p in peer.get("peer_prs", []):
        parts.append(
            f"- Peer PR #{p['number']} '{p.get('title', '?')}' (작가={p.get('nickname') or p.get('author_login')})"
        )
        files = peer.get("peer_files", {}).get(p["number"], [])
        parts.append(f"  - files: {', '.join(files[:MAX_PEER_FILE_LINES])}")
    if peer.get("common_paths"):
        parts.append(f"- 공통 path: {', '.join(peer['common_paths'])}")
    if peer.get("learner_only_paths"):
        parts.append(f"- 학습자만: {', '.join(peer['learner_only_paths'][:MAX_PEER_FILE_LINES])}")
    if peer.get("peer_only_paths"):
        parts.append(f"- Peer만: {', '.join(peer['peer_only_paths'][:MAX_PEER_FILE_LINES])}")
    threads = peer.get("representative_threads") or {}
    for pn, ts in threads.items():
        for t in ts[:3]:
            root = t["root"]
            body = (root.get("body") or "")[:MAX_PATCH_PREVIEW_CHARS]
            parts.append(f"- PR#{pn} 멘토 ({root.get('author_login')}): {root.get('path')}:{root.get('line')} — {body}")
    return "\n".join(parts)
