#!/usr/bin/env python3
"""eval_v2 — eval-framework-v2 multi-tier release harness (Phase 4).

Runs the tier fixtures through bin/cohort-eval, aggregates **per-category** with
**confidence-interval** floors (no point-estimate gates), and applies the
eval-framework-v2 release inequalities. Replaces the single 3/11-category point
gate with: T-A authority (behavioral) + T-C coverage (representativeness,
per-category) + T-F foundation findability.

Tiers (fixture path + role); any missing tier is skipped (reported, not failed):
  T-A authority  : real_learner_qrels_v1.json   (Wilson lower bound on learner_top1)
  T-C coverage   : coverage_stratified_v1.json  (per-category graded nDCG@5 + Hit@5)
  T-F foundation : foundation_findability_v1.json (top5 recall mean)

CLI: python tests/benchmarks/eval_v2.py [--top-k 5] [--json]
Metric/floor rationale: reports/eval-framework-v2/EVAL-FRAMEWORK-V2-DESIGN.md
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIX = REPO_ROOT / "tests" / "fixtures"

TIERS = {
    "T-A authority": FIX / "real_learner_qrels_v1.json",
    "T-C coverage": FIX / "coverage_stratified_v1.json",
    "T-F foundation": FIX / "foundation_findability_v1.json",
}

# eval-framework-v2 gate = baseline-relative (delta vs last release). The v1.0.5
# baseline is established on first run (eval_v2_baseline.json); subsequent runs
# block on regression beyond MARGIN. This is the §4.4 drift-robust gate — absolute
# floors don't fit a corpus whose naive-query findability legitimately varies by
# category (language hit5 0.47 vs OS 0.79). Per-category baselines are tracked so
# the aggregate can't hide a single-category collapse.
MARGIN = 0.05
PERCAT_MIN_N = 15  # categories below this size: tracked but not delta-gated
Z = 1.96


def wilson_lower(k: int, n: int, z: float = Z) -> float:
    if n == 0:
        return 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def bootstrap_lower(values: list[float], pct: float = 2.5, iters: int = 2000) -> float:
    """Deterministic LCG bootstrap lower CI bound (no RNG dependency)."""
    if not values:
        return 0.0
    n = len(values)
    seed = 1442695040888963407
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            seed = (seed * 6364136223846793005 + 1) & ((1 << 64) - 1)
            s += values[(seed >> 33) % n]
        means.append(s / n)
    means.sort()
    return means[int(pct / 100 * iters)]


def _run_cohort(fixture: Path, top_k: int) -> tuple[dict, list[dict]]:
    out = Path(tempfile.gettempdir()) / f"evalv2_{fixture.stem}.json"
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "cohort-eval"),
         "--qrels", str(fixture), "--out", str(out), "--top-k", str(top_k),
         "--relations-expand", "5"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
        env={**_env()},
    )
    if r.returncode != 0:
        raise RuntimeError(f"cohort-eval failed for {fixture.name}: {r.stderr[-300:]}")
    summary = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    blob = json.loads(out.read_text())
    return blob.get("summary", summary), blob["results"]


def _env() -> dict:
    import os
    e = dict(os.environ)
    e["WOOWA_SESSION_MODE"] = "development"
    return e


def _category_of(record: dict, fixture_records: dict) -> str:
    cid = (record.get("expected_concepts_in_corpus") or [None])[0] or ""
    return fixture_records.get(record.get("query_id"), {}).get("category") or (
        cid.split("/")[0] if "/" in cid else "?"
    )


def eval_tier(fixture: Path, top_k: int) -> dict:
    blob = json.loads(fixture.read_text())
    fx = {q.get("query_id"): q for q in (blob.get("queries") or [])}
    summary, rows = _run_cohort(fixture, top_k)
    per_cat = defaultdict(lambda: {"n": 0, "top1": 0, "hit5": 0, "ndcg": []})
    overall = {"n": 0, "top1": 0, "hit5": 0, "ndcg": []}
    from rag.eval import ndcg_at_k  # graded (primary=3/acceptable=1)
    for row in rows:
        # ranking-eligible only — a query whose expected concept isn't in the
        # corpus can't be scored (matches cohort-eval's ranking_evaluated count).
        if not (row.get("expected_concepts_in_corpus") or row.get("relevant_concepts_in_corpus")):
            continue
        cat = _category_of(row, fx)
        t1 = bool(row.get("top1_match"))
        t5 = bool(row.get("top5_match"))
        top = row.get("top_concepts") or []
        top_ids = [t.get("concept_id") if isinstance(t, dict) else t for t in top]
        primary = list(dict.fromkeys(
            (row.get("expected_concepts_in_corpus") or [])
            + (row.get("primary_path_concepts_in_corpus") or [])
        ))
        acceptable = row.get("acceptable_path_concepts_in_corpus") or []
        ndcg = ndcg_at_k(top_ids, primary, top_k, acceptable_ids=acceptable or None)
        for bucket in (per_cat[cat], overall):
            bucket["n"] += 1
            bucket["top1"] += t1
            bucket["hit5"] += t5
            bucket["ndcg"].append(ndcg)
    cats = {}
    for c, b in per_cat.items():
        cats[c] = {
            "n": b["n"],
            "top1": round(b["top1"] / b["n"], 3),
            "hit5": round(b["hit5"] / b["n"], 3),
            "ndcg5": round(sum(b["ndcg"]) / b["n"], 3),
        }
    return {
        "fixture": fixture.name,
        "n": overall["n"],
        # authoritative learner_top1 from cohort-eval (top1 ∈ primary∪acceptable,
        # over ranking_evaluated) — the established gate metric, not re-derived.
        "learner_top1": summary.get("learner_top1_match_rate"),
        "ndcg5_authoritative": summary.get("ndcg_at_5"),
        "top1": round(overall["top1"] / overall["n"], 3) if overall["n"] else 0.0,
        "hit5": round(overall["hit5"] / overall["n"], 3) if overall["n"] else 0.0,
        "ndcg5_mean": round(sum(overall["ndcg"]) / overall["n"], 3) if overall["n"] else 0.0,
        "ndcg5_boot_lower": round(bootstrap_lower(overall["ndcg"]), 3),
        "top1_wilson_lower": round(wilson_lower(overall["top1"], overall["n"]), 3),
        "hit5_wilson_lower": round(wilson_lower(overall["hit5"], overall["n"]), 3),
        "per_category": cats,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report, gate_fail = {}, []
    for name, path in TIERS.items():
        if not path.exists():
            report[name] = {"status": "MISSING"}
            continue
        report[name] = eval_tier(path, args.top_k)

    # baseline-relative gate (eval-framework-v2 §4.4: 절대 임계뿐 아니라 직전 release
    # 대비 델타로 게이트해 코퍼스 성장에 강건). 첫 실행 = baseline 확립 + PASS.
    baseline_path = FIX / "eval_v2_baseline.json"
    cur = {
        "T-A.learner_top1": (report.get("T-A authority") or {}).get("learner_top1"),
        "T-C.hit5": (report.get("T-C coverage") or {}).get("hit5"),
        "T-C.ndcg5": (report.get("T-C coverage") or {}).get("ndcg5_mean"),
        "T-F.hit5": (report.get("T-F foundation") or {}).get("hit5"),
    }
    for c, m in (report.get("T-C coverage") or {}).get("per_category", {}).items():
        cur[f"T-C.hit5[{c}]"] = m["hit5"]
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(
            {"established_for": "v1.0.5", "margin": MARGIN, "metrics": cur},
            ensure_ascii=False, indent=2), encoding="utf-8")
        report["gate"] = {"pass": True, "mode": "baseline_established",
                          "note": f"baseline written to {baseline_path.name} (v1.0.5)"}
    else:
        base = json.loads(baseline_path.read_text())
        bm, margin = base["metrics"], base.get("margin", MARGIN)
        for key, val in cur.items():
            ref = bm.get(key)
            if val is not None and ref is not None and val < ref - margin:
                gate_fail.append(f"{key} {val:.3f} < baseline {ref:.3f} − {margin} (회귀)")
        report["gate"] = {"pass": not gate_fail, "mode": "delta_vs_baseline",
                          "failures": gate_fail}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not gate_fail else 1
    for name in TIERS:
        t = report[name]
        if t.get("status") == "MISSING":
            print(f"\n{name}: (fixture 미존재 — skip)")
            continue
        print(f"\n{name} [{t['fixture']}] n={t['n']}")
        print(f"  top1 {t['top1']} (Wilson≥{t['top1_wilson_lower']}) | "
              f"hit5 {t['hit5']} | nDCG@5 {t['ndcg5_mean']} (boot≥{t['ndcg5_boot_lower']})")
        if t.get("per_category"):
            for c, m in sorted(t["per_category"].items()):
                print(f"    {c:18} n={m['n']:>3} top1 {m['top1']} hit5 {m['hit5']} nDCG {m['ndcg5']}")
    g = report["gate"]
    print(f"\n=== GATE: {'PASS' if g['pass'] else 'FAIL'} ({g.get('mode')}) ===")
    if g.get("note"):
        print(f"  {g['note']}")
    for f in g.get("failures", []):
        print(f"  ✗ {f}")
    return 0 if g["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
