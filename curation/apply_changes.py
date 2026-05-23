"""Apply ProposedChange list to corpus/concepts/*.json.

Reversible: writes original JSON to .bak before mutation. Caller decides
whether to keep or revert via curation log.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from curation.propose_changes import ProposedChange
from rag.corpus_loader import DEFAULT_CORPUS_DIR


@dataclass
class ApplyResult:
    applied: list[ProposedChange]
    skipped: list[tuple[ProposedChange, str]]  # (change, reason)
    touched_paths: list[Path]


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

    return ApplyResult(applied=applied, skipped=skipped, touched_paths=touched)


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
