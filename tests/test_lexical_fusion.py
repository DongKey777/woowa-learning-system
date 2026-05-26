"""Tests for cheap lexical fusion rerank_fn."""
from __future__ import annotations

from rag.corpus_loader import LoadedCorpus
from rag.search import SearchHit
from core.lexical_fusion import (
    _fuse_lexical,
    _lexical_candidates,
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
