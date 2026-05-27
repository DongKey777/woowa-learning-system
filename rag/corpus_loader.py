"""JSON concept corpus loader + schema validator.

Hypothesis (Phase 1): 3199 JSON concepts load + validate in <2s on M4.
Errors return structured `(failed_path, error_message)` list — no silent skip.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_DIR = REPO_ROOT / "corpus" / "concepts"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "corpus" / "schemas" / "concept-v2.schema.json"
DEFAULT_CORPUS_SNAPSHOT_PATH = REPO_ROOT / "state" / "index" / "corpus_snapshot.json"
CORPUS_SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class LoadedCorpus:
    concepts: dict[str, dict]  # id → concept entity
    failures: list[tuple[str, str]]  # (path, error message)

    @property
    def categories(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.concepts.values():
            out[c["category"]] = out.get(c["category"], 0) + 1
        return out


def load_corpus(
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    strict: bool = True,
) -> LoadedCorpus:
    """Load + validate every JSON file under corpus_dir.

    strict=True (default): raise if any concept fails validation.
    strict=False: return concepts that passed, collect failures separately.
    """
    import jsonschema

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    concepts: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []

    for path in sorted(corpus_dir.rglob("*.json")):
        rel = str(path.relative_to(corpus_dir))
        try:
            entity = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append((rel, f"json decode: {exc}"))
            continue
        errors = list(validator.iter_errors(entity))
        if errors:
            failures.append((rel, "; ".join(e.message for e in errors[:3])))
            continue
        cid = entity["id"]
        if cid in concepts:
            failures.append((rel, f"duplicate id: {cid}"))
            continue
        concepts[cid] = entity

    if strict and failures:
        msg_lines = [f"corpus load failed ({len(failures)} errors):"]
        msg_lines.extend(f"  {p}: {m}" for p, m in failures[:10])
        raise ValueError("\n".join(msg_lines))

    return LoadedCorpus(concepts=concepts, failures=failures)


def write_corpus_snapshot(
    corpus: LoadedCorpus,
    path: Path = DEFAULT_CORPUS_SNAPSHOT_PATH,
) -> dict:
    """Write a pre-validated corpus snapshot for daemon cold-start."""
    payload = {
        "version": CORPUS_SNAPSHOT_VERSION,
        "concept_count": len(corpus.concepts),
        "concepts": corpus.concepts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
    return {
        "source": "built",
        "path": str(path),
        "concepts": len(corpus.concepts),
        "size_bytes": path.stat().st_size,
    }


def load_corpus_snapshot(
    path: Path = DEFAULT_CORPUS_SNAPSHOT_PATH,
) -> LoadedCorpus | None:
    """Load a corpus snapshot written after strict validation.

    The snapshot is for runtime latency only. Maintainer/index-fetch paths still
    use strict JSON schema validation before writing it.
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != CORPUS_SNAPSHOT_VERSION:
        return None
    concepts_raw = payload.get("concepts")
    if not isinstance(concepts_raw, dict):
        return None
    concept_count = payload.get("concept_count")
    if concept_count != len(concepts_raw):
        return None
    concepts: dict[str, dict] = {}
    for cid, concept in concepts_raw.items():
        if not isinstance(concept, dict) or concept.get("id") != cid:
            return None
        concepts[str(cid)] = concept
    return LoadedCorpus(concepts=concepts, failures=[])


def load_corpus_runtime(
    snapshot_path: Path = DEFAULT_CORPUS_SNAPSHOT_PATH,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> LoadedCorpus:
    """Load the validated snapshot, falling back to strict corpus validation."""
    snapshot = load_corpus_snapshot(snapshot_path)
    if snapshot is not None:
        return snapshot
    return load_corpus(corpus_dir=corpus_dir, schema_path=schema_path, strict=True)


def encoding_text(concept: dict) -> str:
    """Concatenated text used for embedding (D2-aligned).

    title | aliases | expected_queries | summary — all retrieval-friendly fields.
    """
    parts: list[str] = [concept["title"]]
    if concept.get("aliases"):
        parts.append(" ".join(concept["aliases"]))
    if concept.get("expected_queries"):
        parts.append(" ".join(concept["expected_queries"]))
    if concept.get("summary"):
        parts.append(concept["summary"])
    return " | ".join(parts)
