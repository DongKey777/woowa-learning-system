"""Y13-F auto reformulation path benchmark.

Measures the safe follow-up rewrite path that replaces legacy anaphora
handling without broad lexical guessing.
"""
from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_ROOT = REPO_ROOT.parent / "woowa-learning-hub"
REPORT = REPO_ROOT / "reports" / "auto_reformulation_path.json"
PROMPT = "그게 뭐야"
LEGACY_PROMPT = "그건 뭐야"
PRIOR_CONCEPT = "spring/bean-di-basics"

sys.path.insert(0, str(REPO_ROOT))

from core.daemon import search as daemon_search  # noqa: E402
from core.reformulate import auto_reformulate  # noqa: E402
from core.router import route  # noqa: E402
from rag.corpus_loader import load_corpus_runtime  # noqa: E402


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[idx]


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 4) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 4) if values else None,
        "max_ms": round(max(values), 4) if values else None,
    }


def _ensure_daemon() -> None:
    subprocess.run(
        [
            "bin/rag-daemon",
            "start-bg",
            "--log-path",
            "/tmp/woowa-auto-reformulation-path.log",
            "--timeout-s",
            "90",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=95,
        check=True,
    )


def _history() -> list[dict]:
    return [{
        "event_id": "ask-prev",
        "event_type": "rag_ask",
        "mode": "learning",
        "ts": time.time() - 30,
        "payload": {
            "prompt": "Spring DI가 뭐야",
            "router_mode": "cs_qa",
            "top_concept_ids": [PRIOR_CONCEPT],
        },
    }]


def _measure_decision(iterations: int = 2000) -> tuple[dict[str, Any], str]:
    corpus = load_corpus_runtime()
    history = _history()
    samples: list[float] = []
    query = ""
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        result = auto_reformulate(prompt=PROMPT, history=history, corpus=corpus)
        samples.append((time.perf_counter_ns() - t0) / 1_000_000.0)
        if result is not None:
            query = result.reformulated_query
    return _summary(samples), query


def _measure_search(query: str, iterations: int = 10) -> dict[str, Any]:
    samples: list[float] = []
    top1: list[str | None] = []
    top5: list[list[str]] = []
    errors: list[str] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        try:
            hits = daemon_search(query, top_k=5) or []
            samples.append((time.perf_counter() - t0) * 1000)
            ids = [str(h.get("concept_id")) for h in hits if h.get("concept_id")]
            top1.append(ids[0] if ids else None)
            top5.append(ids[:5])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
    out = _summary(samples)
    out.update({
        "top1_values": sorted({v for v in top1 if v}),
        "top5_values": top5[:3],
        "errors": errors,
    })
    return out


def _legacy_compare() -> dict[str, Any]:
    module = LEGACY_ROOT / "scripts" / "learning" / "rag" / "r3" / "anaphora.py"
    if not module.exists():
        return {"available": False, "pass": False}
    code = f"""
import json, sys
sys.path.insert(0, {str(LEGACY_ROOT)!r})
from scripts.learning.rag.r3.anaphora import detect_follow_up
d = detect_follow_up(
    prompt={LEGACY_PROMPT!r},
    reformulated_query=None,
    learner_context={{'recent_topics': ['Spring DI 정의']}},
)
print(json.dumps({{
    'detected_via': d.detected_via,
    'is_follow_up': d.is_follow_up,
    'prior_topics': d.prior_topics,
    'augmented_semantic_query': d.augmented_semantic_query,
}}, ensure_ascii=False))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=LEGACY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.SubprocessError as exc:
        return {"available": True, "error": str(exc), "pass": False}
    if proc.returncode != 0:
        return {
            "available": True,
            "rc": proc.returncode,
            "stderr": proc.stderr[-500:],
            "pass": False,
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"available": True, "stdout": proc.stdout[-500:], "pass": False}
    payload["available"] = True
    payload["pass"] = (
        payload.get("detected_via") == "regex"
        and payload.get("is_follow_up") is True
        and bool(payload.get("prior_topics"))
    )
    return payload


def main() -> int:
    _ensure_daemon()
    decision_latency, query = _measure_decision()
    raw_route = route(PROMPT).mode
    auto_route = route(query).mode if query else "none"
    if query:
        daemon_search(query, top_k=5)
    search = _measure_search(query) if query else {"errors": ["missing auto query"]}
    legacy = _legacy_compare()
    top1_values = set(search.get("top1_values") or [])
    top5_values = [cid for row in search.get("top5_values") or [] for cid in row]

    checks = {
        "raw_prompt_falls_back": raw_route == "tier_0_fallback",
        "auto_query_routes_cs_qa": auto_route == "cs_qa",
        "auto_query_recovers_prior_top1": PRIOR_CONCEPT in top1_values,
        "auto_query_recovers_prior_top5": PRIOR_CONCEPT in top5_values,
        "legacy_same_follow_up_shape": legacy.get("pass") is True,
        "decision_latency_p95_budget": (
            decision_latency.get("p95_ms") is not None
            and decision_latency["p95_ms"] <= 5.0
        ),
        "search_latency_p95_budget": (
            search.get("p95_ms") is not None and search["p95_ms"] <= 500.0
        ),
    }
    report = {
        "benchmark": "auto_reformulation_path",
        "raw_prompt": PROMPT,
        "legacy_prompt": LEGACY_PROMPT,
        "prior_concept": PRIOR_CONCEPT,
        "auto_query": query,
        "raw_route": raw_route,
        "auto_route": auto_route,
        "decision_latency_ms": decision_latency,
        "search_latency_ms": search,
        "legacy_compare": legacy,
        "checks": checks,
        "pass": all(checks.values()) and not search.get("errors"),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "pass": report["pass"],
        "checks": checks,
        "decision_latency_ms": decision_latency,
        "search_latency_ms": search,
        "report": str(REPORT.relative_to(REPO_ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
