"""F2 profile unified-projection rebuild benchmark.

Verifies that profile.json remains source-of-truth while
state/learner/unified_profile.json can be rebuilt deterministically from
profile + history, with legacy projection parity on the shared cs_view shape.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_ROOT = REPO_ROOT.parent / "woowa-learning-hub"
REPORT = REPO_ROOT / "reports" / "profile_rebuild_projection.json"

sys.path.insert(0, str(REPO_ROOT))

from core.profile import (  # noqa: E402
    compute_cs_view,
    rebuild_unified_projection,
    recompute_profile,
)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[idx]


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 3) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 3) if values else None,
        "max_ms": round(max(values), 3) if values else None,
    }


def _legacy_drill_event() -> dict:
    return {
        "event_id": "legacy-drill-1",
        "event_type": "drill_answer",
        "mode": "learning",
        "ts": "2026-05-07T11:26:18+00:00",
        "scored_at": "2026-05-07T11:26:18+00:00",
        "concept_ids": ["spring/bean"],
        "total_score": 3,
        "dimensions": {
            "accuracy": 1,
            "depth": 1,
            "practicality": 0,
            "completeness": 1,
        },
        "weak_tags": ["정확도"],
        "source_doc": {"category": "spring"},
    }


def _seed_state(root: Path, *, events_n: int = 1000) -> None:
    learner = root / "learner"
    learner.mkdir(parents=True, exist_ok=True)
    (learner / "profile.json").write_text(json.dumps({
        "schema_version": "v3",
        "learner_id": "default",
        "concepts": {
            "mastered": [],
            "proficient": [],
            "uncertain": [],
            "underexplored": [],
        },
        "activity": {"events_total": 0},
        "pending_triggers": {"self_assessment": {"id": "s1"}},
        "drill_due": [{"concept_id": "spring/bean"}],
        "manual_overrides": {"uncertain": {"value": "spring/bean"}},
    }), encoding="utf-8")
    db = learner / "mastery_graph.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE mastery (concept_id TEXT PRIMARY KEY, bloom_level TEXT, "
        "evidence_count INTEGER DEFAULT 0, last_seen_at REAL DEFAULT 0, "
        "promotion_trace TEXT DEFAULT '[]')"
    )
    conn.executemany(
        "INSERT INTO mastery(concept_id, bloom_level, evidence_count, last_seen_at) "
        "VALUES (?, ?, ?, ?)",
        [
            ("spring/di", "mastered", 3, time.time()),
            ("database/tx", "proficient", 2, time.time()),
        ],
    )
    conn.commit()
    conn.close()

    rows: list[dict] = []
    base = time.time() - events_n
    for i in range(events_n - 2):
        rows.append({
            "event_id": f"ask-{i}",
            "event_type": "rag_ask",
            "mode": "learning" if i % 17 else "development",
            "ts": base + i,
            "payload": {
                "router_mode": "cs_qa",
                "top_concept_ids": ["spring/bean"] if i % 3 else ["database/lock"],
            },
        })
    rows.append(_legacy_drill_event())
    rows.append({
        "event_id": "v2-drill-1",
        "event_type": "drill_answer",
        "mode": "learning",
        "ts": base + events_n,
        "payload": {
            "concept_ids": ["database/tx"],
            "score": 0.8,
            "level": "mastered",
            "dimensions": {
                "accuracy": 8,
                "depth": 7,
                "practicality": 6,
                "completeness": 6,
            },
        },
    })
    (learner / "history.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _legacy_compare() -> dict[str, Any]:
    module = LEGACY_ROOT / "scripts" / "learning" / "profile_merge.py"
    if not module.exists():
        return {"available": False, "pass": False}
    event = _legacy_drill_event()
    code = f"""
