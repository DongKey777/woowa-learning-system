#!/usr/bin/env python3
"""One-shot, reviewed mode backfill for production learner telemetry.

Some learning-mode turns are actually system-development turns mislabeled as
`learning` because `WOOWA_SESSION_MODE` was unset at ask time (default is
`learning`). This migration relabels a *session-confirmed* set of events to
`mode="development"` with provenance `mode_source="backfill_reclassified"`.

Two phases, decoupled on purpose:

  --scan
      Read-only. Surface candidate `rag_ask` (mode=="learning") events whose
      prompt matches a deterministic dev-ops pattern. Prints a JSON candidate
      list. NEVER mutates. This is the "reviewed one-time migration" gate — a
      human/session inspects the candidates (cross-checking transcripts where
      needed) before any apply. It is NOT an always-on classifier.

  --apply --ids-file IDS  (or --ids a,b,c)
      Backup-first mutation. For each confirmed source rag_ask event_id, flip
      mode→development + mode_source=backfill_reclassified on the *paired*
      rows joined by source_event_id:
        - history.jsonl rag_ask event (event_id in ids)
        - history.jsonl response_quality event (payload.source_event_id in ids)
        - response-quality.jsonl row (source_event_id in ids)
      Idempotent (rows already backfill_reclassified are skipped). Validates:
      line count preserved, every line still parses, and ONLY mode/mode_source
      differ from the original row. `--dry-run` previews the diff without
      writing.

Development/fixture state under other roots is intentionally not touched.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_ROOT = REPO_ROOT / "state"
RECLASSIFIED_SOURCE = "backfill_reclassified"
TARGET_MODE = "development"

# Deterministic dev-ops prompt signatures used ONLY to surface scan candidates.
# A match means "worth a human look", not "auto-reclassify".
DEVOPS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(gradle|gradlew)\b.*\bdaemon\b.*\bstop\b", re.I), "gradle-daemon-stop"),
    (re.compile(r"\brag-?daemon\b.*\b(stop|restart|start-bg|start_bg)\b", re.I), "rag-daemon-lifecycle"),
    (re.compile(r"\bcorpus-build\b", re.I), "corpus-build"),
    (re.compile(r"\bindex-(fetch|pack)\b", re.I), "index-fetch-pack"),
    (re.compile(r"\brag-eval\b", re.I), "rag-eval"),
    (re.compile(r"\bcohort-eval\b", re.I), "cohort-eval"),
    (re.compile(r"\brag-remote-build\b", re.I), "rag-remote-build"),
    (re.compile(r"\bpytest\b", re.I), "pytest"),
    (re.compile(r"\b(WOOWA_SESSION_MODE|core/|bin/|tests/|scripts/)"), "mode-b-path-token"),
]


def _history_path(state_root: Path) -> Path:
    return state_root / "learner" / "history.jsonl"


def _quality_path(state_root: Path) -> Path:
    return state_root / "learner" / "response-quality.jsonl"


def _join_id(row: dict[str, Any]) -> str | None:
    """The source rag_ask event_id this row should be matched against, or None
    if the row is out of scope for mode backfill."""
    et = row.get("event_type")
    if et == "rag_ask":
        return row.get("event_id")
    if et == "response_quality":
        return (row.get("payload") or {}).get("source_event_id")
    if et is None and "source_event_id" in row:  # response-quality.jsonl row
        return row.get("source_event_id")
    return None


def _match_patterns(prompt: str) -> list[str]:
    return [label for rx, label in DEVOPS_PATTERNS if rx.search(prompt)]


def scan(state_root: Path) -> dict[str, Any]:
    history = _history_path(state_root)
    candidates: list[dict[str, Any]] = []
    paired_rq_events: dict[str, int] = {}
    if not history.exists():
        return {"phase": "scan", "candidates": [], "n": 0,
                "history_exists": False}

    cand_ids: set[str] = set()
    with history.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event_type") != "rag_ask" or row.get("mode") != "learning":
                continue
            prompt = (row.get("payload") or {}).get("prompt") or ""
            matched = _match_patterns(prompt)
            if not matched:
                continue
            eid = row.get("event_id")
            cand_ids.add(eid)
            candidates.append({
                "event_id": eid,
                "ts": row.get("ts"),
                "repo": row.get("repo"),
                "matched_patterns": matched,
                "prompt": prompt,
            })

    # Count paired response_quality history events for visibility.
    with history.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event_type") != "response_quality":
                continue
            sid = (row.get("payload") or {}).get("source_event_id")
            if sid in cand_ids:
                paired_rq_events[sid] = paired_rq_events.get(sid, 0) + 1

    quality = _quality_path(state_root)
    paired_quality_rows = 0
    if quality.exists():
        with quality.open(encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("source_event_id") in cand_ids:
                    paired_quality_rows += 1

    return {
        "phase": "scan",
        "history_exists": True,
        "n": len(candidates),
        "paired_response_quality_events": sum(paired_rq_events.values()),
        "paired_quality_rows": paired_quality_rows,
        "candidates": candidates,
    }


def _changed_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    keys = set(before) | set(after)
    return {k for k in keys if before.get(k) != after.get(k)}


def _apply_to_file(
    path: Path,
    ids: set[str],
    *,
    dry_run: bool,
    backup_root: Path,
    state_root: Path,
) -> dict[str, Any]:
    stats = {
        "path": str(path),
        "exists": path.exists(),
        "lines_total": 0,
        "flipped": 0,
        "already_reclassified": 0,
        "backup_path": None,
        "samples": [],
    }
    if not path.exists():
        return stats

    out_lines: list[str] = []
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                # Preserve blank lines verbatim (keeps line count stable).
                out_lines.append(line)
                continue
            stats["lines_total"] += 1
            row = json.loads(line)  # malformed line is a hard error — surface it
            jid = _join_id(row)
            if jid not in ids:
                out_lines.append(line)
                continue
            if row.get("mode_source") == RECLASSIFIED_SOURCE:
                stats["already_reclassified"] += 1
                out_lines.append(line)
                continue
            before = dict(row)
            row["mode"] = TARGET_MODE
            row["mode_source"] = RECLASSIFIED_SOURCE
            changed = _changed_keys(before, row)
            if changed - {"mode", "mode_source"}:
                raise AssertionError(
                    f"backfill would change unexpected keys {sorted(changed)} "
                    f"on join_id={jid} in {path.name}"
                )
            stats["flipped"] += 1
            if len(stats["samples"]) < 5:
                stats["samples"].append({
                    "join_id": jid,
                    "from_mode": before.get("mode"),
                    "from_mode_source": before.get("mode_source"),
                })
            out_lines.append(json.dumps(row, ensure_ascii=False))

    # Validation: produced exactly as many physical lines as we consumed.
    original_line_count = sum(1 for _ in path.open(encoding="utf-8"))
    if len(out_lines) != original_line_count:
        raise AssertionError(
            f"line count drift in {path.name}: "
            f"{original_line_count} -> {len(out_lines)}"
        )

    if not dry_run and stats["flipped"]:
        relative = path.relative_to(state_root)
        backup_path = backup_root / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_path)
        stats["backup_path"] = str(backup_path)
        path.write_text(
            "\n".join(out_lines) + ("\n" if out_lines else ""),
            encoding="utf-8",
        )
    return stats


def apply(state_root: Path, ids: set[str], *, dry_run: bool) -> dict[str, Any]:
    backup_root = (
        state_root / "_backups" / f"mode-reclassify-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    files = [_history_path(state_root), _quality_path(state_root)]
    results = [
        _apply_to_file(
            p, ids, dry_run=dry_run, backup_root=backup_root, state_root=state_root
        )
        for p in files
    ]
    flipped_total = sum(int(r["flipped"]) for r in results)
    # Relabeling learning->development in history.jsonl leaves the mastery graph
    # inconsistent: the now-dev events already wrote history-derived evidence
    # (mission_use) into mastery_graph.sqlite while they were labeled learning.
    # Reconcile rebuilds that evidence from the CURRENT labels (preserving the
    # PR-sync pr_merge/mentor_accept evidence that no replay can reproduce), so
    # the migration leaves no orphaned mastery evidence behind.
    mastery_reconcile: dict[str, Any] | None = None
    if not dry_run and flipped_total:
        from core.feedback import reconcile_mastery_with_history_labels

        mastery_reconcile = reconcile_mastery_with_history_labels(state_root=state_root)
    return {
        "phase": "apply",
        "dry_run": dry_run,
        "requested_ids": len(ids),
        "flipped_total": flipped_total,
        "backup_root": (
            None if dry_run or flipped_total == 0 else str(backup_root)
        ),
        "files": results,
        "mastery_reconcile": mastery_reconcile,
    }


def _load_ids(args: argparse.Namespace) -> set[str]:
    ids: set[str] = set()
    if args.ids:
        ids |= {x.strip() for x in args.ids.split(",") if x.strip()}
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            tok = line.strip()
            if tok and not tok.startswith("#"):
                ids.add(tok)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--scan", action="store_true",
                        help="read-only candidate detection")
    parser.add_argument("--apply", action="store_true",
                        help="mutate confirmed --ids (backup-first)")
    parser.add_argument("--ids", help="comma-separated confirmed source event_ids")
    parser.add_argument("--ids-file", help="file with one confirmed event_id per line")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --apply: preview without writing")
    args = parser.parse_args(argv)

    state_root = args.state_root.resolve()
    if args.scan == args.apply:  # both or neither
        parser.error("choose exactly one of --scan or --apply")

    if args.scan:
        print(json.dumps(scan(state_root), ensure_ascii=False, indent=2))
        return 0

    ids = _load_ids(args)
    if not ids:
        parser.error("--apply requires --ids or --ids-file")
    print(json.dumps(apply(state_root, ids, dry_run=args.dry_run),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
