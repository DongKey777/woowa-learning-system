"""tests/benchmarks/learn_response_quality_bench.py — Phase T4 perf bench.

Measures:
  1. Append p95 ≤ 30ms (target — JSONL append + redaction is O(N) body length)
  2. Citation drift detection precision: on 20 labeled (expected, declared)
     fixtures, drift flag matches expected drift type ≥90%
  3. PII redaction recall on 15 PII strings = 100%
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

from core.response_quality import (  # noqa: E402
    detect_citation_drift, record_response_quality, redact_pii,
)

REPORT_PATH = REPO_ROOT / "reports" / "learn_response_quality_bench.json"

TARGETS = {
    "p95_ms_max": 30,
    "drift_precision_min": 0.90,
    "pii_recall_min": 1.0,
}

# Synthetic citation drift fixtures
DRIFT_FIXTURES = [
    # (expected, declared, expected_flags)
    ([], [], set()),
    (["a.md"], ["a.md"], set()),
    (["a.md"], [], {"missing_citation"}),
    (["a.md", "b.md"], ["a.md"], {"citation_mismatch"}),
    (["a.md"], ["a.md", "b.md"], {"extra_citation"}),
    (["a.md", "b.md"], ["a.md", "b.md"], set()),
    (["spring/x.md"], ["spring/y.md"], {"extra_citation"}),  # disjoint → extra dominates
    (["a.md", "b.md"], ["b.md"], {"citation_mismatch"}),
    ([], ["a.md"], {"extra_citation"}),
    (["a.md", "b.md", "c.md"], ["a.md"], {"citation_mismatch"}),
    (["x.md"], ["x.md", "y.md", "z.md"], {"extra_citation"}),
    (["a.md", "b.md"], [], {"missing_citation"}),
    (["a"], ["a"], set()),
    (["a", "b", "c"], ["a", "b", "c"], set()),
    (["a", "b"], ["a"], {"citation_mismatch"}),
    (["spring/bean.md"], ["spring/bean.md"], set()),
    (["database/jdbc.md"], ["network/http.md"], {"extra_citation"}),
    ([], ["x.md", "y.md"], {"extra_citation"}),
    (["a.md"], ["a.md"], set()),
    (["one.md", "two.md", "three.md"], ["one.md", "two.md"], {"citation_mismatch"}),
]

# PII fixtures
PII_FIXTURES = [
    ("foo@example.com 답변", "[EMAIL]"),
    ("Authorization: Bearer abc123def456ghi789jkl", "[BEARER_TOKEN]"),
    ("Token: xyz789abc456def123", "[BEARER_TOKEN]"),
    ("api_key=apikey_FAKE_abc123def456ghi789jklmno", "[API_KEY]"),
    ("github gho_abc123def456ghi789jkl", "[API_KEY]"),
    ("Heart-beat ghp_abc123def456ghi789jklmnop", "[API_KEY]"),
    ("Pre-PAT github_pat_abc123def456ghi789jkl", "[API_KEY]"),
    ("amazon AKIA1234567890ABCDEF", "[API_KEY]"),
    ("gcp AIzaSyAbcdefghijklmnopqrstuvwx", "[API_KEY]"),
    ("연락: 010-1234-5678", "[PHONE]"),
    ("+82-2-1234-5678 전화", "[PHONE]"),
    ("admin@corp.org 메모", "[EMAIL]"),
    ("Authorization Bearer x_y_z_w_q_r_s_t_u_v", "[BEARER_TOKEN]"),
    ("test apikey_FAKE_abcdefghijklmnopqrstuvwxyz", "[API_KEY]"),
    ("이메일 user.name+tag@subdomain.co.kr 입니다", "[EMAIL]"),
]


def measure() -> dict:
    # 1. latency
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        (state / "learner").mkdir(parents=True)
        body = "Spring Bean DI는 IoC 컨테이너가 객체 생명주기를 관리하는 패턴이야. " * 30
        latencies = []
        for i in range(50):
            t0 = time.perf_counter()
            record_response_quality(
                source_event_id=f"ev-{i}", response_summary="bench",
                response_body=body,
                expected_citation_paths=["a.md"],
                declared_citation_paths=["a.md"],
                state_root=state,
            )
            latencies.append((time.perf_counter() - t0) * 1000)
        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]

    # 2. citation drift precision
    drift_hits = 0
    for exp, dec, expected_flags in DRIFT_FIXTURES:
        actual = detect_citation_drift(exp, dec)
        if actual == expected_flags:
            drift_hits += 1
    drift_precision = drift_hits / len(DRIFT_FIXTURES)

    # 3. PII recall
    pii_hits = 0
    for raw, expected_token in PII_FIXTURES:
        redacted = redact_pii(raw)
        if expected_token in redacted:
            pii_hits += 1
    pii_recall = pii_hits / len(PII_FIXTURES)

    current = {
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "n_calls": 50,
        "drift_fixtures_n": len(DRIFT_FIXTURES),
        "drift_correct": drift_hits,
        "drift_precision": round(drift_precision, 3),
        "pii_fixtures_n": len(PII_FIXTURES),
        "pii_recall": round(pii_recall, 3),
    }
    axes = {
        "append_latency_under_target": (p95 <= TARGETS["p95_ms_max"],
                                          f"p95 {current['p95_ms']}ms ≤ {TARGETS['p95_ms_max']}ms"),
        "drift_precision_met": (drift_precision >= TARGETS["drift_precision_min"],
                                 f"{drift_precision:.2%} ≥ {TARGETS['drift_precision_min']:.0%}"),
        "pii_recall_met": (pii_recall >= TARGETS["pii_recall_min"],
                            f"{pii_recall:.2%} ≥ {TARGETS['pii_recall_min']:.0%}"),
    }
    return {
        "baseline_legacy_ms": "n/a (legacy didn't measure)",
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
        "p95": report["current"]["p95_ms"],
        "drift_precision": report["current"]["drift_precision"],
        "pii_recall": report["current"]["pii_recall"],
        "pass": report["pass"],
        "axes": {k: v["pass"] for k, v in report["axes"].items()},
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
