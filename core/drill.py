"""Drill offer generator + 4-dimension scoring (F6 completion).

Mechanism:
- build_offer_if_due(): picks an uncertain concept_id, generates a question
  from corpus.expected_queries[0] or learner_query_patterns[0], extracts
  expected_terms from concept body/aliases for scoring, persists pending
  state.
- score_pending_answer(): 4-dim grade (accuracy, depth, practicality,
  completeness) → total in [0, 1] → record_turn(drill_answer) which feeds
  Bloom autoloop via core/feedback.py.

State files:
  state/learner/drill_pending.json   one open offer awaiting answer
  state/learner/drill_due.json       spaced-repetition due list

No external LLM call — deterministic Python. The AI session presents the
question to the learner; learner replies; this module scores.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from core.feedback import record_turn
from core.state import DEFAULT_STATE_ROOT, append_history_event, atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "corpus" / "concepts"

PENDING_FILE = "drill_pending.json"
DUE_FILE = "drill_due.json"
OFFER_META_FILE = "drill_offer_meta.json"  # W8: last proactive offer epoch (freq cap)

# Spaced repetition bands (days). After scoring, schedule next review.
SPACED_BANDS_DAYS = {"weak": 1, "ok": 3, "good": 7, "mastered": 14}

# Dimension scoring weights — sum = 1.0
DIM_WEIGHTS = {
    "accuracy": 0.40,
    "depth": 0.25,
    "practicality": 0.20,
    "completeness": 0.15,
}

# Heuristic regex for "depth/completeness" structure markers
_STRUCTURE_KW = re.compile(
    r"(왜|언제|어떻게|예시|예제|구체|차이|장단점|이유|trade-?off|tradeoff|"
    r"because|when|how|example|use case)",
    re.IGNORECASE,
)
_CODE_BLOCK = re.compile(r"```|@[A-Z][a-zA-Z]+|\bclass\s+\w+|\bnew\s+\w+\(")


@dataclass(frozen=True)
class DrillOffer:
    drill_session_id: str
    concept_id: str
    question: str
    expected_terms: list[str]
    source: str  # "expected_queries[i]" / "learner_query_patterns[0]" / fallback
    created_at: float  # epoch; staleness/expiry is time-based via
    # core.state.DRILL_PENDING_TTL_SECS (W8: a pending older than 24h is unlinked
    # on load_pending_triggers, unblocking build_offer_if_due's one-open guard).


@dataclass(frozen=True)
class DrillScore:
    total: float  # 0..1
    level: str   # weak/ok/good/mastered
    dimensions: dict   # {accuracy, depth, practicality, completeness} each 0..10
    matched_terms: list[str]
    improvement_notes: str


def _pending_path(state_root: Path) -> Path:
    return state_root / "learner" / PENDING_FILE


def _due_path(state_root: Path) -> Path:
    return state_root / "learner" / DUE_FILE


def _load_concept(concept_id: str) -> dict | None:
    """Locate corpus JSON for the given concept_id (cat/slug → cat/slug.json)."""
    parts = concept_id.split("/", 1)
    if len(parts) != 2:
        return None
    cat, slug = parts
    p = CORPUS_DIR / cat / f"{slug}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _build_expected_terms(concept: dict, n: int = 8) -> list[str]:
    """Derive scoring expected_terms from concept title + aliases + body."""
    terms: list[str] = []
    title = concept.get("title", "")
    if title:
        # Title words ≥3 chars
        terms.extend(w for w in re.findall(r"[가-힣A-Za-z]+", title) if len(w) >= 3)
    for a in concept.get("aliases", []) or []:
        if isinstance(a, str) and len(a) >= 3:
            terms.append(a)
    # Top-frequency words from summary
    summary = concept.get("summary", "") or ""
    words = re.findall(r"[가-힣]+|[A-Za-z]{4,}", summary)
    # Dedupe preserving order
    seen = set()
    out = []
    for t in terms + words:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(t)
    return out[:n]


def _learner_query_pattern_text(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        pattern = entry.get("pattern")
        if isinstance(pattern, str):
            return pattern
    return None


def read_last_proactive_offer_at(state_root: Path = DEFAULT_STATE_ROOT) -> float | None:
    """W8: epoch of the last proactive drill offer (frequency-cap marker)."""
    p = state_root / "learner" / OFFER_META_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return float(data.get("last_proactive_drill_offer_at"))
    except (json.JSONDecodeError, TypeError, ValueError, OSError):
        return None


def write_last_proactive_offer_at(ts: float, state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """W8: record the last proactive offer epoch for the cooldown gate."""
    d = state_root / "learner"
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / OFFER_META_FILE, {"last_proactive_drill_offer_at": float(ts)})


def build_offer_if_due(
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
    uncertain_concept_ids: Iterable[str] = (),
    now: float | None = None,
) -> DrillOffer | None:
    """Generate a drill offer if (a) no pending offer exists AND (b) at least
    one uncertain concept can produce a non-stub question.

    Persists pending file with offer details; AI session renders question
    to learner. Subsequent learner reply is handled by score_pending_answer.
    """
    state_dir = state_root / "learner"
    state_dir.mkdir(parents=True, exist_ok=True)
    pending = _pending_path(state_root)
    if pending.exists():
        return None  # one open at a time

    candidates = [cid for cid in uncertain_concept_ids if cid]
    if not candidates:
        return None

    for cid in candidates:
        concept = _load_concept(cid)
        if not concept:
            continue
        # Prefer the first substantive expected_query (len>=10); fall back to
        # learner_query_patterns[0]. EQ[0] is often a short "<term>이/가 뭐야?"
        # that doubles as the exact-shortcut surface but is too thin to drill on
        # — scan past it rather than dropping the concept (latent drill-break).
        eq = concept.get("expected_queries") or []
        lqp = concept.get("learner_query_patterns") or []
        source = None
        question = None
        for idx, candidate in enumerate(eq):
            if isinstance(candidate, str) and len(candidate) >= 10:
                question = candidate
                source = f"expected_queries[{idx}]"
                break
        if not question and lqp:
            pattern = _learner_query_pattern_text(lqp[0])
            if pattern and len(pattern) >= 10:
                question = pattern
                source = "learner_query_patterns[0]"
        if not question or source is None:
            continue
        if not question.rstrip().endswith(("?", "?")):
            question = question.rstrip() + "?"

        offer = DrillOffer(
            drill_session_id=f"drill-{int((now or time.time()) * 1000)}",
            concept_id=cid,
            question=question,
            expected_terms=_build_expected_terms(concept),
            source=source,
            created_at=now or time.time(),
        )
        atomic_write_json(pending, offer.__dict__)
        return offer
    return None


def _score_dimensions(answer: str, expected_terms: list[str]) -> tuple[dict, list[str]]:
    """Return (dim_dict, matched_terms_list). Each dimension is 0..10."""
    ans_lower = answer.lower()
    # Accuracy — how many expected_terms appear (case-insensitive)
    matched = [t for t in expected_terms if t.lower() in ans_lower]
    accuracy = (len(matched) / max(1, len(expected_terms))) * 10 if expected_terms else 0

    # Depth — length-based saturating at 400 chars
    depth = min(10.0, len(answer) / 40)

    # Practicality — code block or annotation/method-call hints present
    practicality = 10.0 if _CODE_BLOCK.search(answer) else (
        7.0 if "예" in answer or "example" in ans_lower else 4.0
    )

    # Completeness — structure markers (왜/언제/어떻게 etc.) + length
    structure_count = len(_STRUCTURE_KW.findall(answer))
    completeness = min(10.0, structure_count * 2.5 + (3.0 if len(answer) > 200 else 0))

    return {
        "accuracy": round(accuracy, 2),
        "depth": round(depth, 2),
        "practicality": round(practicality, 2),
        "completeness": round(completeness, 2),
    }, matched


def _grade_level(total: float) -> str:
    """Map total [0,1] to {weak, ok, good, mastered}."""
    if total >= 0.80:
        return "mastered"
    if total >= 0.65:
        return "good"
    if total >= 0.45:
        return "ok"
    return "weak"


def _improvement_notes(dims: dict, matched_terms: list[str],
                       expected_terms: list[str]) -> str:
    weak_axes = [k for k, v in dims.items() if v < 5.0]
    notes = []
    if "accuracy" in weak_axes:
        missing = [t for t in expected_terms if t not in matched_terms][:3]
        if missing:
            notes.append(f"핵심 키워드 빠짐: {', '.join(missing)}")
    if "depth" in weak_axes:
        notes.append("답변이 짧음 — 핵심을 한두 문장 더 풀어줘")
    if "practicality" in weak_axes:
        notes.append("코드 예시나 구체 시나리오 추가하면 좋겠어")
    if "completeness" in weak_axes:
        notes.append("'왜/언제/어떻게' 같은 맥락 부분이 빠진 듯")
    return " · ".join(notes) if notes else "전반적으로 균형 잡힘"


def score_pending_answer(
    answer: str,
    state_root: Path = DEFAULT_STATE_ROOT,
    now: float | None = None,
) -> DrillScore | None:
    """Read pending offer → grade learner answer → clear pending → emit
    drill_answer event so feedback.record_turn picks it up."""
    pending = _pending_path(state_root)
    if not pending.exists():
        return None
    offer_d = json.loads(pending.read_text(encoding="utf-8"))
    expected_terms = offer_d.get("expected_terms", [])
    dims, matched = _score_dimensions(answer, expected_terms)
    weighted_total = sum(dims[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS) / 10.0
    level = _grade_level(weighted_total)

    score = DrillScore(
        total=round(weighted_total, 3),
        level=level,
        dimensions=dims,
        matched_terms=matched,
        improvement_notes=_improvement_notes(dims, matched, expected_terms),
    )

    # Emit drill_answer event → feedback.record_turn → mastery autoloop
    event = {
        "event_id": f"drill-ans-{int((now or time.time()) * 1000)}",
        "ts": now or time.time(),
        "event_type": "drill_answer",
        "mode": "learning",
        "payload": {
            "drill_session_id": offer_d.get("drill_session_id"),
            "concept_ids": [offer_d.get("concept_id")],
            "score": score.total,
            "level": score.level,
            "dimensions": dims,
            "answer_preview": answer[:200],
        },
    }
    append_history_event(event, state_root=state_root)
    record_turn(event, state_root=state_root)

    # Schedule next review band
    days = SPACED_BANDS_DAYS.get(level, 3)
    due_ts = (now or time.time()) + days * 86400
    due_path = _due_path(state_root)
    due_list = []
    if due_path.exists():
        try:
            due_list = json.loads(due_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            due_list = []
    # Upsert by concept: drop any prior due entry for this concept before adding
    # the new band. Append-only accumulated a fresh row every review while the old
    # past-due row stayed, so the same concept was re-offered every cycle and the
    # due list grew without bound (spaced-repetition band delay was ignored).
    cid = offer_d.get("concept_id")
    due_list = [d for d in due_list if d.get("concept_id") != cid]
    due_list.append({
        "concept_id": cid,
        "level": level,
        "next_due_ts": due_ts,
        "last_score": score.total,
    })
    atomic_write_json(due_path, due_list)

    pending.unlink()
    return score


def build_review_offer_if_due(
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
    now: float | None = None,
) -> DrillOffer | None:
    """Promote a spaced-repetition due item into a fresh drill offer."""
    due_path = _due_path(state_root)
    if not due_path.exists():
        return None
    try:
        due_list = json.loads(due_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    ts_now = now or time.time()
    due = [d for d in due_list if d.get("next_due_ts", 0) <= ts_now]
    if not due:
        return None
    cid = due[0]["concept_id"]
    return build_offer_if_due(
        learner_id=learner_id, state_root=state_root,
        uncertain_concept_ids=[cid], now=now,
    )
