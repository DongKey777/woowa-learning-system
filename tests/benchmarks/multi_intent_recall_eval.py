#!/usr/bin/env python3
"""multi_intent_recall_eval — 멀티인텐트 발화 per-sub-intent recall@K 게이트.

기존 golden/qrels/eval_v2가 표현 못 하는 축을 측정한다: 한 발화에 2-3개 독립
sub-intent가 들어왔을 때 검색이 **각 sub-intent의 정답개념을 얼마나 회복하는가**.

2-arm:
  - Arm A (combined): sub-query를 한 문자열로 합친 단일 벡터(= 세션이 분해 안 하고
    한 쿼리로 검색하는 경우). per-sub-intent recall@K.
  - Arm B (decomposed): sub-query를 각각 따로 검색. per-sub-intent recall@K(상한).

gain = B - A 가 멀티인텐트 분해(§4.2.3)의 retrieval 이득을 가시화한다. 단일 벡터가
한 sub-topic에 hijack돼 나머지가 top-K 밖으로 떨어지면 Arm A가 급락한다.

이 게이트는 'retrieval 천장'만 본다 — '세션이 실제로 분해했는가'(behavioral)는 별개.

usage: WOOWA_SESSION_MODE=development python3 tests/benchmarks/multi_intent_recall_eval.py
       [--top-k 5] [--fixture tests/fixtures/multi_intent_recall_v1.json] [--json]
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "multi_intent_recall_v1.json"
DEFAULT_SOCK = REPO_ROOT / "state" / "rag-daemon.sock"

# Regression floor for Arm B (decomposed). Each sub-query searched alone should
# almost always surface its own gold — a drop here means retrieval itself broke,
# not multi-intent dilution. Arm A has no floor (it is the diluted baseline we
# track over time); we assert the decomposition GAIN stays positive instead.
ARM_B_FLOOR = 0.85
MIN_GAIN = 0.10


def _search(query: str, top_k: int, sock_path: Path) -> list[str]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect(str(sock_path))
    s.sendall((json.dumps({"action": "search", "query": query, "top_k": top_k}) + "\n").encode())
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    hits = json.loads(buf.decode()).get("hits") or []
    return [h.get("concept_id") for h in hits][:top_k]


def evaluate(fixture: Path, top_k: int, sock_path: Path) -> dict:
    blob = json.loads(fixture.read_text(encoding="utf-8"))
    rows = []
    n_sub = 0
    a_recovered = 0
    b_recovered = 0
    for q in blob.get("queries") or []:
        subs = q.get("sub_intents") or []
        combined = " ".join(s.get("query", "") for s in subs)
        full_hits = set(_search(combined, top_k, sock_path))
        a_hit = 0
        b_hit = 0
        sub_detail = []
        for s in subs:
            gold = s.get("gold_concept")
            in_full = gold in full_hits
            sub_hits = set(_search(s.get("query", ""), top_k, sock_path))
            in_sub = gold in sub_hits
            a_hit += int(in_full)
            b_hit += int(in_sub)
            sub_detail.append({"sub_id": s.get("sub_id"), "gold": gold,
                               "in_combined": in_full, "in_decomposed": in_sub})
        n = len(subs) or 1
        n_sub += len(subs)
        a_recovered += a_hit
        b_recovered += b_hit
        rows.append({"query_id": q.get("query_id"), "n_sub": len(subs),
                     "a_hit": a_hit, "b_hit": b_hit,
                     "recall_combined": round(a_hit / n, 3),
                     "recall_decomposed": round(b_hit / n, 3),
                     # A "diluting" utterance is one whose sub-intents land in
                     # different embedding clusters, so the combined single-vector
                     # query misses at least one (recall_combined < 1.0). These are
                     # the cases decomposition actually rescues; combined==1.0 ones
                     # are close-cluster controls that need no decomposition.
                     "dilutes": a_hit < len(subs),
                     "sub_detail": sub_detail})
    arm_a = round(a_recovered / n_sub, 3) if n_sub else 0.0
    arm_b = round(b_recovered / n_sub, 3) if n_sub else 0.0
    # Diluting subset — the discriminative signal. Non-diluting (close-cluster)
    # utterances inflate arm_a and shrink the headline gain, so report the gain
    # on the diluting subset separately; that is where decomposition earns its keep.
    dil = [r for r in rows if r["dilutes"]]
    dil_nsub = sum(r["n_sub"] for r in dil)
    dil_a = round(sum(r["a_hit"] for r in dil) / dil_nsub, 3) if dil_nsub else 0.0
    dil_b = round(sum(r["b_hit"] for r in dil) / dil_nsub, 3) if dil_nsub else 0.0
    return {
        "fixture": fixture.name,
        "top_k": top_k,
        "n_utterances": len(rows),
        "n_sub_intents": n_sub,
        "arm_a_combined_recall": arm_a,
        "arm_b_decomposed_recall": arm_b,
        "decomposition_gain": round(arm_b - arm_a, 3),
        "n_diluting_utterances": len(dil),
        "diluting_arm_a": dil_a,
        "diluting_arm_b": dil_b,
        "diluting_gain": round(dil_b - dil_a, 3),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--state-root", type=Path, default=REPO_ROOT / "state")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sock = args.state_root / "rag-daemon.sock"
    if not sock.exists():
        print(f"ERROR: daemon socket 없음 {sock} — bin/rag-daemon start-bg 먼저", file=sys.stderr)
        return 2

    report = evaluate(args.fixture, args.top_k, sock)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"=== multi-intent recall@{report['top_k']} "
              f"({report['n_utterances']} 발화 / {report['n_sub_intents']} sub-intent) ===")
        for r in report["rows"]:
            tag = "" if r["dilutes"] else "  (control: 비-희석)"
            print(f"  {r['query_id']}: combined {r['recall_combined']} | "
                  f"decomposed {r['recall_decomposed']} (n={r['n_sub']}){tag}")
        print(f"\n  전체 {report['n_utterances']}발화 — Arm A {report['arm_a_combined_recall']:.0%} / "
              f"Arm B {report['arm_b_decomposed_recall']:.0%} / gain +{report['decomposition_gain']:.0%}p")
        print(f"  희석 subset {report['n_diluting_utterances']}발화 (cross-cluster, combined<100%) — "
              f"Arm A {report['diluting_arm_a']:.0%} / Arm B {report['diluting_arm_b']:.0%} / "
              f"**gain +{report['diluting_gain']:.0%}p** (진짜 신호)")

    # Gate: decomposed recall must hold the floor across ALL cases (a regression in
    # decomposed retrieval is real). The gain is asserted on the DILUTING subset —
    # the discriminative signal — not the overall, which close-cluster controls mute.
    ok = (report["arm_b_decomposed_recall"] >= ARM_B_FLOOR
          and report["diluting_gain"] >= MIN_GAIN)
    if not ok:
        print(f"\nGATE FAIL: arm_b={report['arm_b_decomposed_recall']} (floor {ARM_B_FLOOR}), "
              f"diluting_gain={report['diluting_gain']} (min {MIN_GAIN})", file=sys.stderr)
        return 1
    print("\nGATE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
