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


def mine(history_path: Path, mode_filter: str = "learning") -> list[MinedSignal]:
    events = _load_events(history_path, mode_filter)

    citation_mismatch: defaultdict[str, list[str]] = defaultdict(list)
    uncertain_counts: Counter = Counter()
    low_hit_queries: list[tuple[str, list]] = []
    for ev in events:
        p = ev.get("payload", {})
        flags = p.get("quality_flags") or []
        if "citation_mismatch" in flags:
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
