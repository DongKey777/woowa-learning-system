"""Cheap lexical candidate fusion for daemon RAG search.

This stays outside rag.search so dense retrieval remains small and generic.
The daemon injects this as a rerank_fn when it wants lexical recall.
"""
from __future__ import annotations

import re
from typing import Callable

from rag.corpus_loader import LoadedCorpus
from rag.search import SearchHit

RRF_K = 60
_TOKEN_RE = re.compile(r"@[a-z0-9_]+|[a-z0-9][a-z0-9_.-]*|[가-힣]{2,}", re.IGNORECASE)
_STOPWORDS = {
    "what", "why", "how", "when", "where", "vs", "the", "and", "or",
    "이게", "이건", "그게", "그건", "뭐야", "무엇", "차이", "정리", "설명",
    "알려줘", "왜", "언제", "어디", "어떻게", "하는", "되는", "기초",
}
_LEXICAL_CACHE: dict[int, tuple[dict, ...]] = {}
_CATEGORY_HINTS = {
    "spring": ("spring", "스프링", "@transactional", "transactional", "bean", "mvc", "aop"),
    "database": ("database", "db", "mvcc", "replica", "lock", "pool", "sql"),
    "security": ("401", "403", "cookie", "cors", "csrf", "xss", "jwt", "auth", "인증", "인가"),
}
_SPRING_DESIGN_BLOCKERS = (
    "factory", "strategy", "template", "registry", "pattern",
    "팩토리", "전략", "템플릿", "패턴",
)
_CANONICAL_PROMOTION_IDS = {
    "spring/ioc-di-basics",
    "spring/transactional-basics",
}
_SPECIALIZED_MARKERS = (
    "bridge", "router", "drill", "interview", "decision-guide", "chooser",
)
_INTRO_QUERY_MARKERS = (
    "뭐야", "무슨 의미", "기초", "처음", "입문", "큰 그림", "단계 알려줘",
    "what is", "basics", "primer",
)
_COMPARISON_QUERY_MARKERS = (
    " vs ", "vs", "차이", "비교", "구분", "대신", "보다", "다른", "같은 거", "분리",
)


def make_lexical_fusion_fn(
    corpus: LoadedCorpus,
    expand: int = 8,
) -> Callable[[str, list[SearchHit]], list[SearchHit]]:
    """Return a rerank_fn that preserves dense top-1 and enriches top-5."""
    def _rerank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
        if expand <= 0 or not hits:
            return hits
        fused = _fuse_lexical(hits, _lexical_candidates(query, corpus, expand))
        promoted = _promote_hinted_category(query, fused)
        promoted = _promote_canonical_candidate(promoted)
        promoted = _promote_exact_expected_query(promoted, _exact_expected_candidates(query, corpus))
        return _refine_confusable_order(query, promoted, corpus)

    return _rerank


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text or "")
        if token.lower() not in _STOPWORDS
    }


def _norm_exact(text: str) -> str:
    return " ".join((text or "").lower().split())


def _lexical_entries(corpus_key: int, corpus: LoadedCorpus) -> tuple[dict, ...]:
    cached = _LEXICAL_CACHE.get(corpus_key)
    if cached is not None:
        return cached
    entries: list[dict] = []
    for cid, concept in corpus.concepts.items():
        title = concept.get("title", "")
        strong_text = " ".join(
            str(x)
            for key_name in ("aliases", "expected_queries")
            for x in (concept.get(key_name) or [])
        )
        body_text = " ".join([
            str(concept.get("summary") or ""),
            str(concept.get("body_markdown") or ""),
        ])
        entries.append({
            "concept_id": cid,
            "category": concept.get("category", ""),
            "level": concept.get("level", ""),
            "title": title,
            "expected_query_norms": {
                _norm_exact(str(q))
                for q in (concept.get("expected_queries") or [])
                if str(q).strip()
            },
            "title_tokens": _tokens(title),
            "strong_tokens": _tokens(strong_text),
            "body_tokens": _tokens(body_text),
        })
    built = tuple(entries)
    _LEXICAL_CACHE[corpus_key] = built
    return built


def _lexical_candidates(query: str, corpus: LoadedCorpus, limit: int) -> list[SearchHit]:
    query_tokens = _tokens(query)
    if not query_tokens or limit <= 0:
        return []
    scored: list[tuple[float, dict]] = []
    for entry in _lexical_entries(id(corpus), corpus):
        title_hits = query_tokens & entry["title_tokens"]
        strong_hits = query_tokens & entry["strong_tokens"]
        body_hits = query_tokens & entry["body_tokens"]
        score = len(title_hits) * 4.0 + len(strong_hits) * 2.5 + len(body_hits) * 0.75
        if score > 0:
            scored.append((score, entry))
    if not scored:
        return []
    scored.sort(key=lambda item: -item[0])
    best = scored[0][0]
    return [
        SearchHit(
            concept_id=entry["concept_id"],
            score=score / best,
            category=entry["category"],
            title=entry["title"],
            source="lexical",
        )
        for score, entry in scored[:limit]
    ]


