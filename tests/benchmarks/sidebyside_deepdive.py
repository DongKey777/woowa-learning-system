"""Side-by-side deep dive for 4 representative scenarios.

Captures full output from both systems and highlights:
- Evidence types present (mission_patterns, cross_crew, rag_hits, cs_augmentation)
- Concept citations (parsed `참고:` or [path] markers)
- Personas / mode signaling
- Token budget

For visual / qualitative comparison.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

PARADIGM_V2_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_ROOT = Path("/Users/idonghun/IdeaProjects/woowa-learning-hub")

DEEP_DIVE_SCENARIOS = [
    {"sid": "cs_qa_di", "prompt": "DI가 뭐야", "repo": None,
     "feature": "F1 cs_qa", "purpose": "단순 정의 — 코퍼스 활용도 비교"},
    {"sid": "coach_refactor", "prompt": "내 ReservationController 어떻게 리팩토링하면 좋아?",
     "repo": "spring-roomescape-member", "feature": "F2+F10 coaching",
     "purpose": "Mentor 시각 + mission_patterns surface"},
    {"sid": "f11_cross_crew",
     "prompt": "다른 크루는 이 reservation 코드를 어떻게 작성했어?",
     "repo": "spring-roomescape-member", "feature": "F11 cross-crew",
     "purpose": "Cross-crew 비교 — pre-built parquet payoff"},
    {"sid": "tool_transactional", "prompt": "@Transactional 어디에 붙여야 해?",
     "repo": None, "feature": "F3 tool_only",
     "purpose": "Tool fast-path"},
]


def run_v2(prompt: str, repo: str | None) -> tuple[str, float]:
    cmd = ["python3", "bin/ask", prompt]
    if repo:
        cmd.extend(["--repo", repo])
    t0 = time.perf_counter()
    res = subprocess.run(cmd, cwd=str(PARADIGM_V2_ROOT), capture_output=True,
                         text=True, timeout=60,
                         env={**os.environ, "WOOWA_SESSION_MODE": "development"})
    return res.stdout, (time.perf_counter() - t0) * 1000


def run_legacy(prompt: str, repo: str | None) -> tuple[str, float]:
    cmd = ["bin/rag-ask", prompt]
    t0 = time.perf_counter()
    res = subprocess.run(cmd, cwd=str(LEGACY_ROOT), capture_output=True,
                         text=True, timeout=60,
                         env={**os.environ, "WOOWA_SESSION_MODE": "development"})
    return res.stdout, (time.perf_counter() - t0) * 1000


def analyze_v2(text: str) -> dict:
    return {
        "mode": (re.search(r"\*\*mode\*\*:\s*`([^`]+)`", text) or [None, "?"])[1],
        "budget": (re.search(r"\*\*token budget\*\*:\s*(\d+)", text) or [None, "?"])[1],
        "personas": re.findall(r"\[(MENTOR|REVIEWER|SOCRATIC)\]", text),
        "has_mission_patterns": "mission_patterns" in text and "NOT YET BUILT" not in text,
        "has_cross_crew": "cross_crew_review_graph" in text and "NOT YET BUILT" not in text,
        "has_rag_hits": "rag_hits" in text,
        "rag_hit_count": len(re.findall(r"- \[\w+\] `", text)),
        "concept_ids_cited": re.findall(r"`([a-z\-]+/[\w\-]+)`", text)[:5],
        "output_chars": len(text),
    }


def analyze_legacy(text: str) -> dict:
    # Legacy: tier-N header + 참고: footer typically
    tier_m = re.search(r"\[RAG:\s*tier-(\d+)\s*—\s*([^\]]+)\]", text)
    citations = re.findall(r"- ([\w\-/]+\.md)", text)
    return {
        "tier": f"tier-{tier_m.group(1)}" if tier_m else "?",
        "reason": tier_m.group(2).strip() if tier_m else "?",
        "has_cs_augmentation": "cs_augmentation" in text.lower() or "cs_block" in text.lower(),
        "has_citation_block": "참고:" in text,
        "citations": citations[:5],
        "output_chars": len(text),
    }


def main():
    out = []
    for sc in DEEP_DIVE_SCENARIOS:
        print(f"[{sc['sid']}]", flush=True)
        v2_out, v2_ms = run_v2(sc["prompt"], sc["repo"])
        leg_out, leg_ms = run_legacy(sc["prompt"], sc["repo"])
        entry = {
            "scenario": sc,
            "paradigm_v2": {
                "latency_ms": round(v2_ms, 1),
                "analysis": analyze_v2(v2_out),
                "output_sample": v2_out[:2000],
            },
            "legacy": {
                "latency_ms": round(leg_ms, 1),
                "analysis": analyze_legacy(leg_out),
                "output_sample": leg_out[:2000],
            },
        }
        out.append(entry)

    out_path = PARADIGM_V2_ROOT / "reports" / "v2_vs_legacy_deepdive.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote: {out_path}")

    # Print compact summary
    print("\n=== SIDE-BY-SIDE SUMMARY ===")
    for e in out:
        sc = e["scenario"]
        v2a = e["paradigm_v2"]["analysis"]
        la = e["legacy"]["analysis"]
        print(f"\n[{sc['sid']}] {sc['feature']}")
        print(f"  prompt: {sc['prompt']}")
        print(f"  v2:     mode={v2a['mode']:<12} {e['paradigm_v2']['latency_ms']:6.1f}ms "
              f"chars={v2a['output_chars']:5d}  "
              f"mission_patterns={v2a['has_mission_patterns']}  "
              f"cross_crew={v2a['has_cross_crew']}  "
              f"rag_hits={v2a['rag_hit_count']}")
        print(f"  legacy: {la['tier']:<12} {e['legacy']['latency_ms']:6.1f}ms "
              f"chars={la['output_chars']:5d}  "
              f"reason={la['reason'][:40]}")


if __name__ == "__main__":
    main()
