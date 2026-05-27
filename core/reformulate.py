"""Conservative multi-turn query reformulation.

This module intentionally only handles the safe legacy-compatible shape:
short anaphoric follow-up prompts plus recent RAG citations. It does not try
to guess a concept for every vague query because broad lexical rewriting
degrades cohort retrieval quality.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from core.intent import should_use_rag
from rag.corpus_loader import LoadedCorpus, load_corpus_runtime

_DISABLED = {"0", "false", "off", "no", "disabled"}
_MAX_PRIOR_CONCEPTS = 2
_MAX_ALIASES = 4

_FOLLOW_UP_PATTERNS = (
    re.compile(r"^\s*(그|이|저)(게|건|거|건가|건지|것|것도|것은|건요)\b"),
    re.compile(r"^\s*(그럼|그러면|그렇다면|그때|방금|아까|위에서)\b"),
    re.compile(r"^\s*(그|이|저)\s*(차이|원리|이유|예시|문제|장점|단점)"),
    re.compile(r"^\s*(차이는|왜\s+그래|왜\s+그런|예시는|예시만|좀\s+더|더\s+자세히)"),
    re.compile(r"^\s*(then|that|it|what about that|and then|why is that)\b", re.I),
)

_QUESTION_HINTS = (
    "뭐야", "뭔데", "무슨 뜻", "어떤 뜻", "왜", "어떻게", "차이", "예시",
    "설명", "자세히", "그럼", "그러면", "then", "?",
)

_CATEGORY_HINTS = {
    "algorithm": "알고리즘 컴퓨터공학",
    "data-structure": "자료구조 컴퓨터공학",
    "database": "DB 데이터베이스",
    "design-pattern": "design pattern 디자인 패턴",
    "language": "Java 프로그래밍 언어",
    "network": "네트워크 컴퓨터공학",
    "operating-system": "운영체제 OS 컴퓨터공학",
    "security": "보안 security",
    "software-engineering": "OOP software engineering",
    "spring": "Spring",
    "system-design": "system design 시스템 설계",
}


@dataclass(frozen=True, slots=True)
class ReformulationResult:
    reformulated_query: str
    source: str
    prior_concept_ids: list[str]
    prior_titles: list[str]
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def reformulation_enabled(value: str | None = None) -> bool:
    """Return whether automatic reformulation is enabled.

    Default is on because the selector is no-op unless both an anaphoric prompt
    and recent RAG context exist. ``WOOWA_REFORMULATE=off`` is the rollback
    switch for sessions or benchmarks.
    """
    raw = (value if value is not None else os.environ.get("WOOWA_REFORMULATE", "auto"))
    return str(raw).strip().lower() not in _DISABLED


def is_follow_up_prompt(prompt: str) -> bool:
    text = _normalize(prompt)
    if not text:
        return False
    if not any(h.lower() in text.lower() for h in _QUESTION_HINTS):
        return False
    return any(pattern.search(text) for pattern in _FOLLOW_UP_PATTERNS)


def select_reformulation(
    *,
    prompt: str,
    explicit_reformulated_query: str | None = None,
    history: list[dict] | None = None,
    corpus: LoadedCorpus | None = None,
    repo: str | None = None,
    enabled: bool = True,
) -> ReformulationResult | None:
    """Choose explicit or conservative automatic reformulation.

    Explicit caller-provided reformulation always wins. Automatic
    reformulation only runs when the current prompt is not already a
    self-contained RAG query.
    """
    explicit = (explicit_reformulated_query or "").strip()
    if explicit:
        return ReformulationResult(
            reformulated_query=explicit,
            source="explicit",
            prior_concept_ids=[],
            prior_titles=[],
            reason="caller provided reformulated_query",
        )
    if not enabled:
        return None
    return auto_reformulate(prompt=prompt, history=history, corpus=corpus, repo=repo)


def auto_reformulate(
    *,
    prompt: str,
    history: list[dict] | None,
    corpus: LoadedCorpus | None = None,
    repo: str | None = None,
) -> ReformulationResult | None:
    """Fold recent RAG context into short anaphoric prompts.

    Returns ``None`` for self-contained prompts, no-history prompts, stale
    development/test history, or missing concept metadata.
    """
    clean_prompt = _normalize(prompt)
    if not clean_prompt:
        return None
    if should_use_rag(clean_prompt, repo):
        return None
    if not is_follow_up_prompt(clean_prompt):
        return None

    concept_ids = _recent_rag_concepts(history or [])
    if not concept_ids:
        return None

    loaded = corpus or load_corpus_runtime()
    topics: list[str] = []
    titles: list[str] = []
    used_ids: list[str] = []
    for cid in concept_ids:
        concept = loaded.concepts.get(cid)
        if not concept:
            continue
        topic = _topic_text(cid, concept)
        if not topic:
            continue
        topics.append(topic)
        titles.append(_compact(str(concept.get("title") or cid), 100))
        used_ids.append(cid)
        if len(used_ids) >= _MAX_PRIOR_CONCEPTS:
            break
    if not used_ids:
        return None

    joined_topics = " ; ".join(topics)
    joined_ids = ", ".join(used_ids)
    query = (
        f"{joined_topics} 정의 설명. "
        f"이전 RAG 주제({joined_ids})에 대한 짧은 후속 질문: {clean_prompt}"
    )
    return ReformulationResult(
        reformulated_query=_compact(query, 520),
        source="auto_recent_rag_context",
        prior_concept_ids=used_ids,
        prior_titles=titles,
        reason="short anaphoric prompt + recent cited RAG concepts",
    )


def _recent_rag_concepts(history: list[dict]) -> list[str]:
    seen: set[str] = set()
    for event in reversed(history):
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "rag_ask":
            continue
        if str(event.get("mode") or "learning") in {"development", "test"}:
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("router_mode") == "tier_0_fallback":
            continue
        raw_ids = payload.get("top_concept_ids") or payload.get("citation_paths") or []
        if not isinstance(raw_ids, list):
            continue
        out: list[str] = []
        for raw in raw_ids:
            cid = str(raw).strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            out.append(cid)
            if len(out) >= _MAX_PRIOR_CONCEPTS:
                return out
        if out:
            return out
    return []


def _topic_text(concept_id: str, concept: dict[str, Any]) -> str:
    title = _compact(str(concept.get("title") or concept_id), 120)
    category = str(concept.get("category") or "").strip()
    category_hint = _CATEGORY_HINTS.get(category, category)
    first_hint = category_hint.split()[0].lower() if category_hint else ""
    prefix = "" if first_hint and title.lower().startswith(first_hint) else category_hint
    aliases = _aliases(concept)
    alias_text = f" 핵심어: {', '.join(aliases)}." if aliases else ""
    return _compact(f"{prefix} {title}.{alias_text}", 220)


def _aliases(concept: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for raw in concept.get("aliases") or []:
        alias = _normalize(str(raw))
        if not alias or len(alias) > 42:
            continue
        if alias.lower() in {a.lower() for a in out}:
            continue
        out.append(alias)
        if len(out) >= _MAX_ALIASES:
            break
    return out


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _compact(value: str, max_chars: int) -> str:
    text = _normalize(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