def _exact_expected_candidates(query: str, corpus: LoadedCorpus) -> list[SearchHit]:
    query_norm = _norm_exact(query)
    if not query_norm:
        return []
    hits: list[SearchHit] = []
    for entry in _lexical_entries(id(corpus), corpus):
        if query_norm in entry["expected_query_norms"]:
            hits.append(SearchHit(
                concept_id=entry["concept_id"],
                score=1.0,
                category=entry["category"],
                title=entry["title"],
                source="lexical_exact",
            ))
    return hits


def _promote_exact_expected_query(
    hits: list[SearchHit],
    exact_hits: list[SearchHit],
) -> list[SearchHit]:
    if not hits or not exact_hits:
        return hits
    exact_by_id = {h.concept_id: h for h in exact_hits}
    if hits[0].concept_id in exact_by_id:
        head = hits[0]
        return [
            SearchHit(
                concept_id=head.concept_id,
                score=max(head.score, 1.0),
                category=head.category,
                title=head.title,
                source="lexical_exact",
            )
        ] + hits[1:]
    for idx, hit in enumerate(hits[1:], start=1):
        if hit.concept_id in exact_by_id:
            promoted = SearchHit(
                concept_id=hit.concept_id,
                score=max(hit.score, 1.0),
                category=hit.category,
                title=hit.title,
                source="lexical_exact",
            )
            return [promoted] + hits[:idx] + hits[idx + 1:]
    return [exact_hits[0]] + hits


def _hinted_category(query: str) -> str | None:
    ql = query.lower()
    if any(term in ql for term in _CATEGORY_HINTS["spring"]):
        if not any(term in ql for term in _SPRING_DESIGN_BLOCKERS):
            return "spring"
    for category in ("database", "security"):
        if any(term in ql for term in _CATEGORY_HINTS[category]):
            return category
    return None


def _promote_hinted_category(query: str, hits: list[SearchHit]) -> list[SearchHit]:
    category = _hinted_category(query)
    if category is None or not hits or hits[0].category == category:
        return hits
    for idx, hit in enumerate(hits[1:], start=1):
        if hit.category == category:
            return [hit] + hits[:idx] + hits[idx + 1:]
    return hits


def _promote_canonical_candidate(hits: list[SearchHit]) -> list[SearchHit]:
    if not hits or not any(marker in hits[0].concept_id for marker in _SPECIALIZED_MARKERS):
        return hits
    head_category = hits[0].category
    for idx, hit in enumerate(hits[1:5], start=1):
        if hit.category == head_category and hit.concept_id in _CANONICAL_PROMOTION_IDS:
            return [hit] + hits[:idx] + hits[idx + 1:]
    return hits


def _refine_confusable_order(
    query: str,
    hits: list[SearchHit],
    corpus: LoadedCorpus,
) -> list[SearchHit]:
    if len(hits) < 2:
        return hits
    head = hits[0]
    head_concept = corpus.concepts.get(head.concept_id)
    if head_concept is None:
        return hits

    promoted: list[SearchHit] = []
    neutral: list[SearchHit] = []
    demoted: list[SearchHit] = []
    for hit in hits[1:]:
        concept = corpus.concepts.get(hit.concept_id)
        if concept is None:
            neutral.append(hit)
        elif _should_promote_comparison_neighbor(query, head, head_concept, hit, concept):
            promoted.append(hit)
        elif _should_demote_neighbor(query, head, head_concept, hit, concept):
            demoted.append(hit)
        else:
            neutral.append(hit)
    return [head] + promoted + neutral + demoted


def _should_promote_comparison_neighbor(
    query: str,
    head: SearchHit,
    head_concept: dict,
    hit: SearchHit,
    concept: dict,
) -> bool:
    return (
        _is_comparison_query(query)
        and hit.category == head.category
        and not _is_mission_bridge_concept(hit.concept_id)
        and _is_confusable_pair(head.concept_id, head_concept, hit.concept_id, concept)
        and _is_comparison_concept(hit.concept_id, concept)
        and len(_tokens(query) & _id_tokens(hit.concept_id)) >= 2
    )


def _should_demote_neighbor(
    query: str,
    head: SearchHit,
    head_concept: dict,
    hit: SearchHit,
    concept: dict,
) -> bool:
    if _should_demote_category_mismatch(query, head, hit):
        return True
    if _should_demote_advanced_from_beginner(query, head_concept, concept):
        return True
    if _should_demote_unasked_comparison_neighbor(query, head.concept_id, head_concept, hit.concept_id, concept):
        return True
    if _is_single_side_of_comparison(query, head.concept_id, head_concept, hit.concept_id, concept):
        return True
    return False


