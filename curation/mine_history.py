"""Mine learner history.jsonl for corpus-improvement signals.

Patterns extracted:
1. citation_mismatch_clusters: queries where declared citations didn't match
   expected — hints that the matched concept needs more aliases / better
   expected_queries
2. high_uncertain_concepts: concepts repeatedly tagged uncertain — possibly
   underspecified summary / missing prerequisites
3. low_hit_queries: queries that returned <3 hits — corpus gap candidate
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MinedSignal:
    pattern: str  # "citation_mismatch" | "high_uncertain" | "low_hit"
    target_concept_id: str | None
    queries: list[str]
    frequency: int
    metadata: dict = field(default_factory=dict)


# Real response-quality flags that signal a citation/retrievability problem on
# the matched concept (the learner's answer cited the wrong / extra / missing doc).
_CITATION_SIGNAL_FLAGS = ("citation_mismatch", "extra_citation", "missing_citation")


def mine(history_path: Path, mode_filter: str = "learning") -> list[MinedSignal]:
    return mine_events(_load_events(history_path, mode_filter))


def mine_events(events: list[dict]) -> list[MinedSignal]:
    citation_mismatch: defaultdict[str, list[str]] = defaultdict(list)
    uncertain_counts: Counter = Counter()
    low_hit_queries: list[tuple[str, list]] = []
    for ev in events:
        p = ev.get("payload", {})
        flags = p.get("quality_flags") or []
        if any(f in flags for f in _CITATION_SIGNAL_FLAGS):
            declared = p.get("citations") or []
            for c in declared:
                citation_mismatch[c].append(p.get("prompt", ""))
        for u in p.get("uncertain", []) or []:
            uncertain_counts[u] += 1
        hits = p.get("hits") or []
        if 0 < len(hits) < 3:
            low_hit_queries.append((p.get("prompt", ""), hits))

    signals: list[MinedSignal] = []
    for cid, queries in citation_mismatch.items():
        if len(queries) >= 2:
            signals.append(MinedSignal(
                pattern="citation_mismatch",
                target_concept_id=cid,
                queries=queries[:5],
                frequency=len(queries),
            ))
    for cid, freq in uncertain_counts.most_common(20):
        if freq >= 3:
            signals.append(MinedSignal(
                pattern="high_uncertain",
                target_concept_id=cid,
                queries=[],
                frequency=freq,
            ))
    if low_hit_queries:
        signals.append(MinedSignal(
            pattern="low_hit",
            target_concept_id=None,
            queries=[q for q, _ in low_hit_queries[:10]],
            frequency=len(low_hit_queries),
        ))
    return signals


def _load_events(path: Path, mode_filter: str) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if mode_filter and ev.get("mode") not in (mode_filter, None):
            continue
        out.append(ev)
    return out


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build_joined_events(
    history_path: Path,
    response_quality_path: Path | None = None,
    mode_filter: str = "learning",
) -> list[dict]:
    """Join real learner state into the event/payload shape ``mine_events`` reads.

    Real ``history.jsonl`` rag_ask payloads carry ``prompt`` and ``top_concept_ids``
    but not the ``quality_flags``/``citations``/``hits`` keys ``mine`` expects.
    This joins ``response-quality.jsonl`` (by ``source_event_id``) onto each rag_ask
    event and projects ``top_concept_ids`` into ``hits`` so mining works on real data.
    """
    rq_by_event: dict[str, dict] = {}
    if response_quality_path is not None:
        for rq in _load_jsonl(response_quality_path):
            sid = rq.get("source_event_id")
            if sid:
                rq_by_event[sid] = rq

    joined: list[dict] = []
    for ev in _load_events(history_path, mode_filter):
        if ev.get("event_type") not in ("rag_ask", None):
            continue
        p = dict(ev.get("payload") or {})
        rq = rq_by_event.get(ev.get("event_id"))
        if rq is not None:
            p.setdefault("quality_flags", rq.get("quality_flags") or [])
            p.setdefault("citations", rq.get("citation_paths_declared") or [])
        p.setdefault("hits", [{"concept_id": c} for c in (p.get("top_concept_ids") or [])])
        joined.append({"mode": ev.get("mode"), "event_id": ev.get("event_id"), "payload": p})
    return joined


def mine_real(
    state_dir: Path = Path("state/learner"),
    mode_filter: str = "learning",
) -> list[MinedSignal]:
    """Mine real learner state (history + response-quality + profile.uncertain).

    ``profile.json`` already holds an authoritative ``concepts.uncertain`` list, so
    those are surfaced directly as ``high_uncertain`` signals rather than re-derived
    from event counts.
    """
    state_dir = Path(state_dir)
    events = build_joined_events(
        state_dir / "history.jsonl",
        state_dir / "response-quality.jsonl",
        mode_filter=mode_filter,
    )
    signals = mine_events(events)

    profile_path = state_dir / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            profile = {}
        uncertain = (profile.get("concepts") or {}).get("uncertain") or []
        have = {s.target_concept_id for s in signals if s.pattern == "high_uncertain"}
        for cid in uncertain:
            if cid in have:
                continue
            signals.append(MinedSignal(
                pattern="high_uncertain",
                target_concept_id=cid,
                queries=[],
                frequency=0,
                metadata={"source": "profile.concepts.uncertain"},
            ))
    return signals