import json, sys
sys.path.insert(0, {str(LEGACY_ROOT)!r})
from scripts.learning import profile_merge
history = [{event!r}]
view = profile_merge.compute_cs_view(history)
print(json.dumps(view, ensure_ascii=False))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=LEGACY_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return {
            "available": True,
            "rc": proc.returncode,
            "stderr": proc.stderr[-500:],
            "pass": False,
        }
    legacy = json.loads(proc.stdout)
    current = compute_cs_view([event])
    def _comparable(view: dict | None) -> dict | None:
        if view is None:
            return None
        return {
            "avg_score": view.get("avg_score"),
            "level": view.get("level"),
            "weak_dimensions": view.get("weak_dimensions"),
            "weak_tags": view.get("weak_tags"),
            "low_categories": view.get("low_categories"),
            "recent_drills": [
                {
                    "scored_at": row.get("scored_at"),
                    "total_score": row.get("total_score"),
                    "level": row.get("level"),
                    "weak_tags": row.get("weak_tags"),
                }
                for row in (view.get("recent_drills") or [])
            ],
        }
    return {
        "available": True,
        "legacy": _comparable(legacy),
        "current": _comparable(current),
        "pass": _comparable(legacy) == _comparable(current),
    }


def _measure_direct(root: Path, iterations: int = 200) -> dict[str, Any]:
    profile = recompute_profile(state_root=root)
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        rebuild_unified_projection(state_root=root, profile=profile)
        samples.append((time.perf_counter() - t0) * 1000)
    return _summary(samples)


def _measure_cli(root: Path, iterations: int = 10) -> dict[str, Any]:
    samples: list[float] = []
    errors: list[str] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        proc = subprocess.run(
            [
                sys.executable,
                "bin/profile-recompute",
                "--state-root",
                str(root),
                "--rebuild-unified-projection",
                "--silent",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        samples.append((time.perf_counter() - t0) * 1000)
        if proc.returncode != 0:
            errors.append(proc.stderr[-300:] or f"rc={proc.returncode}")
    out = _summary(samples)
    out["errors"] = errors
    return out


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="woowa-profile-rebuild-") as d:
        root = Path(d)
        _seed_state(root)
        direct = _measure_direct(root)
        cli = _measure_cli(root)
        profile = json.loads((root / "learner" / "profile.json").read_text(encoding="utf-8"))
        projection = json.loads(
            (root / "learner" / "unified_profile.json").read_text(encoding="utf-8")
        )
        legacy = _legacy_compare()
        checks = {
            "profile_schema_preserved": (
                profile.get("schema_version") == "v3"
                and "manual_overrides" in profile
                and "pending_triggers" in profile
                and "drill_due" in profile
            ),
            "projection_written": projection.get("schema_version") == "unified_v1",
            "projection_has_skip_context": bool(
                (projection.get("coach_view") or {}).get("must_skip_explanations_of")
            ),
            "legacy_cs_view_same": legacy.get("pass") is True,
            "direct_rebuild_p95_budget": (
                direct.get("p95_ms") is not None and direct["p95_ms"] <= 10_000.0
            ),
            "cli_rebuild_p95_budget": (
                cli.get("p95_ms") is not None and cli["p95_ms"] <= 30_000.0
            ),
        }
        report = {
            "benchmark": "profile_rebuild_projection",
            "events_n": 1000,
            "direct_latency_ms": direct,
            "cli_latency_ms": cli,
            "legacy_compare": legacy,
            "projection_summary": {
                "priority_focus_n": len(
                    (projection.get("reconciled") or {}).get("priority_focus") or []
                ),
                "cs_view_present": projection.get("cs_view") is not None,
                "skip_context_n": len(
                    (projection.get("coach_view") or {}).get("must_skip_explanations_of") or []
                ),
            },
            "checks": checks,
            "pass": all(checks.values()) and not cli.get("errors"),
        }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "pass": report["pass"],
        "checks": checks,
        "direct_latency_ms": direct,
        "cli_latency_ms": cli,
        "report": str(REPORT.relative_to(REPO_ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
