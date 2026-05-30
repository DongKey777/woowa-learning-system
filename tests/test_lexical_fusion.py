"""Tests for cheap lexical fusion rerank_fn."""
from __future__ import annotations

import json
from pathlib import Path

from rag.corpus_loader import LoadedCorpus
from rag.search import SearchHit
from core.lexical_fusion import (
    _LEXICAL_CACHE,
    _fuse_lexical,
    _hinted_category,
    _lexical_candidates,
    _promote_canonical_candidate,
    _refine_confusable_order,
    load_lexical_sidecar,
    make_lexical_fusion_fn,
    preload_lexical_entries,
    write_lexical_sidecar,
)


def _concept(
    cid: str,
    title: str,
    category: str,
    *,
    summary: str = "",
    body_markdown: str = "",
    expected_queries: list[str] | None = None,
    aliases: list[str] | None = None,
    level: str = "beginner",
) -> dict:
    return {
        "id": cid,
        "title": title,
        "category": category,
        "level": level,
        "summary": summary,
        "body_markdown": body_markdown,
        "expected_queries": expected_queries or [],
        "aliases": aliases or [],
        "metadata": {
            "schema_version": "v2",
            "created_at": "2026-05-23",
            "last_modified": "2026-05-23",
        },
    }


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


def test_lexical_sidecar_round_trip_preserves_candidates(tmp_path: Path) -> None:
    corpus = LoadedCorpus(concepts={
        "spring/bean": _concept(
            "spring/bean", "Spring Bean", "spring",
            summary="bean basics", body_markdown="dependency injection container",
            aliases=["Bean DI"], expected_queries=["bean이 뭐야"],
        ),
        "database/index": _concept(
            "database/index", "Database Index", "database",
            body_markdown="btree index query plan",
        ),
    }, failures=[])
    path = tmp_path / "lexical_fusion_sidecar.json"

    stats = write_lexical_sidecar(corpus, path)
    _LEXICAL_CACHE.clear()
    loaded = load_lexical_sidecar(path, corpus)

    assert stats["entries"] == 2
    assert stats["size_bytes"] > 0
    assert loaded is not None
    assert isinstance(loaded[0]["title_tokens"], list)
    assert loaded[0]["title_tokens"]
    assert loaded[0]["body_tokens"]
    _LEXICAL_CACHE[id(corpus)] = (corpus, loaded)
    assert "spring/bean" in [h.concept_id for h in _lexical_candidates("Bean DI", corpus, 3)]


