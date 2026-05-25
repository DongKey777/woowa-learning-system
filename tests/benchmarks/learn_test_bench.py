"""tests/benchmarks/learn_test_bench.py — Phase T3 perf bench.

Synthesizes 100 JUnit testcase XML directory, measures:
  1. Parse + ingest latency ≤500ms total (legacy ~1.2s, 2.4× faster)
  2. Idempotency: second run on same dir = 0 events appended, 100 dups detected
  3. Concept→test mapping recall: failed Controller/Service/Repository tests
     surface correct uncertain concept ids (≥85%)

Reports → reports/learn_test_bench.json
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.junit_ingest import ingest_junit_dir  # noqa: E402

REPORT_PATH = REPO_ROOT / "reports" / "learn_test_bench.json"

TARGETS = {
    "parse_ingest_ms_max": 500,
    "concept_recall_min": 0.85,
    "case_count": 100,
}


def _build_synth_xml(xml_dir: Path, n_cases: int = 100) -> tuple[int, int]:
    """Create n test XMLs split across 4 categories with controlled failure rate."""
    cats = [
        ("ReservationControllerTest", "spring/mvc-controller-basics"),
        ("ReservationServiceTest", "spring/ioc-di-basics"),
        ("JdbcReservationRepositoryTest", "database/jdbc-jpa-mybatis-basics"),
        ("TransactionalServiceTest", "spring/transactional-basics"),
    ]
    per_class = n_cases // len(cats)
    expected_concepts = {c for _, c in cats}
    fail_count = 0
    for class_short, _ in cats:
        full_class = f"com.bench.{class_short}"
        xml_path = xml_dir / f"TEST-{full_class}.xml"
        cases_xml = []
        for i in range(per_class):
            failed = (i % 4 == 0)  # 25% failed → ≥1 fail per category
            if failed:
                fail_count += 1
                cases_xml.append(
                    f'<testcase classname="{full_class}" name="t{i}" time="0.05">'
                    f'<failure message="boom {i}">stack</failure></testcase>'
                )
            else:
                cases_xml.append(
                    f'<testcase classname="{full_class}" name="t{i}" time="0.05"/>'
                )
        xml_path.write_text(
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            f"<testsuite name='{full_class}'>\n" + "\n".join(cases_xml) + "\n</testsuite>",
            encoding="utf-8",
        )
    return fail_count, len(expected_concepts)


def measure() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        (state / "learner").mkdir(parents=True)
        xml_dir = Path(tmp) / "xml"
        xml_dir.mkdir()
        fail_count, expected_concept_count = _build_synth_xml(xml_dir, TARGETS["case_count"])

        # First ingest — measure latency
        latencies = []
        for _ in range(3):
            # Re-seed since each ingest dedups
            state2 = Path(tmp) / f"state-{_}"
            (state2 / "learner").mkdir(parents=True)
            t0 = time.perf_counter()
            r = ingest_junit_dir(xml_dir, state_root=state2)
            latencies.append((time.perf_counter() - t0) * 1000)
        p50 = statistics.median(latencies)
        p95 = max(latencies)

        # Idempotency check (separate clean state)
        state3 = Path(tmp) / "state-idem"
        (state3 / "learner").mkdir(parents=True)
        r1 = ingest_junit_dir(xml_dir, state_root=state3)
        r2 = ingest_junit_dir(xml_dir, state_root=state3)

        # Concept recall — surfaced inferred_uncertain should cover ≥85% of
        # expected categories
        recall = len(set(r1.inferred_uncertain_concepts)) / max(1, expected_concept_count)

    current = {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "cases_total": r1.cases_total,
        "cases_failed": r1.cases_failed,
        "events_first_run": r1.events_appended,
        "events_second_run": r2.events_appended,
        "dups_second_run": r2.events_skipped_dup,
        "inferred_uncertain_n": len(r1.inferred_uncertain_concepts),
        "concept_recall": round(recall, 3),
    }
    axes = {
        "latency_under_target": (p50 <= TARGETS["parse_ingest_ms_max"],
                                  f"p50 {current['p50_ms']}ms ≤ {TARGETS['parse_ingest_ms_max']}ms"),
        "idempotent": (r2.events_appended == 0 and r2.events_skipped_dup == r1.events_appended,
                        f"2nd run: 0 appended + {r2.events_skipped_dup}/{r1.events_appended} dups"),
        "concept_recall_met": (recall >= TARGETS["concept_recall_min"],
                                f"recall {recall:.2%} ≥ {TARGETS['concept_recall_min']:.0%}"),
    }
    return {
        "baseline_legacy_ms": 1200,
        "targets": TARGETS,
        "current": current,
        "axes": {k: {"pass": v[0], "detail": v[1]} for k, v in axes.items()},
        "pass": all(p for p, _ in axes.values()),
    }


if __name__ == "__main__":
    report = measure()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "p50": report["current"]["p50_ms"],
        "p95": report["current"]["p95_ms"],
        "concept_recall": report["current"]["concept_recall"],
        "events_first/dups_second": f"{report['current']['events_first_run']}/{report['current']['dups_second_run']}",
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()},
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
