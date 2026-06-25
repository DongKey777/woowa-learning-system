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

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE_PATH = _REPO_ROOT / "tests" / "fixtures" / "exact_owner_baseline.json"
_APPLIED_LOG_DIR = _REPO_ROOT / "state" / "curation"


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
    timestamp: str | None = None,
) -> ApplyResult:
    """Apply proposals to the corpus and flush an applied-log for rollback tracking.

    `timestamp` names the applied-log file (state/curation/applied-<ts>.jsonl) and is
    INJECTED by the caller — apply() does not read the wall clock for the filename, so
    the log path is deterministic per invocation. Defaults to None → no flush (callers
    that want the rollback log pass an explicit timestamp; tests pin a fixed value).
    """
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

        # add_confusable registers the relation on BOTH endpoints (reciprocal) so the
        # confusable graph stays symmetric — a one-sided edge made the resolver's
        # sibling-disambiguation see the link from only one direction (friction #3).
        if change.operation == "add_confusable":
            ok, extra = _apply_confusable_reciprocal(entity_target=target, change=change,
                                                     corpus_dir=corpus_dir, dry_run=dry_run)
            if not ok:
                skipped.append((change, extra))  # extra carries the skip reason
                continue
            touched.extend(extra)  # extra carries the touched path list (both sides)
            applied.append(change)
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
    result = ApplyResult(applied=applied, skipped=skipped, touched_paths=touched,
                         ownership_new_steals=ownership_new_steals)
    if timestamp and not dry_run:
        _flush_applied_log(result, timestamp)
    return result


def _bump_last_modified(entity: dict) -> None:
    entity.setdefault("metadata", {})["last_modified"] = str(date.today())


def _apply_confusable_reciprocal(
    entity_target: Path,
    change: ProposedChange,
    corpus_dir: Path,
    dry_run: bool,
) -> tuple[bool, object]:
    """Register confusable_with on BOTH the target and the `with` concept.

    Returns (True, [touched paths]) on success, or (False, reason) when the edge is
    already symmetric (idempotent skip) or the `with` concept file is missing.
    """
    cid = change.target_concept_id
    with_id = change.payload.get("with")
    if not with_id:
        return False, "add_confusable missing 'with'"
    if with_id == cid:
        return False, "add_confusable self-reference"

    with_path = corpus_dir / (with_id + ".json")
    if not with_path.exists():
        return False, f"confusable target not found: {with_id}"

    target_entity = json.loads(entity_target.read_text(encoding="utf-8"))
    with_entity = json.loads(with_path.read_text(encoding="utf-8"))

    changed_target = _add_confusable_edge(target_entity, with_id)
    changed_with = _add_confusable_edge(with_entity, cid)

    if not changed_target and not changed_with:
        return False, "operation produced no change (idempotent)"

    touched: list[Path] = []
    if changed_target:
        _bump_last_modified(target_entity)
        if not dry_run:
            entity_target.write_text(json.dumps(target_entity, ensure_ascii=False, indent=2), encoding="utf-8")
        touched.append(entity_target)
    if changed_with:
        _bump_last_modified(with_entity)
        if not dry_run:
            with_path.write_text(json.dumps(with_entity, ensure_ascii=False, indent=2), encoding="utf-8")
        touched.append(with_path)
    return True, touched


def _add_confusable_edge(entity: dict, other_id: str) -> bool:
    """Append other_id to entity.relations.confusable_with if absent. Returns True if
    the list was mutated (idempotent: returns False when the edge already exists)."""
    relations = entity.setdefault("relations", {})
    confusable = relations.setdefault("confusable_with", [])
    if other_id in confusable:
        return False
    confusable.append(other_id)
    return True


def _flush_applied_log(result: ApplyResult, timestamp: str) -> None:
    """Append the apply outcome to state/curation/applied-<ts>.jsonl — the rollback
    source of truth. One JSON line per applied/skipped change plus a summary record
    listing touched paths and any ownership steals. Best-effort: a flush failure never
    propagates (the corpus write already happened; the log is advisory)."""
    try:
        _APPLIED_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _APPLIED_LOG_DIR / f"applied-{timestamp}.jsonl"
        lines: list[str] = []
        for ch in result.applied:
            lines.append(json.dumps({
                "status": "applied",
                "target_concept_id": ch.target_concept_id,
                "operation": ch.operation,
                "payload": ch.payload,
                "rationale": ch.rationale,
            }, ensure_ascii=False))
        for ch, reason in result.skipped:
            lines.append(json.dumps({
                "status": "skipped",
                "target_concept_id": ch.target_concept_id,
                "operation": ch.operation,
                "payload": ch.payload,
                "reason": reason,
            }, ensure_ascii=False))
        lines.append(json.dumps({
            "status": "summary",
            "timestamp": timestamp,
            "applied": len(result.applied),
            "skipped": len(result.skipped),
            "touched_paths": [str(p) for p in result.touched_paths],
            "ownership_new_steals": result.ownership_new_steals,
        }, ensure_ascii=False))
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        return  # advisory log; never break the apply on a write failure


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
    # add_confusable is handled reciprocally in apply() (both endpoints), not here.
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
