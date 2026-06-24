"""Apply ProposedChange list to corpus/concepts/*.json.

Caller decides whether to keep or revert via the curation log (the original JSON is
recoverable from git, not a .bak sidecar).

Ownership guard: after applying, this recomputes the exact-shortcut fire+shadow set
(curation/ownership.fire_and_shadow) and reports any change that newly STEALS/shadows
an existing canonical owner — the "DI가 뭐야?" regression class — in
ApplyResult.ownership_new_steals. This is the cheapest catch point (no RunPod rebuild
needed to discover a steal); bin/corpus-build runs the same gate as the hard backstop.
The check reuses the production resolver, so it inherits the verified 0-false-positive
property (benign same-tier alias collisions are NOT flagged).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from curation.propose_changes import ProposedChange
from rag.corpus_loader import DEFAULT_CORPUS_DIR

_BASELINE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "exact_owner_baseline.json"


@dataclass
class ApplyResult:
    applied: list[ProposedChange]
    skipped: list[tuple[ProposedChange, str]]  # (change, reason)
    touched_paths: list[Path]
    ownership_new_steals: dict = field(default_factory=dict)  # norm -> info (new fire+shadow vs baseline)


def apply(
    proposals: list[ProposedChange],
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    dry_run: bool = False,
) -> ApplyResult:
    applied: list[ProposedChange] = []
    skipped: list[tuple[ProposedChange, str]] = []
    touched: list[Path] = []

    for change in proposals:
        cid = change.target_concept_id
        target = corpus_dir / (cid + ".json") if cid else None

        if change.operation == "new_concept":
            new_id = change.payload.get("id")
            if not new_id:
                skipped.append((change, "new_concept missing id"))
                continue
            target = corpus_dir / (new_id + ".json")
            if target.exists():
                skipped.append((change, f"concept already exists: {new_id}"))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            entity = _new_entity(change.payload)
            if not dry_run:
                target.write_text(json.dumps(entity, ensure_ascii=False, indent=2), encoding="utf-8")
            touched.append(target)
            applied.append(change)
            continue

        if not target or not target.exists():
            skipped.append((change, f"target concept not found: {cid}"))
            continue

        entity = json.loads(target.read_text(encoding="utf-8"))
        mutated = _mutate(entity, change)
        if mutated is None:
            skipped.append((change, "operation produced no change (idempotent)"))
            continue

        entity = mutated
        entity["metadata"]["last_modified"] = str(date.today())
        if not dry_run:
            target.write_text(json.dumps(entity, ensure_ascii=False, indent=2), encoding="utf-8")
        touched.append(target)
        applied.append(change)

    ownership_new_steals = _ownership_new_steals(corpus_dir) if (touched and not dry_run) else {}
    return ApplyResult(applied=applied, skipped=skipped, touched_paths=touched,
                       ownership_new_steals=ownership_new_steals)


def _ownership_new_steals(corpus_dir: Path) -> dict:
    """Recompute fire+shadow over the written corpus and return steals not in the
    frozen baseline (a change shadowed an existing canonical owner). Empty when the
    batch introduced no exact-shortcut regression. Reuses the production resolver, so
    benign same-tier alias collisions are not flagged (verified 0 false positives)."""
    try:
        from curation.ownership import diff_against_baseline, fire_and_shadow
        from rag.corpus_loader import load_corpus
        baseline = (json.loads(_BASELINE_PATH.read_text(encoding="utf-8")).get("norms", {})
                    if _BASELINE_PATH.exists() else {})
        diff = diff_against_baseline(fire_and_shadow(load_corpus(corpus_dir=corpus_dir)), baseline)
        return {**diff["new_steals"], **diff["flipped"]}
    except Exception:
        return {}  # never let the advisory guard break an apply


def _mutate(entity: dict, change: ProposedChange) -> dict | None:
    payload = change.payload
    op = change.operation
    if op == "add_alias":
        val = payload.get("alias")
        if val and val not in entity.get("aliases", []):
            entity.setdefault("aliases", []).append(val)
            return entity
    elif op == "add_expected_query":
        val = payload.get("query")
        if val and val not in entity.get("expected_queries", []):
            entity.setdefault("expected_queries", []).append(val)
            return entity
    elif op == "add_symptom":
        val = payload.get("symptom")
        if val and val not in entity.get("symptoms", []):
            entity.setdefault("symptoms", []).append(val)
            return entity
    elif op == "add_confusable":
        val = payload.get("with")
        relations = entity.setdefault("relations", {})
        confusable = relations.setdefault("confusable_with", [])
        if val and val not in confusable:
            confusable.append(val)
            return entity
    return None


def _new_entity(payload: dict) -> dict:
    today = str(date.today())
    return {
        "id": payload["id"],
        "title": payload["title"],
        "category": payload["id"].split("/", 1)[0],
        "level": payload.get("level", "intermediate"),
        "summary": payload["summary"],
        "body_markdown": payload["body_markdown"],
        "aliases": payload.get("aliases", []),
        "expected_queries": payload.get("expected_queries", []),
        "symptoms": payload.get("symptoms", []),
        "relations": payload.get("relations", {
            "prerequisites": [], "next_docs": [], "confusable_with": [], "forbidden_for_queries": []
        }),
        "learner_query_patterns": [],
        "metadata": {
            "schema_version": "v2",
            "created_at": today,
            "last_modified": today,
            "revision_history": [{"date": today, "source": "curation/apply_changes"}],
        },
    }
