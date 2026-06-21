#!/usr/bin/env python3
"""full_pipeline_eval — 세션-side 풀 파이프라인(HyDE→CC→rerank) 전후 측정.

게이트(golden/eval_v2/cohort)는 raw 쿼리를 daemon search action에 넣어 **CC fusion만** 본다.
HyDE·rerank·내용주입은 세션(LLM) 행동/ask 경로라 그 게이트가 못 본다. 이 벤치는 세션 행동을
**캐시된 fixture**(14-agent HyDE 생성 + 7-agent blind rerank)로 재현해 풀 파이프라인을 deterministic하게
재측정한다 — daemon/fusion/content 코드 회귀를 LLM 없이 감지.

4-arm:
  arm0 raw+rrf  (recorded baseline, rrf daemon에서만 재측정 가능)
  arm1 raw+cc   (현 daemon=cc, raw 쿼리)           ← 게이트가 보는 것
  arm2 +HyDE    (cc, q + cached HyDE 키워드)
  arm3 +rerank  (cc, HyDE 후보에 cached rerank concept_id 적용)

rerank fixture는 index가 아닌 concept_id로 저장돼 top-5 재정렬에 robust(선택 개념이 현 top-5에 있으면 적용).

CLI: python tests/benchmarks/full_pipeline_eval.py [--json]
fixture 갱신(쿼리/코퍼스 변경 시): reports/top1-top5-research/VERIFICATION-METHOD.md의 'live refresh' 참조.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
FIX = REPO / "tests" / "fixtures"
BASELINE = FIX / "full_pipeline_baseline.json"
SOCK = REPO / "state" / "rag-daemon.sock"
MARGIN = 0.05  # arm3 top1 회귀 허용폭


def _search(query: str, top_k: int = 5, relations_expand: int = 5) -> list[str]:
    if not SOCK.exists():
        raise RuntimeError(f"daemon socket missing: {SOCK} (bin/rag-daemon start-bg)")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect(str(SOCK))
    req = {"action": "search", "query": query, "top_k": top_k,
           "relations_expand": relations_expand}
    s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    buf = b""
    while True:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    resp = json.loads(buf.decode("utf-8").strip())
    return [h.get("concept_id") for h in (resp.get("hits") or [])][:top_k]


def evaluate() -> dict:
    queries = json.loads((FIX / "full_pipeline_queries_v1.json").read_text())["queries"]
    hyde = json.loads((FIX / "full_pipeline_hyde_v1.json").read_text())["hyde"]
    rerank = json.loads((FIX / "full_pipeline_rerank_v1.json").read_text())["rerank"]

    agg = {a: [0, 0] for a in ("arm1", "arm2", "arm3")}  # [top1, top5]
    per_stratum = defaultdict(lambda: {a: [0, 0] for a in ("arm1", "arm2", "arm3")})
    n = 0
    for item in queries:
        qid = str(item["qid"])
        ans = set(item["answer"])
        if not ans:
            continue
        n += 1
        raw_ids = _search(item["q"])
        hyde_ids = _search(f'{item["q"]} {hyde.get(qid, "")}'.strip())
        chosen = rerank.get(qid)
        arm3_top1 = chosen if (chosen and chosen in hyde_ids) else (hyde_ids[0] if hyde_ids else None)
        res = {
            "arm1": (bool(raw_ids and raw_ids[0] in ans), bool(set(raw_ids) & ans)),
            "arm2": (bool(hyde_ids and hyde_ids[0] in ans), bool(set(hyde_ids) & ans)),
            "arm3": (bool(arm3_top1 in ans), bool(set(hyde_ids) & ans)),
        }
        st = item.get("stratum", "?")
        for a, (t1, t5) in res.items():
            agg[a][0] += t1
            agg[a][1] += t5
            per_stratum[st][a][0] += t1
            per_stratum[st][a][1] += t5
    out = {"n": n, "arms": {a: {"top1": round(v[0] / n, 3), "top5": round(v[1] / n, 3)}
                            for a, v in agg.items()}}
    out["per_stratum"] = {s: {a: {"top1": round(v[a][0] / max(1, sum(1 for q in queries if q.get("stratum") == s and q["answer"])), 3)}
                              for a in ("arm1", "arm2", "arm3")}
                          for s, v in per_stratum.items()}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = evaluate()
    arms = report["arms"]
    # arm0 (rrf baseline) is recorded — re-measure only on an rrf daemon.
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else None
    arm0 = (base or {}).get("arm0", {"top1": None, "top5": None})
    report["arm0_recorded"] = arm0

    fail = []
    if base is None:
        # establish baseline (includes a placeholder arm0 to be filled from an rrf run)
        BASELINE.write_text(json.dumps(
            {"established": "v1", "margin": MARGIN,
             "arm0": {"top1": 0.484, "top5": 0.718, "note": "raw+rrf, recorded"},
             "arms": arms}, ensure_ascii=False, indent=2), encoding="utf-8")
        report["gate"] = {"pass": True, "mode": "baseline_established"}
    else:
        ref = base["arms"]["arm3"]["top1"]
        cur = arms["arm3"]["top1"]
        if cur < ref - MARGIN:
            fail.append(f"arm3 top1 {cur:.3f} < baseline {ref:.3f} − {MARGIN} (회귀)")
        report["gate"] = {"pass": not fail, "failures": fail}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not fail else 1
    print(f"full-pipeline 4-arm (n={report['n']}):")
    print(f"  arm0 raw+rrf  (recorded) : top1 {arm0.get('top1')} top5 {arm0.get('top5')}")
    for a, label in (("arm1", "raw+cc  (게이트가 봄)"), ("arm2", "+HyDE        "), ("arm3", "+rerank (full)")):
        print(f"  {a} {label}: top1 {arms[a]['top1']} top5 {arms[a]['top5']}")
    if arm0.get("top1") is not None:
        print(f"  ★ BEFORE→AFTER: top1 {arm0['top1']}→{arms['arm3']['top1']} "
              f"(+{(arms['arm3']['top1'] - arm0['top1']) * 100:.0f}pp)")
    g = report["gate"]
    print(f"  GATE: {'PASS' if g['pass'] else 'FAIL'} ({g.get('mode', 'delta')})")
    for f in g.get("failures", []):
        print(f"   ✗ {f}")
    return 0 if g["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
