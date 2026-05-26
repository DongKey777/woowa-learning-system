"""F1 RAG quality regression — corpus expected_queries fixture.

For each corpus concept (3199 total), the curated `expected_queries` field
lists canonical natural-language phrasings learners would use. Each query
maps unambiguously to one ground-truth concept_id (the source file).

We sample N queries stratified by category, run them through current
rag.search via daemon, and compute:
- top-1 strict match (retrieved[0].concept_id == ground_truth)
- top-5 strict match (ground_truth in retrieved[:5])
- top-1 category match (retrieved[0].category == ground_truth_category)
- top-5 category match (ground_truth_category in retrieved[:5] categories)

Compare against Phase G v2 baseline (top5 83.6% across ephemeral fixtures,
top1 ~52%) — strict comparison since this fixture is corpus-grounded
(more stable than ephemeral baselines).
"""
from __future__ import annotations

import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path("/Users/idonghun/IdeaProjects/woowa-learning-system")
HISTORY = REPO_ROOT / "state" / "learner" / "history.jsonl"
N_SAMPLES = 200
SEED = 42
TIMEOUT_S = 30

sys.path.insert(0, str(REPO_ROOT))

from rag.eval import retrieval_metrics  # noqa: E402

# Domain heuristic — query keyword → expected primary category in top-5
DOMAIN_RULES = [
    # spring core
    (r"\b(bean|di|ioc|spring|@autowired|@component|@configuration|컴포넌트|빈|의존성\s*주입)", "spring"),
    (r"\b(@transactional|transactional|aop|@aspect|@cacheable|spring\s*보안|spring\s*security)", "spring"),
    (r"\b(mvc|controller|dispatcher|requestmapping)", "spring"),
    # database
    (r"\b(트랜잭션|transaction|isolation|jdbc|jpa|mybatis|영속성|쿼리|sql|index|lock|mvcc|redo|undo|wal)", "database"),
    # network
    (r"\b(http|tcp|udp|websocket|tls|ssl|dns|cors|cookie|session|cdn|레이턴시|latency)", "network"),
    # security
    (r"\b(보안|security|xss|csrf|sql\s*injection|jwt|oauth|password|hash|salt|crypto)", "security"),
    # language
    (r"\b(optional|stream|exception|try-with|java\s+8|generic|제네릭|lambda|record)", "language"),
    # software engineering
    (r"\b(oop|솔리드|solid|디자인\s*패턴|design pattern|refactor|리팩토링|책임|클린코드|tdd|테스트\s*전략)",
     "software-engineering"),
    # test
    (r"\b(테스트|test|junit|mockito|@spy|@mock|integration test|unit test)",
     "software-engineering"),
    # system-design
    (r"\b(분산|distributed|마이크로|saga|cqrs|event sourcing|circuit breaker)",
     "system-design"),
    # data structures / algorithms
    (r"\b(자료구조|알고리즘|해시|트리|graph|동적계획)", "data-structure"),
]


def classify_domain(query: str) -> str | None:
    """Return expected primary category, or None if no rule matches."""
    ql = query.lower()
    for pat, cat in DOMAIN_RULES:
        if re.search(pat, ql, re.IGNORECASE):
            return cat
    return None


