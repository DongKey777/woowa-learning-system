"""Tests for core.ask_render — the shared session-facing stdout slimming.

Guards the invariant that motivated extracting this helper: the warm daemon path
(_render_ask_stdout) and the cold bin/ask client must slim response_hints /
response_quality_hint to the SAME session-facing subset. A regression here means
the session re-reads ~3300 chars of dead telemetry every turn (the bug the
extraction fixed).
"""

from core.ask_render import (
    SESSION_HINT_KEYS,
    SESSION_QUALITY_KEYS,
    session_quality_hint,
    session_response_hints,
)

# A full response_hints as the daemon emits it — 9 keys, only 3 session-facing here.
_FULL_HINTS = {
    "citation_markdown": "참고: [a](p) [b](q)",
    "citation_paths": ["corpus/a.md", "corpus/b.md"],
    "citation_concept_ids": ["a", "b"],
    "citation_trace": [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.8}],
    "tier_downgrade": None,
    "fallback_disclaimer": None,
    "reformulated_query": None,
    "dense_margin": 0.07,
    "rerank_gate": "skip",
}
_FULL_RQ = {
    "command_template": "bin/learn-response-quality --source-event-id e1 ...",
    "source_event_id": "e1",
    "full_body_path_template": "... --response-path <answer.md>",
    "response_excerpt_cap": 5000,
}


def test_response_hints_keeps_only_session_subset():
    slim = session_response_hints(_FULL_HINTS)
    # Only the non-None session keys survive; the parallel citation arrays are dropped.
    assert slim == {
        "citation_markdown": "참고: [a](p) [b](q)",
        "dense_margin": 0.07,
        "rerank_gate": "skip",
    }
    assert "citation_paths" not in slim
    assert "citation_trace" not in slim


def test_quality_hint_keeps_only_fallback_essentials():
    slim = session_quality_hint(_FULL_RQ)
    assert slim == {
        "command_template": "bin/learn-response-quality --source-event-id e1 ...",
        "source_event_id": "e1",
    }
    assert "full_body_path_template" not in slim


def test_empty_inputs_return_none():
    assert session_response_hints(None) is None
    assert session_response_hints({}) is None
    assert session_quality_hint(None) is None
    # A hint dict with no session-facing keys collapses to None (no empty `{}` line).
    assert session_response_hints({"citation_paths": ["x"]}) is None


def test_slim_is_substantially_smaller():
    import json

    full = len(json.dumps(_FULL_HINTS, ensure_ascii=False)) + len(
        json.dumps(_FULL_RQ, ensure_ascii=False)
    )
    slim = len(json.dumps(session_response_hints(_FULL_HINTS), ensure_ascii=False)) + len(
        json.dumps(session_quality_hint(_FULL_RQ), ensure_ascii=False)
    )
    assert slim < full  # the whole point — telemetry is dropped from session stdout


def test_key_lists_are_disjoint_and_nonempty():
    assert SESSION_HINT_KEYS and SESSION_QUALITY_KEYS
    assert not (set(SESSION_HINT_KEYS) & set(SESSION_QUALITY_KEYS))
