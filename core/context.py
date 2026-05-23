"""Per-mode context collectors — unified data layer for AI session prompt (D14).

Each `collect_*_context(...)` returns a plain dict that the AI session
prompt builder serializes into markdown. Shapes are intentionally simple so
the prompt template can iterate without per-field logic.

Modes (D13):
- cs_qa        : pure CS RAG question
- coaching     : mission PR coaching (learner_state + peer_pr + rag)
- drill        : answering pending review drill
- retro        : self PR retrospective (recurring mentor signals)
- self_assess  : answering pending self-assessment
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.archive import list_prs
from core.peer_pr import compare as peer_compare
from core.state import (
    DEFAULT_STATE_ROOT,
    RepoState,
    load_profile,
    load_repo_state,
    read_history,
)
from rag.personalization import adjust
from rag.search import SearchHit, search

MAX_HISTORY_RECENT_EVENTS = 20
MAX_RECURRING_THREADS = 10


def collect_cs_qa_context(
    prompt: str,
    learner_id: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    **search_kwargs: Any,
) -> dict[str, Any]:
    """Pure CS question — RAG search + personalization adjust."""
    profile = load_profile(learner_id, state_root=state_root)
    hits = search(prompt, **search_kwargs)
    adjusted = adjust(
        hits,
        mastered_concepts=profile.mastered_concepts,
        uncertain_concepts=profile.uncertain_concepts,
    )
    return {
        "mode": "cs_qa",
        "prompt": prompt,
        "hits": [_hit_to_dict(h) for h in adjusted],
        "personalization": {
            "mastered_applied": [c for c in profile.mastered_concepts if _hit_in_family(adjusted, c)],
            "uncertain_applied": [c for c in profile.uncertain_concepts if _hit_in_family(adjusted, c)],
        },
    }


def collect_coaching_context(
    prompt: str,
    repo: str,
    learner_id: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    peer_pr_numbers: list[int] | None = None,
    **search_kwargs: Any,
) -> dict[str, Any]:
    """Mission coaching — learner_state + peer PR comparison + RAG augment."""
    profile = load_profile(learner_id, state_root=state_root)
    repo_state = load_repo_state(repo, state_root=state_root)

    rag_hits = search(prompt, **search_kwargs) if prompt.strip() else []
    rag_adjusted = adjust(
        rag_hits,
        mastered_concepts=profile.mastered_concepts,
        uncertain_concepts=profile.uncertain_concepts,
    )

    peer_block: dict | None = None
    if peer_pr_numbers and repo_state.target_pr_number:
        peer_cmp = peer_compare(
            repo,
            repo_state.target_pr_number,
            peer_pr_numbers,
            state_root=state_root,
        )
        peer_block = _peer_to_dict(peer_cmp)

    return {
        "mode": "coaching",
        "prompt": prompt,
        "repo": repo,
        "learner_state": _repo_state_to_dict(repo_state),
        "peer_pr_comparison": peer_block,
        "rag_augment": [_hit_to_dict(h) for h in rag_adjusted],
    }


def collect_drill_context(
    learner_id: str,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict[str, Any]:
    """Drill answer — load pending drill + recent mastery context."""
    profile = load_profile(learner_id, state_root=state_root)
    return {
        "mode": "drill",
        "drill_due": profile.drill_due,
        "pending_triggers": profile.pending_triggers,
        "recent_mastered": profile.mastered_concepts[-10:],
    }


def collect_retro_context(
    repo: str,
    learner_login: str,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict[str, Any]:
    """PR retrospective — learner's recent PRs + cross-PR signals."""
    learner_prs = list_prs(repo, author_login=learner_login, limit=15, state_root=state_root)
    return {
        "mode": "retro",
        "repo": repo,
        "learner_login": learner_login,
        "pr_timeline": learner_prs,
        "pr_count": len(learner_prs),
    }


def collect_self_assess_context(
    learner_id: str,
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict[str, Any]:
    """Self-assessment answer — pending trigger + recent code attempts."""
    profile = load_profile(learner_id, state_root=state_root)
    pending = profile.pending_triggers.get("self_assessment")
    return {
        "mode": "self_assess",
        "pending_self_assessment": pending,
        "recent_history": [e for e in read_history(state_root)[-MAX_HISTORY_RECENT_EVENTS:]],
    }


# ── helpers ────────────────────────────────────────────────────────────────


def _hit_to_dict(h: SearchHit) -> dict[str, Any]:
    return {
        "concept_id": h.concept_id,
        "score": round(h.score, 4),
        "category": h.category,
        "title": h.title,
        "source": h.source,
    }


def _repo_state_to_dict(s: RepoState) -> dict[str, Any]:
    return {
        "repo_name": s.repo_name,
        "working_copy": s.working_copy,
        "target_pr_number": s.target_pr_number,
        "threads": s.threads,
        "active_session_id": s.active_session_id,
    }


def _peer_to_dict(cmp) -> dict[str, Any]:
    return {
        "learner_pr": {k: v for k, v in cmp.learner_pr.items() if k != "files"},
        "learner_files": [f["path"] for f in cmp.learner_pr.get("files", [])],
        "peer_prs": [{k: v for k, v in p.items() if k != "files"} for p in cmp.peer_prs],
        "peer_files": {p["number"]: [f["path"] for f in p.get("files", [])] for p in cmp.peer_prs},
        "common_paths": cmp.common_paths,
        "learner_only_paths": cmp.learner_only_paths,
        "peer_only_paths": cmp.peer_only_paths,
        "representative_threads": {str(k): v[:MAX_RECURRING_THREADS] for k, v in cmp.representative_threads.items()},
    }


def _hit_in_family(hits: list[SearchHit], concept_id: str) -> bool:
    from rag.personalization import _matches_family

    return any(_matches_family(h.concept_id, concept_id) for h in hits)