def sample_queries(n: int = N_SAMPLES, seed: int = SEED) -> list[dict]:
    """Sample N queries stratified by category from corpus expected_queries.

    Each query carries its ground-truth concept_id + category from the
    source file. Stratification ensures category coverage isn't skewed.
    """
    corpus_dir = REPO_ROOT / "corpus" / "concepts"
    by_cat: dict[str, list[dict]] = {}
    for p in corpus_dir.rglob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cid = d.get("id") or f"{p.parent.name}/{p.stem}"
        cat = d.get("category") or p.parent.name
        eq = d.get("expected_queries") or []
        for q in eq:
            if isinstance(q, str) and len(q.strip()) >= 8:
                by_cat.setdefault(cat, []).append({
                    "query": q.strip(),
                    "gt_concept_id": cid,
                    "gt_category": cat,
                })
    rng = random.Random(seed)
    # Stratify: pick ceil(N / num_cats) from each category, then shuffle.
    cats = sorted(by_cat.keys())
    per_cat = max(1, n // len(cats))
    sampled = []
    for cat in cats:
        rng.shuffle(by_cat[cat])
        sampled.extend(by_cat[cat][:per_cat])
    rng.shuffle(sampled)
    return sampled[:n]


def run_search(query: str) -> tuple[list[dict], float, str | None]:
    """Call paradigm-v2 rag.search via daemon socket (action='search')."""
    import socket
    sock_path = str(REPO_ROOT / "state" / "rag-daemon.sock")
    if not os.path.exists(sock_path):
        return [], 0.0, "daemon not running"
    try:
        t0 = time.perf_counter()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_S)
        sock.connect(sock_path)
        req = json.dumps({"action": "search", "query": query, "top_k": 5,
                          "relations_expand": 5}) + "\n"
        sock.sendall(req.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        resp = json.loads(data.decode("utf-8").strip())
        return resp.get("hits", []), elapsed_ms, None
    except Exception as e:
        return [], 0.0, f"{type(e).__name__}: {e}"[:200]


def measure() -> dict:
    samples = sample_queries()
    print(f"Sampled {len(samples)} stratified queries from corpus expected_queries", flush=True)

    results = []
    top1_strict = top5_strict = top1_cat_match = top5_cat_match = 0
    ndcg_values = []
    mrr_values = []
    recall_values = []
    latencies = []
    errors = []
    no_hit = 0

    for i, sm in enumerate(samples, start=1):
        q = sm["query"]
        gt_cid = sm["gt_concept_id"]
        gt_cat = sm["gt_category"]
        hits, ms, err = run_search(q)
        if err:
            errors.append({"query": q[:60], "err": err})
            continue
        latencies.append(ms)
        if not hits:
            no_hit += 1
            results.append({"query": q[:80], "gt": gt_cid, "gt_cat": gt_cat,
                            "top1": None, "top5": [], "no_hit": True,
                            "latency_ms": round(ms, 1)})
            continue

        top1_cid = hits[0].get("concept_id")
        top1_cat = hits[0].get("category")
        top5_cids = [h.get("concept_id") for h in hits[:5]]
        top5_cats = [h.get("category") for h in hits[:5]]
        rank_metrics = retrieval_metrics(top5_cids, [gt_cid], k=5)
        ndcg_values.append(rank_metrics["ndcg_at_5"])
        mrr_values.append(rank_metrics["mrr"])
        recall_values.append(rank_metrics["recall_at_5"])

        is_top1 = (top1_cid == gt_cid)
        is_top5 = (gt_cid in top5_cids)
        is_top1_cat = (top1_cat == gt_cat)
        is_top5_cat = (gt_cat in top5_cats)

        top1_strict += int(is_top1)
        top5_strict += int(is_top5)
        top1_cat_match += int(is_top1_cat)
        top5_cat_match += int(is_top5_cat)

        results.append({
            "query": q[:80], "gt": gt_cid, "gt_cat": gt_cat,
            "top1": top1_cid, "top1_cat": top1_cat,
            "top5": top5_cids, "is_top1": is_top1, "is_top5": is_top5,
            "is_top1_cat": is_top1_cat, "is_top5_cat": is_top5_cat,
            "ndcg_at_5": round(rank_metrics["ndcg_at_5"], 4),
            "mrr": round(rank_metrics["mrr"], 4),
            "recall_at_5": round(rank_metrics["recall_at_5"], 4),
            "latency_ms": round(ms, 1),
        })
        if i % 20 == 0:
            print(f"  ...{i}/{len(samples)} top1={top1_strict} top5={top5_strict} "
                  f"top5_cat={top5_cat_match}", flush=True)

    n_eval = len(samples) - len(errors)
    summary = {
        "n_samples": len(samples),
        "n_evaluated": n_eval,
        "errors_n": len(errors),
        "no_hits_n": no_hit,
        "top1_strict_match_rate": round(top1_strict / n_eval, 3) if n_eval else None,
        "top5_strict_match_rate": round(top5_strict / n_eval, 3) if n_eval else None,
        "top1_category_match_rate": round(top1_cat_match / n_eval, 3) if n_eval else None,
        "top5_category_match_rate": round(top5_cat_match / n_eval, 3) if n_eval else None,
        "ndcg_at_5": round(sum(ndcg_values) / len(ndcg_values), 3) if ndcg_values else None,
        "mrr": round(sum(mrr_values) / len(mrr_values), 3) if mrr_values else None,
        "recall_at_5": round(sum(recall_values) / len(recall_values), 3) if recall_values else None,
        "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
        "latency_ms_p95": (round(sorted(latencies)[int(len(latencies) * 0.95)], 1)
                          if latencies else None),
        "category_distribution": dict(Counter(s["gt_category"] for s in samples)),
    }

    return {
        "metadata": {
            "branch": "paradigm-v2",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "history_path": str(HISTORY),
            "n_samples": N_SAMPLES,
            "seed": SEED,
        },
        "baseline_phase_g_v2": {
            "method": "4 ephemeral fixtures (train_real_v1_200, train_seed_40, train_learner_log_16, test_holdout_19)",
            "aggregate_top5": "83.6% (230/275)",
            "aggregate_top1": "~52% (142/275)",
            "note": "Different fixture — comparison is order-of-magnitude only.",
        },
        "current_measurement": summary,
        "errors": errors,
        "samples": results,
    }


if __name__ == "__main__":
    report = measure()
    out_path = REPO_ROOT / "reports" / "rag_quality_regression.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out_path}")
    print("\n=== SUMMARY ===")
    print(json.dumps(report["current_measurement"], ensure_ascii=False, indent=2))
