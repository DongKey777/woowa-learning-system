#!/usr/bin/env python3
"""Canonicalize production learner telemetry ids.

This is a one-shot, backup-first migration for the production learner stream:

  state/learner/history.jsonl
  state/learner/response-quality.jsonl
  state/learner/feedback.jsonl
  state/cs_rag/feedback.jsonl  # previous feedback path, if still present

Development and fixture splits are intentionally not touched.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_ROOT = REPO_ROOT / "state"
DEFAULT_CANONICAL = "DongKey777"
LEGACY_VALUES = {"higgs95@naver.com", "higgs95", "default", None, ""}
NON_PRODUCTION_MODES = {"development", "test"}


def _targets(state_root: Path) -> list[Path]:
    return [
        state_root / "learner" / "history.jsonl",
        state_root / "learner" / "response-quality.jsonl",
        state_root / "learner" / "feedback.jsonl",
        state_root / "cs_rag" / "feedback.jsonl",
    ]


def _source_label(value: Any) -> str:
    if value is None:
        return "missing"
    if value == "":
        return "empty"
    return str(value)


def _rewrite_line(line: str, *, canonical: str) -> tuple[str, bool, bool, bool]:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return line, False, False, False
    original = row.get("learner_id")
    if original == canonical:
        return json.dumps(row, ensure_ascii=False), False, False, False
    if original in LEGACY_VALUES:
        row["learner_id"] = canonical
        row.setdefault("_canonical_id_source", _source_label(original))
        if row.get("_backfilled_from"):
            row.setdefault("_provisional", True)
        return json.dumps(row, ensure_ascii=False), True, False, False
    if row.get("mode") in NON_PRODUCTION_MODES:
        return json.dumps(row, ensure_ascii=False), False, False, True
    row["_canonical_id_skipped"] = _source_label(original)
    return json.dumps(row, ensure_ascii=False), False, True, False


def _backup_file(path: Path, backup_root: Path, state_root: Path) -> Path:
    relative = path.relative_to(state_root)
    backup_path = backup_root / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return backup_path


def process_file(
    path: Path,
    *,
    state_root: Path,
    backup_root: Path,
    canonical: str,
    dry_run: bool,
) -> dict:
    stats = {
        "path": str(path),
        "exists": path.exists(),
        "lines_total": 0,
        "changed_n": 0,
        "skipped_unknown_n": 0,
        "skipped_non_production_n": 0,
        "backup_path": None,
    }
    if not path.exists():
        return stats

    rewritten: list[str] = []
    with path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if not line.strip():
                continue
            stats["lines_total"] += 1
            new_line, changed, skipped, skipped_non_prod = _rewrite_line(line, canonical=canonical)
            stats["changed_n"] += int(changed)
            stats["skipped_unknown_n"] += int(skipped)
            stats["skipped_non_production_n"] += int(skipped_non_prod)
            rewritten.append(new_line)

    if not dry_run and (stats["changed_n"] or stats["skipped_unknown_n"]):
        backup_path = _backup_file(path, backup_root, state_root)
        stats["backup_path"] = str(backup_path)
        path.write_text(
            "\n".join(rewritten) + ("\n" if rewritten else ""),
            encoding="utf-8",
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--canonical", default=DEFAULT_CANONICAL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    state_root = args.state_root.resolve()
    backup_root = (
        state_root / "_backups" / f"canonical-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    results = [
        process_file(
            path,
            state_root=state_root,
            backup_root=backup_root,
            canonical=args.canonical,
            dry_run=args.dry_run,
        )
        for path in _targets(state_root)
    ]
    changed_total = sum(int(r["changed_n"]) for r in results)
    skipped_total = sum(int(r["skipped_unknown_n"]) for r in results)
    print(json.dumps({
        "canonical": args.canonical,
        "dry_run": args.dry_run,
        "backup_root": None if args.dry_run or changed_total == 0 else str(backup_root),
        "changed_total": changed_total,
        "skipped_unknown_total": skipped_total,
        "files": results,
    }, ensure_ascii=False, indent=2))
    return 0 if skipped_total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
