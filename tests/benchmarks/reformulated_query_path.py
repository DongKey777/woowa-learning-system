"""Y13-B explicit reformulated-query retrieval path benchmark.

Verifies that a corpus-friendly reformulation is not just surfaced in hints,
but actually changes routing/retrieval before prompt composition.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT = REPO_ROOT / "reports" / "reformulated_query_path.json"
RAW_PROMPT = "그게 뭐야"
REFORMULATED_QUERY = "Spring DI 정의"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * q)))
    return ordered[idx]


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 1) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 1) if values else None,
        "min_ms": round(min(values), 1) if values else None,
        "max_ms": round(max(values), 1) if values else None,
    }


def _ensure_daemon() -> None:
    subprocess.run(
        [
            "bin/rag-daemon",
            "start-bg",
            "--log-path",
            "/tmp/woowa-reformulated-query-path.log",
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


def _ask(args: list[str]) -> tuple[float, dict[str, Any]]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "bin/ask", *args, "--json"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    ms = (time.perf_counter() - t0) * 1000
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-300:] or f"bin/ask rc={proc.returncode}")
    return ms, json.loads(proc.stdout)


def _measure(label: str, args: list[str], iterations: int) -> dict[str, Any]:
    latencies: list[float] = []
    modes: list[str] = []
    citation_counts: list[int] = []
    reformulated_values: list[str | None] = []
    errors: list[str] = []
    for _ in range(iterations):
        try:
            ms, payload = _ask(args)
            latencies.append(ms)
            modes.append(str(payload.get("mode")))
            hints = payload.get("response_hints") or {}
            citation_counts.append(len(hints.get("citation_paths") or []))
            reformulated_values.append(hints.get("reformulated_query"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
    out = _summary(latencies)
    out.update({
        "label": label,
        "modes": sorted(set(modes)),
        "citation_counts": sorted(set(citation_counts)),
        "reformulated_values": sorted({v for v in reformulated_values if v}),
        "errors": errors,
    })
    return out


def main() -> int:
    _ensure_daemon()
    _ask([RAW_PROMPT, "--reformulated-query", REFORMULATED_QUERY])
    raw = _measure("raw_anaphora", [RAW_PROMPT], 10)
    reformulated = _measure(
        "explicit_reformulated",
        [RAW_PROMPT, "--reformulated-query", REFORMULATED_QUERY],
        10,
    )

    checks = {
        "raw_falls_back_without_citations": (
            raw["errors"] == []
            and raw["modes"] == ["tier_0_fallback"]
            and raw["citation_counts"] == [0]
        ),
        "reformulated_routes_to_cs_qa": reformulated["modes"] == ["cs_qa"],
        "reformulated_surfaces_query": (
            reformulated["reformulated_values"] == [REFORMULATED_QUERY]
        ),
        "reformulated_has_citations": bool(
            reformulated["citation_counts"]
            and min(reformulated["citation_counts"]) >= 1
        ),
        "reformulated_latency_p95_budget": (
            reformulated["p95_ms"] is not None
            and reformulated["p95_ms"] <= 500.0
        ),
    }
    report = {
        "benchmark": "reformulated_query_path",
        "raw_prompt": RAW_PROMPT,
        "reformulated_query": REFORMULATED_QUERY,
        "raw": raw,
        "reformulated": reformulated,
        "checks": checks,
        "pass": all(checks.values()) and not reformulated["errors"],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "pass": report["pass"],
        "raw": raw,
        "reformulated": reformulated,
        "checks": checks,
        "report": str(REPORT.relative_to(REPO_ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
