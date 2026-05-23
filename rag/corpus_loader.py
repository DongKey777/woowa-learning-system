"""JSON concept corpus loader + schema validator.

Hypothesis (Phase 1): 3199 JSON concepts load + validate in <2s on M4.
Errors return structured `(failed_path, error_message)` list — no silent skip.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus" / "concepts"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "corpus" / "schemas" / "concept-v2.schema.json"


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
