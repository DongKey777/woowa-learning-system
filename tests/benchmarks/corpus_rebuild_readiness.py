#!/usr/bin/env python3
"""Corpus readiness audit before rebuilding the dense index."""
from __future__ import annotations

import json
import hashlib
import argparse
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path("/Users/idonghun/IdeaProjects/woowa-learning-system")

import sys

sys.path.insert(0, str(REPO_ROOT))

from core.lexical_fusion import _corpus_fingerprint as _dense_corpus_fingerprint  # noqa: E402
from rag.corpus_loader import load_corpus, load_corpus_snapshot  # noqa: E402
from rag.search import _exact_query_shortcut_hits, _norm_exact  # noqa: E402

STATE_INDEX = REPO_ROOT / "state" / "index"
QRELS_PATH = REPO_ROOT / "tests" / "fixtures" / "r3_qrels_real_v1.json"
REPORT_PATH = REPO_ROOT / "reports" / "corpus_rebuild_readiness.json"

STRESS_QUERIES = [
    "DI가 뭐야",
    "Bean이 뭐야",
    "MVC가 뭐야",
    "@Transactional 사용법",
    "JdbcTemplate 어떻게 써",
    "Optional 사용",
    "Stream API",
    "Spring Bean lifecycle",
    "Configuration vs Component",
    "트랜잭션 isolation level",
    "Lazy loading",
    "AOP가 뭐야",
]


@dataclass
class Check:
    name: str
    passed: bool
    observed: str
    details: dict = field(default_factory=dict)


