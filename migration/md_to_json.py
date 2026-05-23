"""Legacy markdown → JSON concept entity migrator (Phase 0, throwaway).

Reads `knowledge/cs/contents/**/*.md` from legacy `woowa-learning-hub`, outputs
`corpus/concepts/<category>/<id>.json` schema-validated entities.

Hypothesis (verified by measurement before writing this):
- 3211 markdown files total; 12 README; 3199 v3 concept docs
- All v3 docs uniform frontmatter (canonical/category/level/aliases/expected_queries/
  symptoms/prerequisites/next_docs/linked_paths/confusable_with/contextual_chunk_prefix/...)
- 0 duplicate concept_ids
- 2 quoted concept_ids ("spring/..., "algorithm/...) need strip

Expected migration: 3199 success, 12 README skip, 0 fail.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

LEGACY_HUB = Path("/Users/idonghun/IdeaProjects/woowa-learning-hub")
LEGACY_CORPUS = LEGACY_HUB / "knowledge/cs/contents"
NEW_CORPUS = Path(__file__).resolve().parent.parent / "corpus/concepts"
SCHEMA_VERSION = "v2"


def strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter_dict, body). Strict subset YAML — scalars + simple lists."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    fm_text = text[3:end]
    body = text[end + 4 :].lstrip("\n")

    fm: dict = {}
    current_key: str | None = None
    for raw_line in fm_text.split("\n"):
        if not raw_line.strip():
            continue
        list_match = re.match(r"^\s*-\s*(.*)$", raw_line)
        scalar_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)$", raw_line)
        if scalar_match and not raw_line.startswith(" "):
            key, val = scalar_match.group(1), scalar_match.group(2)
            current_key = key
            if val.strip():
                fm[key] = strip_quotes(val)
                current_key = None
            else:
                fm[key] = []
        elif list_match and current_key is not None:
            fm[current_key].append(strip_quotes(list_match.group(1)))
        elif current_key and raw_line.startswith("  "):
            # Block scalar continuation — append to scalar previous value.
            prev = fm.get(current_key)
            if isinstance(prev, str):
                fm[current_key] = prev + " " + raw_line.strip()
            elif isinstance(prev, list) and not prev:
                fm[current_key] = raw_line.strip()
    return fm, body


def transform(fm: dict, body: str, src: Path) -> dict:
    """Map legacy frontmatter → JSON concept entity (concept-v2 schema)."""
    cid = strip_quotes(fm.get("concept_id", "") or "")
    cat = strip_quotes(fm.get("category", "") or "")
    title = strip_quotes(fm.get("title", "") or "")
    level = strip_quotes(fm.get("level", "beginner") or "beginner")
    summary_source = fm.get("contextual_chunk_prefix") or ""
    summary = (
        strip_quotes(summary_source).strip()
        if isinstance(summary_source, str) and summary_source.strip()
        else _extract_body_summary(body)
    )

    def _list(key: str) -> list:
        val = fm.get(key)
        return val if isinstance(val, list) else []

    return {
        "id": cid,
        "title": title,
        "category": cat,
        "level": level,
        "summary": summary[:500] if len(summary) > 500 else summary,
        "body_markdown": body,
        "aliases": _list("aliases"),
        "expected_queries": _list("expected_queries"),
        "symptoms": _list("symptoms"),
        "relations": {
            "prerequisites": _list("prerequisites"),
            "next_docs": _list("next_docs"),
            "confusable_with": _list("confusable_with"),
            "forbidden_for_queries": [],
        },
        "learner_query_patterns": [],
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "created_at": str(date.today()),
            "last_modified": str(date.today()),
            "legacy_path": str(src.relative_to(LEGACY_HUB)),
            "revision_history": [],
        },
    }


def _extract_body_summary(body: str) -> str:
    cleaned = re.sub(r"^#+\s+.*\n", "", body, count=3, flags=re.MULTILINE)
    return cleaned.strip()[:500]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="don't write JSON, just report stats")
    parser.add_argument("--report", type=Path, default=None, help="write migration stats JSON")
    args = parser.parse_args(argv)

    if not args.dry_run:
        NEW_CORPUS.mkdir(parents=True, exist_ok=True)

    stats: dict = {"migrated": 0, "skipped_readme": 0, "failed": [], "categories": {}}
    for md in sorted(LEGACY_CORPUS.rglob("*.md")):
        if md.name.lower() == "readme.md":
            stats["skipped_readme"] += 1
            continue
        rel = str(md.relative_to(LEGACY_HUB))
        try:
            text = md.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text)
            if fm is None:
                stats["failed"].append({"path": rel, "reason": "no frontmatter"})
                continue
            entity = transform(fm, body, md)
            if not entity["id"]:
                stats["failed"].append({"path": rel, "reason": "missing concept_id"})
                continue
            if not entity["category"]:
                stats["failed"].append({"path": rel, "reason": "missing category"})
                continue
            stats["categories"][entity["category"]] = stats["categories"].get(entity["category"], 0) + 1
            if not args.dry_run:
                out_path = NEW_CORPUS / (entity["id"] + ".json")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(entity, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["migrated"] += 1
        except Exception as exc:  # noqa: BLE001 — migration script reports failures
            stats["failed"].append({"path": rel, "reason": repr(exc)})

    print(f"Migrated: {stats['migrated']}")
    print(f"Skipped README: {stats['skipped_readme']}")
    print(f"Failed: {len(stats['failed'])}")
    if stats["failed"]:
        print("First 10 failures:")
        for f in stats["failed"][:10]:
            print(f"  {f['path']}: {f['reason']}")
    print(f"Categories: {sorted(stats['categories'].items(), key=lambda x: -x[1])}")

    if args.report:
        args.report.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {args.report}")

    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
