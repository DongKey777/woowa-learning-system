"""AI semantic judge framework — no external API, uses session runtime (D7+D9).

Mechanism:
1. `EvalSample` = (query, expected_concept_ids, retrieved_hits, answer_text)
2. `build_judge_prompt(sample)` → markdown prompt
3. AI session (this conversation) reads prompt + emits `Judgment` line
4. `parse_judgment(text)` extracts {equivalent, confidence, reason}
5. `majority(judgments)` over 3 rounds — D7 falsification gate

History sampler reads learner history.jsonl (D12 mode=learning filter).
"""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EvalSample:
    sample_id: str
    query: str
    expected_concept_ids: list[str]
    retrieved_hits: list[dict]  # {concept_id, score, title}
    answer_text: str


@dataclass(frozen=True)
class Judgment:
    sample_id: str
    round_n: int
    equivalent: bool
    confidence: float
    reason: str


def build_judge_prompt(sample: EvalSample) -> str:
    """Render Korean markdown prompt for in-session AI semantic judge."""
    hits_md = "\n".join(
        f"  {i + 1}. `{h['concept_id']}` — {h.get('title', '?')} (score={h.get('score', 0):.3f})"
        for i, h in enumerate(sample.retrieved_hits)
    ) or "  (없음)"
    expected_md = ", ".join(f"`{c}`" for c in sample.expected_concept_ids) or "(불특정)"
    return (
        f"## 평가 sample: {sample.sample_id}\n\n"
        f"**Query**: {sample.query}\n\n"
        f"**Expected concepts**: {expected_md}\n\n"
        f"**Retrieved top-K**:\n{hits_md}\n\n"
        f"**Generated answer**:\n```\n{sample.answer_text}\n```\n\n"
        "**판단**: retrieved hits + answer가 query의 의도를 의미적으로 충족하는가?\n"
        "Path string match가 아닌 semantic equivalence 기준. Best-fit retarget OK.\n\n"
        "출력 형식 (1줄):\n"
        "`equivalent: true|false, confidence: 0.0-1.0, reason: <한 줄>`"
    )


_PARSE_PATTERN = re.compile(
    r"equivalent\s*:\s*(true|false)\s*,\s*confidence\s*:\s*([0-9]*\.?[0-9]+)\s*,\s*reason\s*:\s*(.+)",
    re.IGNORECASE,
)


def parse_judgment(text: str, sample_id: str, round_n: int) -> Judgment:
    """Extract Judgment from AI session output line."""
    match = _PARSE_PATTERN.search(text)
    if match is None:
        raise ValueError(f"could not parse judgment from: {text!r}")
    equiv = match.group(1).lower() == "true"
    conf = float(match.group(2))
    if not 0.0 <= conf <= 1.0:
        raise ValueError(f"confidence out of [0, 1]: {conf}")
    reason = match.group(3).strip()
    return Judgment(sample_id=sample_id, round_n=round_n, equivalent=equiv, confidence=conf, reason=reason)


def majority(judgments: list[Judgment]) -> Judgment:
    """Majority vote over N judgments — returns synthetic round_n=-1 verdict."""
    if not judgments:
        raise ValueError("empty judgments list")
    sid = judgments[0].sample_id
    if any(j.sample_id != sid for j in judgments):
        raise ValueError("judgments mix different sample_id")
    true_votes = sum(1 for j in judgments if j.equivalent)
    verdict = true_votes > len(judgments) / 2
    avg_conf = sum(j.confidence for j in judgments) / len(judgments)
    return Judgment(
        sample_id=sid,
        round_n=-1,
        equivalent=verdict,
        confidence=round(avg_conf, 3),
        reason=f"majority {true_votes}/{len(judgments)}",
    )


def reciprocal_rank(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """Return MRR contribution for one ranked result list."""
    expected = set(expected_ids)
    if not expected:
        return 0.0
    for idx, cid in enumerate(retrieved_ids, start=1):
        if cid in expected:
            return 1.0 / idx
    return 0.0


def recall_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """Fraction of expected ids present in the first k retrieved ids."""
    expected = set(expected_ids)
    if not expected:
        return 0.0
    return len(expected & set(retrieved_ids[:k])) / len(expected)


def dcg_at_k(relevances: list[float], k: int) -> float:
    """Discounted cumulative gain for a relevance vector."""
    total = 0.0
    for idx, rel in enumerate(relevances[:k], start=1):
        total += (2**rel - 1) / math.log2(idx + 1)
    return total


def ndcg_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """NDCG@k using binary relevance against expected concept ids."""
    expected = set(expected_ids)
    if not expected:
        return 0.0
    relevances = [1.0 if cid in expected else 0.0 for cid in retrieved_ids[:k]]
    ideal_relevances = [1.0] * min(len(expected), k)
    ideal = dcg_at_k(ideal_relevances, k)
    if ideal == 0:
        return 0.0
    return dcg_at_k(relevances, k) / ideal


def retrieval_metrics(
    retrieved_ids: list[str],
    expected_ids: list[str],
    k: int = 5,
) -> dict[str, float]:
    """Compute per-query ranking metrics used by Y13 gates."""
    return {
        f"recall_at_{k}": recall_at_k(retrieved_ids, expected_ids, k),
        "mrr": reciprocal_rank(retrieved_ids, expected_ids),
        f"ndcg_at_{k}": ndcg_at_k(retrieved_ids, expected_ids, k),
    }


@dataclass
class JudgmentBatch:
    samples: list[EvalSample] = field(default_factory=list)
    judgments: list[Judgment] = field(default_factory=list)

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "samples": [asdict(s) for s in self.samples],
                    "judgments": [asdict(j) for j in self.judgments],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> "JudgmentBatch":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            samples=[EvalSample(**s) for s in data["samples"]],
            judgments=[Judgment(**j) for j in data["judgments"]],
        )


def load_history_samples(
    history_path: Path,
    n: int,
    mode_filter: str = "learning",
    event_types: tuple[str, ...] = ("rag_ask",),
    seed: int = 0,
) -> list[EvalSample]:
    """Sample n events from history.jsonl filtered by mode + event_type."""
    candidates: list[EvalSample] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if mode_filter and ev.get("mode") not in (mode_filter, None):
            continue
        if ev.get("event_type") not in event_types:
            continue
        payload = ev.get("payload", {})
        query = payload.get("prompt") or payload.get("query") or ""
        if not query:
            continue
        hits = payload.get("hits") or []
        candidates.append(
            EvalSample(
                sample_id=ev.get("event_id", f"hist-{len(candidates)}"),
                query=query,
                expected_concept_ids=payload.get("expected_concept_ids", []),
                retrieved_hits=[
                    {"concept_id": h.get("concept_id", ""), "score": h.get("score", 0.0), "title": h.get("title", "")}
                    for h in hits
                ],
                answer_text=payload.get("answer_text", ""),
            )
        )
    if n >= len(candidates):
        return candidates
    rng = random.Random(seed)
    return rng.sample(candidates, n)