def test_lexical_sidecar_rejects_stale_corpus(tmp_path: Path) -> None:
    corpus = LoadedCorpus(concepts={
        "spring/bean": _concept("spring/bean", "Spring Bean", "spring"),
    }, failures=[])
    path = tmp_path / "lexical_fusion_sidecar.json"
    write_lexical_sidecar(corpus, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["concept_count"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_lexical_sidecar(path, corpus) is None


def test_preload_lexical_entries_builds_then_loads_sidecar(tmp_path: Path) -> None:
    corpus = LoadedCorpus(concepts={
        "spring/bean": _concept("spring/bean", "Spring Bean", "spring"),
    }, failures=[])
    path = tmp_path / "lexical_fusion_sidecar.json"

    first = preload_lexical_entries(corpus, path)
    _LEXICAL_CACHE.clear()
    second = preload_lexical_entries(corpus, path)

    assert first["source"] == "built"
    assert second["source"] == "sidecar"
    assert second["entries"] == 1


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


def test_strong_lexical_lookalike_does_not_override_dense_head() -> None:
    # A cross-category lexical lookalike must NOT displace the dense top-1:
    # this aggressive override regressed real paraphrased queries (Pillar 0).
    concepts = {
        "database/roomescape-table-drill": _concept(
            "database/roomescape-table-drill",
            "Roomescape Reservation Table Drill",
            "database",
            aliases=["roomescape table relationship"],
            expected_queries=["예약 테이블 관계를 연습하고 싶어"],
        ),
        "software-engineering/roomescape-dao-vs-repository-bridge": _concept(
            "software-engineering/roomescape-dao-vs-repository-bridge",
            "roomescape DAO Repository naming bridge",
            "software-engineering",
            aliases=["JdbcReservationRepository 의도", "Roomescape DAO Repository"],
            expected_queries=["DAO와 Repository 차이가 뭐야?"],
        ),
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    rerank = make_lexical_fusion_fn(tiny, expand=3)
    seed = [
        SearchHit(
            "database/roomescape-table-drill",
            0.9,
            "database",
            "Roomescape Table",
            "dense",
        ),
        SearchHit(
            "software-engineering/roomescape-dao-vs-repository-bridge",
            0.8,
            "software-engineering",
            "DAO Repository",
            "dense",
        ),
    ]

    hits = rerank("JdbcReservationRepository DAO vs Repository naming bridge", seed)

    assert hits[0].concept_id == "database/roomescape-table-drill"


def test_strong_lexical_only_candidate_does_not_override_dense_head() -> None:
    # Even when the only lexical candidate has a richer token overlap, the dense
    # top-1 head is preserved rather than displaced (Pillar 0).
    concepts = {
        "data-structure/counting-bloom-cuckoo-filter-deletion-chooser": _concept(
            "data-structure/counting-bloom-cuckoo-filter-deletion-chooser",
            "Counting Bloom Cuckoo Filter Deletion Chooser",
            "data-structure",
            aliases=["Bloom Cuckoo filter deletion false positive"],
            expected_queries=["Bloom filter 삭제와 Cuckoo filter 차이가 뭐야?"],
        ),
        "data-structure/bloom-cuckoo-counting-filter-membership-perspective-map": _concept(
            "data-structure/bloom-cuckoo-counting-filter-membership-perspective-map",
            "Bloom Cuckoo Counting Filter Membership Perspective Map",
            "data-structure",
            aliases=[
                "Bloom Cuckoo Counting filter membership",
                "membership filter cache guard",
            ],
            expected_queries=[
                "캐시 앞단에서 Bloom/Cuckoo filter를 쓸 때 false positive, deletion, latency를 어떻게 판단해?"
            ],
        ),
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    rerank = make_lexical_fusion_fn(tiny, expand=3)
    seed = [
        SearchHit(
            "data-structure/counting-bloom-cuckoo-filter-deletion-chooser",
            0.9,
            "data-structure",
            "Counting Bloom Cuckoo Filter Deletion Chooser",
            "dense",
        )
    ]

    hits = rerank(
        "Bloom Cuckoo Counting filter membership Bloom filter false positive "
        "삭제 membership filter cache guard",
        seed,
    )

    assert hits[0].concept_id == (
        "data-structure/counting-bloom-cuckoo-filter-deletion-chooser"
    )


def test_strong_lexical_scan_does_not_displace_dense_head() -> None:
    # A deep strong-lexical match further down the list must not be promoted
    # over the dense top-1 head (Pillar 0).
    concepts = {
        "system-design/stateful-stream-processor-state-store-checkpoint-recovery-design": _concept(
            "system-design/stateful-stream-processor-state-store-checkpoint-recovery-design",
            "Stateful Stream Processor State Store Checkpoint Recovery Design",
            "system-design",
            aliases=["stream processor state store checkpoint recovery"],
        ),
        "spring/async-mvc-streaming-observability-playbook": _concept(
            "spring/async-mvc-streaming-observability-playbook",
            "Spring Async MVC Streaming Observability Playbook",
            "spring",
            aliases=["spring async mvc streaming observability"],
        ),
        "system-design/exactly-once-stream-processing-idempotent-sinks-checkpoint-chooser": _concept(
            "system-design/exactly-once-stream-processing-idempotent-sinks-checkpoint-chooser",
            "Exactly Once Stream Processing Idempotent Sinks Checkpoint Chooser",
            "system-design",
            aliases=[
                "exactly once stream processing idempotent sink checkpoint",
                "source offset state checkpoint transactional sink replay latency",
                "stream processing checkpoint sink commit ordering",
            ],
        ),
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    rerank = make_lexical_fusion_fn(tiny, expand=8)
    seed = [
        SearchHit(
            "system-design/stateful-stream-processor-state-store-checkpoint-recovery-design",
            0.9,
            "system-design",
            "Stateful Stream Processor",
            "dense",
        ),
        SearchHit(
            "spring/async-mvc-streaming-observability-playbook",
            0.8,
            "spring",
            "Spring Async MVC Streaming",
            "dense",
        ),
    ]

    hits = rerank(
        "exactly once stream processing source offset checkpoint state store "
        "idempotent sink transactional sink replay latency",
        seed,
    )

    assert hits[0].concept_id == (
        "system-design/stateful-stream-processor-state-store-checkpoint-recovery-design"
    )


def test_strong_lexical_signal_preserves_head_when_head_also_matches() -> None:
    concepts = {
        "security/auth-failure-response-401-403-404": _concept(
            "security/auth-failure-response-401-403-404",
            "401 403 404 인증 인가 응답 코드",
            "security",
            aliases=["401 403 404 인증 인가 응답 코드"],
            expected_queries=["401 403 404 응답 코드 차이"],
        ),
        "security/auth-response-code-triage-micro-drill": _concept(
            "security/auth-response-code-triage-micro-drill",
            "Auth response code drill",
            "security",
            aliases=["401 403 404 인증 인가 응답 코드 triage drill"],
            expected_queries=["401 403 404 드릴"],
        ),
    }
    tiny = LoadedCorpus(concepts=concepts, failures=[])
    rerank = make_lexical_fusion_fn(tiny, expand=3)
    seed = [
        SearchHit(
            "security/auth-failure-response-401-403-404",
            0.9,
            "security",
            "401 403 404",
            "dense",
        ),
        SearchHit(
            "security/auth-response-code-triage-micro-drill",
            0.8,
            "security",
            "drill",
            "dense",
        ),
    ]

    hits = rerank("401 vs 403 vs 404 응답 코드 차이 인증 인가", seed)

    assert hits[0].concept_id == "security/auth-failure-response-401-403-404"


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


def test_unscoped_comparison_demotes_mission_bridge() -> None:
    concepts = {
        "design-pattern/bridge-strategy-vs-factory-runtime-selection": _concept(
            "design-pattern/bridge-strategy-vs-factory-runtime-selection",
            "Strategy vs Factory Runtime Selection",
            "design-pattern",
        ),
        "design-pattern/factory": _concept("design-pattern/factory", "Factory", "design-pattern"),
        "design-pattern/roomescape-strategy-vs-factory-bridge": _concept(
            "design-pattern/roomescape-strategy-vs-factory-bridge",
            "roomescape Strategy vs Factory Bridge",
            "design-pattern",
        ),
    }
    hits = [
        SearchHit("design-pattern/bridge-strategy-vs-factory-runtime-selection", 1.0, "design-pattern", "Strategy vs Factory", "dense"),
        SearchHit("design-pattern/roomescape-strategy-vs-factory-bridge", 0.8, "design-pattern", "roomescape bridge", "fusion"),
        SearchHit("design-pattern/factory", 0.7, "design-pattern", "Factory", "fusion"),
    ]

    generic = _refine_confusable_order("런타임에 알고리즘 갈아끼우는 게 strategy야 factory야?", hits, LoadedCorpus(concepts, []))
    scoped = _refine_confusable_order("roomescape 검증에 Strategy 패턴 어떻게 적용해?", hits, LoadedCorpus(concepts, []))

    assert "design-pattern/roomescape-strategy-vs-factory-bridge" not in [h.concept_id for h in generic]
    assert scoped[1].concept_id == "design-pattern/roomescape-strategy-vs-factory-bridge"
