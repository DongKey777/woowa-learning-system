"""Tests for cheap lexical fusion rerank_fn."""
from __future__ import annotations

from rag.corpus_loader import LoadedCorpus
from rag.search import SearchHit
from core.lexical_fusion import (
    _fuse_lexical,
    _hinted_category,
    _lexical_candidates,
    _promote_canonical_candidate,
    _refine_confusable_order,
    make_lexical_fusion_fn,
)


def test_lexical_fusion_adds_exact_corpus_candidate_without_moving_head() -> None:
    concepts = {
        "spring/bean": {
            "id": "spring/bean", "title": "Spring Bean", "category": "spring",
            "level": "beginner", "summary": "bean basics", "body_markdown": "",
            "expected_queries": ["bean이 뭐야"],
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "spring/transaction": {
            "id": "spring/transaction", "title": "Transaction", "category": "spring",
            "level": "beginner", "summary": "tx", "body_markdown": "",
            "expected_queries": [],
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    seed = [SearchHit("software-engineering/di", 0.9, "software-engineering", "DI", "dense")]

    lexical = _lexical_candidates("Spring Bean이 뭐야", tiny, limit=3)
    fused = _fuse_lexical(seed, lexical)

    assert fused[0].concept_id == "software-engineering/di"
    assert "spring/bean" in [h.concept_id for h in fused]
    assert any(h.source == "lexical" for h in fused)


def test_exact_expected_query_promotes_matching_concept() -> None:
    concepts = {
        "spring/bean": {
            "id": "spring/bean", "title": "Spring Bean", "category": "spring",
            "level": "beginner", "summary": "bean basics", "body_markdown": "",
            "expected_queries": ["bean이 뭐야"],
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "software-engineering/di": {
            "id": "software-engineering/di", "title": "DI", "category": "software-engineering",
            "level": "beginner", "summary": "di", "body_markdown": "",
            "expected_queries": [],
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    rerank = make_lexical_fusion_fn(tiny, expand=3)
    seed = [SearchHit("software-engineering/di", 0.9, "software-engineering", "DI", "dense")]

    hits = rerank("bean이 뭐야", seed)

    assert hits[0].concept_id == "spring/bean"
    assert hits[0].source == "lexical_exact"


def test_spring_design_terms_do_not_force_spring_category() -> None:
    assert _hinted_category("Spring DI container vs factory pattern") is None


def test_canonical_candidate_promotes_over_specialized_bridge() -> None:
    hits = [
        SearchHit("spring/roomescape-transactional-boundary-bridge", 0.9, "spring", "Bridge", "dense"),
        SearchHit("spring/transactional-basics", 0.8, "spring", "@Transactional", "dense"),
    ]

    promoted = _promote_canonical_candidate(hits)

    assert promoted[0].concept_id == "spring/transactional-basics"


def test_confusable_neighbor_is_demoted_after_neutral_candidate() -> None:
    concepts = {
        "database/lock-timeout-first-check": {
            "id": "database/lock-timeout-first-check", "title": "Lock Timeout", "category": "database",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": ["database/deadlock-vs-lock-wait-timeout"]},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "database/deadlock-vs-lock-wait-timeout": {
            "id": "database/deadlock-vs-lock-wait-timeout", "title": "Deadlock vs Lock Wait Timeout", "category": "database",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "database/timeout-checklist": {
            "id": "database/timeout-checklist", "title": "Timeout Checklist", "category": "database",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    hits = [
        SearchHit("database/lock-timeout-first-check", 1.0, "database", "Lock Timeout", "dense"),
        SearchHit("database/deadlock-vs-lock-wait-timeout", 0.8, "database", "Deadlock vs Lock", "fusion"),
        SearchHit("database/timeout-checklist", 0.7, "database", "Timeout Checklist", "fusion"),
    ]

    refined = _refine_confusable_order("lock timeout blocker first check", hits, LoadedCorpus(concepts, []))

    assert [h.concept_id for h in refined] == [
        "database/lock-timeout-first-check",
        "database/timeout-checklist",
        "database/deadlock-vs-lock-wait-timeout",
    ]


def test_comparison_neighbor_is_promoted_for_comparison_query() -> None:
    concepts = {
        "design-pattern/composition-over-inheritance-practical": {
            "id": "design-pattern/composition-over-inheritance-practical",
            "title": "Composition over Inheritance", "category": "design-pattern",
            "level": "intermediate", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": ["design-pattern/template-method-vs-strategy"]},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "design-pattern/template-method-basics": {
            "id": "design-pattern/template-method-basics", "title": "Template Method Basics",
            "category": "design-pattern", "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "design-pattern/template-method-vs-strategy": {
            "id": "design-pattern/template-method-vs-strategy", "title": "Template Method vs Strategy",
            "category": "design-pattern", "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    hits = [
        SearchHit("design-pattern/composition-over-inheritance-practical", 1.0, "design-pattern", "Composition", "dense"),
        SearchHit("design-pattern/template-method-basics", 0.8, "design-pattern", "Template", "fusion"),
        SearchHit("design-pattern/template-method-vs-strategy", 0.7, "design-pattern", "Template vs Strategy", "relations_walk"),
    ]

    refined = _refine_confusable_order("Composition vs Template Method 비교", hits, LoadedCorpus(concepts, []))

    assert refined[1].concept_id == "design-pattern/template-method-vs-strategy"


def test_advanced_candidate_demoted_for_beginner_intro_query() -> None:
    concepts = {
        "spring/bean-lifecycle-basics": {
            "id": "spring/bean-lifecycle-basics", "title": "Bean Lifecycle", "category": "spring",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"next_docs": [], "confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "spring/beanfactorypostprocessor-vs-beanpostprocessor-lifecycle": {
            "id": "spring/beanfactorypostprocessor-vs-beanpostprocessor-lifecycle",
            "title": "BeanFactoryPostProcessor vs BeanPostProcessor", "category": "spring",
            "level": "advanced", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "spring/bean-scope": {
            "id": "spring/bean-scope", "title": "Bean Scope", "category": "spring",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    hits = [
        SearchHit("spring/bean-lifecycle-basics", 1.0, "spring", "Bean Lifecycle", "dense"),
        SearchHit("spring/beanfactorypostprocessor-vs-beanpostprocessor-lifecycle", 0.8, "spring", "BPP", "dense"),
        SearchHit("spring/bean-scope", 0.7, "spring", "Bean Scope", "dense"),
    ]

    refined = _refine_confusable_order("Spring Bean 생명주기 기초 primer", hits, LoadedCorpus(concepts, []))

    assert [h.concept_id for h in refined] == [
        "spring/bean-lifecycle-basics",
        "spring/bean-scope",
        "spring/beanfactorypostprocessor-vs-beanpostprocessor-lifecycle",
    ]


def test_category_mismatch_demoted_when_query_has_strong_category_hint() -> None:
    concepts = {
        "spring/aop-proxy-mechanism": {
            "id": "spring/aop-proxy-mechanism", "title": "AOP Proxy", "category": "spring",
            "level": "intermediate", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "design-pattern/decorator-proxy-basics": {
            "id": "design-pattern/decorator-proxy-basics", "title": "Decorator Proxy", "category": "design-pattern",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
        "spring/aop-basics": {
            "id": "spring/aop-basics", "title": "AOP Basics", "category": "spring",
            "level": "beginner", "summary": "", "body_markdown": "",
            "expected_queries": [],
            "relations": {"confusable_with": []},
            "metadata": {"schema_version": "v2", "created_at": "2026-05-23", "last_modified": "2026-05-23"},
        },
    }
    hits = [
        SearchHit("spring/aop-proxy-mechanism", 1.0, "spring", "AOP Proxy", "dense"),
        SearchHit("design-pattern/decorator-proxy-basics", 0.8, "design-pattern", "Decorator Proxy", "fusion"),
        SearchHit("spring/aop-basics", 0.7, "spring", "AOP", "fusion"),
    ]

    refined = _refine_confusable_order("AOP proxy 메커니즘", hits, LoadedCorpus(concepts, []))

    assert [h.concept_id for h in refined] == [
        "spring/aop-proxy-mechanism",
        "spring/aop-basics",
        "design-pattern/decorator-proxy-basics",
    ]
