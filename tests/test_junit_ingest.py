"""Unit tests for core.junit_ingest — Phase T3."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.junit_ingest import (  # noqa: E402
    _infer_uncertain_from_failed, _parse_xml, _stable_event_id,
    ingest_junit_dir,
)


def _seed_xml(tmp_path: Path) -> Path:
    """3 testcases: 1 passed + 1 failed + 1 skipped."""
    p = tmp_path / "TEST-com.foo.BarTest.xml"
    p.write_text("""<?xml version='1.0' encoding='UTF-8'?>
<testsuite name="com.foo.BarTest" tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="com.foo.BarTest" name="shouldPassA" time="0.01"/>
  <testcase classname="com.foo.BarTest" name="shouldFailB" time="0.02">
    <failure message="expected X but Y" type="AssertionError">java.lang.AssertionError: at line 42</failure>
  </testcase>
  <testcase classname="com.foo.BarTest" name="shouldSkipC" time="0">
    <skipped/>
  </testcase>
</testsuite>
""", encoding="utf-8")
    return p


def test_parse_xml_3_cases(tmp_path: Path) -> None:
    xml = _seed_xml(tmp_path)
    cases = _parse_xml(xml)
    assert len(cases) == 3
    statuses = sorted(c.status for c in cases)
    assert statuses == ["failed", "passed", "skipped"]


def test_parse_xml_failure_message_extracted(tmp_path: Path) -> None:
    xml = _seed_xml(tmp_path)
    cases = _parse_xml(xml)
    failed = [c for c in cases if c.status == "failed"][0]
    assert failed.failure_message == "expected X but Y"
    assert failed.classname == "com.foo.BarTest"
    assert failed.name == "shouldFailB"


def test_parse_xml_malformed_returns_empty(tmp_path: Path) -> None:
    bad = tmp_path / "BAD.xml"
    bad.write_text("<not really xml", encoding="utf-8")
    assert _parse_xml(bad) == []


def test_stable_event_id_deterministic(tmp_path: Path) -> None:
    xml = _seed_xml(tmp_path)
    cases1 = _parse_xml(xml)
    cases2 = _parse_xml(xml)
    ids1 = [_stable_event_id(c) for c in cases1]
    ids2 = [_stable_event_id(c) for c in cases2]
    assert ids1 == ids2
    # Distinct cases → distinct ids
    assert len(set(ids1)) == 3


def test_infer_uncertain_from_failed_controller() -> None:
    h = _infer_uncertain_from_failed("com.x.FooControllerTest")
    assert "spring/mvc-controller-basics" in h


def test_infer_uncertain_from_failed_repository() -> None:
    h = _infer_uncertain_from_failed("com.x.JdbcUserRepositoryTest")
    assert "database/jdbc-jpa-mybatis-basics" in h


def test_infer_uncertain_from_failed_empty_returns_empty() -> None:
    assert _infer_uncertain_from_failed("") == []


def test_ingest_appends_3_events(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "learner").mkdir(parents=True)
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    _seed_xml(xml_dir)

    report = ingest_junit_dir(xml_dir, repo="demo", state_root=state)
    assert report.files_scanned == 1
    assert report.cases_total == 3
    assert report.cases_passed == 1
    assert report.cases_failed == 1
    assert report.cases_skipped == 1
    assert report.events_appended == 3
    assert report.events_skipped_dup == 0

    lines = [json.loads(l) for l in (state / "learner" / "history.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3
    assert all(l["event_type"] == "test_result" for l in lines)


def test_ingest_idempotent_re_run(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "learner").mkdir(parents=True)
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    _seed_xml(xml_dir)

    r1 = ingest_junit_dir(xml_dir, state_root=state)
    r2 = ingest_junit_dir(xml_dir, state_root=state)
    assert r1.events_appended == 3
    assert r2.events_appended == 0
    assert r2.events_skipped_dup == 3


def test_ingest_missing_dir_returns_empty(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "learner").mkdir(parents=True)
    report = ingest_junit_dir(tmp_path / "nope", state_root=state)
    assert report.files_scanned == 0
    assert report.cases_total == 0


def test_ingest_failed_test_adds_inferred_concepts(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "learner").mkdir(parents=True)
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    xml = xml_dir / "TEST-com.foo.ReservationControllerTest.xml"
    xml.write_text("""<?xml version='1.0' encoding='UTF-8'?>
<testsuite>
  <testcase classname="com.foo.ReservationControllerTest" name="shouldFail" time="0.1">
    <failure message="boom">stack</failure>
  </testcase>
</testsuite>""", encoding="utf-8")
    report = ingest_junit_dir(xml_dir, state_root=state)
    assert "spring/mvc-controller-basics" in report.inferred_uncertain_concepts
