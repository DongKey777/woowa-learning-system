"""Phase T3: learn-test — parse JUnit XML test results → test_result events.

stdlib xml.etree only — no lxml/pytest dependency.

Idempotent: event_id = "test_result-{sha256(xml_path|classname|name)[:12]}"
so re-ingesting same XML directory produces no duplicate events.

Failed tests bump linked concepts into uncertain_concepts via mission/extract
reverse mapping (test class → tested class → concept).

Public API:
  ingest_junit_dir(xml_dir, repo, state_root) -> IngestReport
"""
from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.feedback import record_turn
from core.state import DEFAULT_STATE_ROOT, append_history_event


@dataclass(frozen=True)
class TestCaseResult:
    classname: str
    name: str
    time_s: float
    status: str            # passed | failed | skipped
    failure_message: str | None
    xml_path: str


@dataclass
class IngestReport:
    xml_dir: str
    files_scanned: int = 0
    cases_total: int = 0
    cases_passed: int = 0
    cases_failed: int = 0
    cases_skipped: int = 0
    events_appended: int = 0
    events_skipped_dup: int = 0
    inferred_uncertain_concepts: list[str] = field(default_factory=list)


def _parse_xml(path: Path) -> list[TestCaseResult]:
    try:
        tree = ET.parse(str(path))
    except ET.ParseError:
        return []
    root = tree.getroot()
    # JUnit XML: <testsuite>(<testcase>...)+ or <testsuites>(<testsuite>...)+
    suites = root.findall(".//testsuite") if root.tag == "testsuites" else [root]
    cases: list[TestCaseResult] = []
    for suite in suites:
        for tc in suite.findall("testcase"):
            classname = tc.get("classname", "")
            name = tc.get("name", "")
            try:
                time_s = float(tc.get("time", "0") or 0)
            except ValueError:
                time_s = 0.0
            failure = tc.find("failure")
            error = tc.find("error")
            skipped = tc.find("skipped")
            if failure is not None or error is not None:
                msg_node = failure if failure is not None else error
                status = "failed"
                failure_message = (msg_node.get("message") or msg_node.text or "")[:300]
            elif skipped is not None:
                status = "skipped"
                failure_message = None
            else:
                status = "passed"
                failure_message = None
            cases.append(TestCaseResult(
                classname=classname, name=name, time_s=time_s,
                status=status, failure_message=failure_message,
                xml_path=str(path),
            ))
    return cases


def _stable_event_id(case: TestCaseResult) -> str:
    h = hashlib.sha256(
        f"{case.xml_path}|{case.classname}|{case.name}".encode("utf-8")
    ).hexdigest()[:12]
    return f"test_result-{h}"


def _infer_uncertain_from_failed(classname: str) -> list[str]:
    """Reverse-map test class → tested class → concept_id via mission.extract.

    Lightweight heuristic: drop trailing 'Test'/'Tests' from classname,
    look up Spring annotations / methods that this name suggests.
    Conservative: return [] when no signal.
    """
    if not classname:
        return []
    base = classname.split(".")[-1]
    if base.endswith("Tests"):
        base = base[:-5]
    elif base.endswith("Test"):
        base = base[:-4]
    # Lightweight name → concept hint
    lower = base.lower()
    hints = []
    if "controller" in lower:
        hints.append("spring/mvc-controller-basics")
    if "service" in lower:
        hints.append("spring/ioc-di-basics")
    if "repository" in lower or "jdbc" in lower:
        hints.append("database/jdbc-jpa-mybatis-basics")
    if "transactional" in lower or "transaction" in lower:
        hints.append("spring/transactional-basics")
    return hints


def ingest_junit_dir(
    xml_dir: Path,
    repo: str | None = None,
    state_root: Path = DEFAULT_STATE_ROOT,
    learner_id: str = "default",
    mode: str = "learning",
    now: float | None = None,
) -> IngestReport:
    """Walk xml_dir for *.xml, parse each, append events.

    Idempotency: tracks seen event_ids per call (per-run dedup) AND skips
    events whose stable id already exists in last 200 history.jsonl tail.
    """
    ts = now or time.time()
    report = IngestReport(xml_dir=str(xml_dir))
    if not xml_dir.exists() or not xml_dir.is_dir():
        return report

    # Build dedup set from recent history (tail=200 covers typical day)
    from core.state import read_history
    recent = read_history(state_root=state_root, tail=200)
    recent_ids = {e.get("event_id") for e in recent
                  if e.get("event_type") == "test_result"}

    inferred_uncertain: set[str] = set()
    xml_files = sorted(xml_dir.rglob("*.xml"))
    for xml_path in xml_files:
        report.files_scanned += 1
        cases = _parse_xml(xml_path)
        for case in cases:
            report.cases_total += 1
            # Concept inference per failed case is computed once and reused below
            # (it was called twice per failed case: the running set + the payload).
            inferred = (_infer_uncertain_from_failed(case.classname)
                        if case.status == "failed" else [])
            if case.status == "passed":
                report.cases_passed += 1
            elif case.status == "failed":
                report.cases_failed += 1
                inferred_uncertain.update(inferred)
            else:
                report.cases_skipped += 1

            event_id = _stable_event_id(case)
            if event_id in recent_ids:
                report.events_skipped_dup += 1
                continue
            event = {
                "event_id": event_id,
                "event_type": "test_result",
                "mode": mode,
                "ts": ts,
                "learner_id": learner_id,
                "repo": repo,
                "payload": {
                    "classname": case.classname,
                    "name": case.name,
                    "time_s": case.time_s,
                    "status": case.status,
                    "failure_message": case.failure_message,
                    "xml_path": case.xml_path,
                    "concept_ids": inferred,
                },
            }
            append_history_event(event, state_root=state_root)
            if mode == "learning":
                record_turn(event, state_root=state_root)
            report.events_appended += 1
            recent_ids.add(event_id)  # within-run dedup

    report.inferred_uncertain_concepts = sorted(inferred_uncertain)
    return report