def _duplicate_norms(corpus, field: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for cid, concept in corpus.concepts.items():
        values = [concept.get("title")] if field == "title" else concept.get(field) or []
        for value in values:
            norm = _norm_exact(str(value))
            if norm:
                buckets[norm].append(cid)
    return {k: sorted(set(v)) for k, v in buckets.items() if len(set(v)) > 1}


def _full_corpus_fingerprint(corpus) -> str:
    payload = {
        cid: corpus.concepts[cid]
        for cid in sorted(corpus.concepts)
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _qrels_short_cases() -> list[dict]:
    queries = json.loads(QRELS_PATH.read_text(encoding="utf-8"))["queries"]
    return [
        q for q in queries
        if len(q.get("expected_concepts") or []) == 1
        and len(q.get("prompt", "")) <= 45
        and not str(q.get("query_id", "")).startswith("corpus_gap_probe")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=STATE_INDEX)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    index_dir = args.index_dir

    started = time.perf_counter()
    loaded = load_corpus(strict=False)
    checks: list[Check] = [
        Check(
            "schema_valid",
            not loaded.failures,
            f"{len(loaded.concepts)} concepts, {len(loaded.failures)} failures",
            {"failures": loaded.failures[:20]},
        )
    ]
    if loaded.failures:
        corpus = loaded
    else:
        corpus = load_corpus(strict=True)

    dense_corpus_sha = _dense_corpus_fingerprint(corpus)
    full_corpus_sha = _full_corpus_fingerprint(corpus)
    title_dups = _duplicate_norms(corpus, "title")
    expected_dups = _duplicate_norms(corpus, "expected_queries")
    checks.append(Check(
        "title_exact_unique",
        not title_dups,
        f"{len(title_dups)} duplicate normalized titles",
        {"examples": dict(list(title_dups.items())[:20])},
    ))

    stress_hits = {
        q: [h.concept_id for h in _exact_query_shortcut_hits(q, corpus)]
        for q in STRESS_QUERIES
    }
    stress_missing = [q for q, ids in stress_hits.items() if not ids]
    checks.append(Check(
        "stress_queries_exact_covered",
        not stress_missing,
        f"{len(STRESS_QUERIES) - len(stress_missing)}/{len(STRESS_QUERIES)} exact covered",
        {"missing": stress_missing, "hits": stress_hits},
    ))

    qrels = _qrels_short_cases()
    exact_ok: list[str] = []
    exact_wrong: list[dict] = []
    exact_missing: list[dict] = []
    cohort_missing = Counter()
    for q in qrels:
        prompt = q["prompt"]
        expected = q["expected_concepts"][0]
        ids = [h.concept_id for h in _exact_query_shortcut_hits(prompt, corpus)]
        if ids and ids[0] == expected:
            exact_ok.append(q["query_id"])
        elif ids:
            exact_wrong.append({
                "query_id": q["query_id"],
                "prompt": prompt,
                "expected": expected,
                "exact": ids,
            })
        else:
            exact_missing.append({
                "query_id": q["query_id"],
                "prompt": prompt,
                "expected": expected,
                "cohort_tag": q.get("cohort_tag"),
            })
            cohort_missing[q.get("cohort_tag", "unknown")] += 1
    checks.append(Check(
        "qrels_short_exact_not_wrong",
        not exact_wrong,
        f"{len(exact_wrong)} wrong exact owners, {len(exact_missing)} missing exact owners",
        {
            "short_cases": len(qrels),
            "exact_ok": len(exact_ok),
            "wrong": exact_wrong,
            "missing_sample": exact_missing[:80],
            "missing_by_cohort": dict(cohort_missing),
        },
    ))

    snapshot = load_corpus_snapshot(index_dir / "corpus_snapshot.json")
    snapshot_sha = _full_corpus_fingerprint(snapshot) if snapshot else None
    checks.append(Check(
        "runtime_snapshot_fresh",
        snapshot_sha == full_corpus_sha,
        f"snapshot_full_sha={snapshot_sha or 'missing'} full_corpus_sha={full_corpus_sha}",
    ))

    lexical_path = index_dir / "lexical_fusion_sidecar.json"
    lexical_sha = None
    if lexical_path.exists():
        try:
            lexical_payload = json.loads(lexical_path.read_text(encoding="utf-8"))
            lexical_sha = (
                lexical_payload.get("dense_corpus_sha256")
                or lexical_payload.get("corpus_sha256")
            )
        except json.JSONDecodeError:
            lexical_sha = "invalid-json"
    checks.append(Check(
        "lexical_sidecar_fresh",
        lexical_sha == dense_corpus_sha,
        f"lexical_dense_sha={lexical_sha or 'missing'} dense_corpus_sha={dense_corpus_sha}",
    ))

    manifest_path = index_dir / "manifest.json"
    manifest_dense_sha = None
    manifest_full_sha = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_dense_sha = (
                manifest.get("dense_corpus_sha256")
                or manifest.get("corpus_sha256")
            )
            manifest_full_sha = manifest.get("full_corpus_sha256")
        except json.JSONDecodeError:
            manifest_dense_sha = "invalid-json"
            manifest_full_sha = "invalid-json"
    rebuild_needed = manifest_dense_sha != dense_corpus_sha

    duplicate_qrel_prompts = []
    for q in qrels:
        claimants = expected_dups.get(_norm_exact(q["prompt"]))
        if claimants:
            duplicate_qrel_prompts.append({
                "query_id": q["query_id"],
                "cohort_tag": q.get("cohort_tag"),
                "prompt": q["prompt"],
                "expected": q["expected_concepts"][0],
                "claimants": claimants,
            })

    ready_checks = [
        c for c in checks
        if c.name in {
            "schema_valid",
            "title_exact_unique",
            "stress_queries_exact_covered",
            "qrels_short_exact_not_wrong",
        }
    ]
    ready_for_dense_rebuild = all(c.passed for c in ready_checks)
    report = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "corpus_sha256": dense_corpus_sha,
            "dense_corpus_sha256": dense_corpus_sha,
            "full_corpus_sha256": full_corpus_sha,
            "manifest_corpus_sha256": manifest_dense_sha,
            "manifest_dense_corpus_sha256": manifest_dense_sha,
            "manifest_full_corpus_sha256": manifest_full_sha,
            "rebuild_needed": rebuild_needed,
            "index_dir": str(index_dir),
        },
        "ready_for_dense_rebuild": ready_for_dense_rebuild,
        "checks": [asdict(c) for c in checks],
        "advisory": {
            "duplicate_expected_query_norms": len(expected_dups),
            "duplicate_expected_examples": dict(list(expected_dups.items())[:40]),
            "duplicate_short_qrel_exact_prompts": len(duplicate_qrel_prompts),
            "duplicate_short_qrel_exact_examples": duplicate_qrel_prompts[:80],
        },
    }
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ready_for_dense_rebuild={ready_for_dense_rebuild}")
    print(f"rebuild_needed={rebuild_needed}")
    print(f"dense_corpus_sha256={dense_corpus_sha}")
    print(f"full_corpus_sha256={full_corpus_sha}")
    for check in checks:
        marker = "OK" if check.passed else "FAIL"
        print(f"{marker} {check.name}: {check.observed}")
    print(f"Report: {args.report_path}")
    return 0 if ready_for_dense_rebuild else 1


if __name__ == "__main__":
    raise SystemExit(main())