def _should_demote_category_mismatch(query: str, head: SearchHit, hit: SearchHit) -> bool:
    category = _hinted_category(query)
    return (
        category in {"security", "spring"}
        and head.category == category
        and hit.category != category
    )


def _should_demote_advanced_from_beginner(query: str, head_concept: dict, concept: dict) -> bool:
    if head_concept.get("level") != "beginner" or concept.get("level") != "advanced":
        return False
    return _is_intro_query(query) or _is_comparison_query(query)


def _should_demote_unasked_comparison_neighbor(
    query: str,
    head_id: str,
    head_concept: dict,
    candidate_id: str,
    candidate_concept: dict,
) -> bool:
    return (
        not _is_comparison_query(query)
        and _is_routing_head(head_id)
        and not _is_mission_bridge_concept(candidate_id)
        and _is_comparison_concept(candidate_id, candidate_concept)
        and _is_confusable_pair(head_id, head_concept, candidate_id, candidate_concept)
    )


def _is_confusable_pair(
    head_id: str,
    head_concept: dict,
    candidate_id: str,
    candidate_concept: dict,
) -> bool:
    head_confusables = set(head_concept.get("relations", {}).get("confusable_with", []) or [])
    candidate_confusables = set(candidate_concept.get("relations", {}).get("confusable_with", []) or [])
    return candidate_id in head_confusables or head_id in candidate_confusables


def _is_intro_query(query: str) -> bool:
    ql = query.lower()
    return any(marker in ql for marker in _INTRO_QUERY_MARKERS)


def _is_comparison_query(query: str) -> bool:
    ql = f" {query.lower()} "
    return any(marker in ql for marker in _COMPARISON_QUERY_MARKERS)


def _is_comparison_concept(concept_id: str, concept: dict) -> bool:
    text = f" {concept_id.lower()} {str(concept.get('title') or '').lower()} "
    return "-vs-" in text or " vs " in text or " vs-" in text or "-vs " in text


def _is_single_side_of_comparison(
    query: str,
    head_id: str,
    head_concept: dict,
    candidate_id: str,
    candidate_concept: dict,
) -> bool:
    if not (_is_comparison_query(query) and _is_comparison_concept(head_id, head_concept)):
        return False
    if head_concept.get("category") != "security":
        return False
    if _is_comparison_concept(candidate_id, candidate_concept):
        return False
    head_tokens = _id_tokens(head_id)
    candidate_tokens = _id_tokens(candidate_id)
    if len(head_tokens & candidate_tokens) < 2:
        return False
    return bool(head_tokens - candidate_tokens)


def _id_tokens(concept_id: str) -> set[str]:
    _, _, stem = concept_id.partition("/")
    return {
        token
        for token in re.split(r"[-_/]+", stem.lower())
        if len(token) >= 3 and token not in {"basics", "primer", "guide", "card"}
    }


def _is_routing_head(concept_id: str) -> bool:
    return any(marker in concept_id for marker in ("splitter", "router", "first-check", "chooser"))


def _is_mission_bridge_concept(concept_id: str) -> bool:
    return any(
        marker in concept_id
        for marker in ("-bridge", "roomescape", "lotto", "shopping-cart", "baseball", "blackjack")
    )


def _fuse_lexical(hits: list[SearchHit], lexical_hits: list[SearchHit]) -> list[SearchHit]:
    if not lexical_hits:
        return hits
    head = hits[0] if hits else None
    by_id: dict[str, SearchHit] = {h.concept_id: h for h in hits}
    dense_rank = {h.concept_id: idx for idx, h in enumerate(hits, start=1)}
    lexical_rank = {h.concept_id: idx for idx, h in enumerate(lexical_hits, start=1)}
    for hit in lexical_hits:
        by_id.setdefault(hit.concept_id, hit)

    fused: list[SearchHit] = []
    for cid, hit in by_id.items():
        if head is not None and cid == head.concept_id:
            continue
        dense_part = 1.0 / (RRF_K + dense_rank[cid]) if cid in dense_rank else 0.0
        lexical_part = 1.0 / (RRF_K + lexical_rank[cid]) if cid in lexical_rank else 0.0
        score = dense_part * 0.65 + lexical_part * 0.35
        if cid in dense_rank and cid in lexical_rank:
            source = "fusion"
        elif cid in lexical_rank:
            source = "lexical"
        else:
            source = hit.source
        fused.append(SearchHit(
            concept_id=cid,
            score=score,
            category=hit.category,
            title=hit.title,
            source=source,
        ))
    fused.sort(key=lambda h: -h.score)
    return ([head] if head is not None else []) + fused
