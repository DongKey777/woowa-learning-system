# Phase N — second wave of uncovered scenarios (all pass + 1 bug fix)

**Date**: 2026-05-25 (post Phase M)
**Branch**: `paradigm-v2`

---

## Results — 12/12 pass

| # | Scenario | Result | Notes |
|---|---|---|---|
| N1 | State persistence (daemon restart) | ✅ | 5 mastered survive stop/start; SQLite durable |
| N2 | 3-turn deep dialogue + history fold-in | ✅ | turn 3 sees 20 rag_ask refs in recent_history |
| N3 | bin/ask --no-daemon fallback | ✅ | in-process path produces 2807-char output |
| N4 | Long prompt (794 chars) | ✅ | daemon returns in 216ms, no truncation |
| N5 | mission-patterns-build idempotency | ✅ | re-run → identical 121 patterns |
| N6 | cross-crew-build idempotency | ✅ | re-run → identical 95 rows / anchors |
| N7 | F11 trigger on repo with no anchors | ✅ | admin repo (0 anchors) → f11_anchor mode + 1757-char graceful response |
| N8 | Token budget enforcement | ✅ | coaching 823≤5500, F11 621≤12000 — well under cap |
| N9 | Coaching surfaces all 3 personas | ✅ | MENTOR + REVIEWER + SOCRATIC all present |
| N10 | Emoji + special chars in prompt | ✅ | UTF-8 + < > & emoji handled cleanly |
| N11 | history.jsonl tail seek correctness | ✅ | (after fix) tail=20 == full[-20:] |
| N12 | Daemon ask + history append atomicity | ✅ | 5 asks → 5 well-formed events appended |

---

## 🐛 Bug found + fixed (N11)

**Symptom**: `core.state.read_history(tail=20)` returned only **18 events**, and content didn't match `full[-20:]`.

**Root cause**: chunk size estimate was 500 bytes/event. Real distribution (last 100 events): avg 523B, max 2274B. With tail=20, chunk = max(20×500, 4096) = 10000 bytes — insufficient to contain 20 complete events when some are 2KB+. The leading-line-drop heuristic then deletes a valid line too.

**Fix** (`core/state.py`):
```python
# Iterative chunk grow: 1500 → 3000 → 6000 → 12000 bytes/event
bytes_per_event = 1500
for attempt in range(4):
    chunk = min(size, max(tail * bytes_per_event, 4096))
    ...
    valid_lines = [L for L in raw_lines if L.strip()]
    if len(valid_lines) >= tail or chunk >= size:
        break
    bytes_per_event *= 2
```

**Verified**: `read_history(tail=20)` now returns exactly 20 events and matches `full[-20:]`. Daemon restart picks up fix.

**Impact**: prompt's "Recent history" block was missing 2/20 events for the past unknown duration → personalization signals slightly weaker than intended. No data loss; just rendering window was short. Fix restores correct surface to coach prompt.

---

## Notable findings

### N2 3-turn dialogue — history fold-in works
Turn 3 markdown contains **20 rag_ask references** in the recent_history block. The daemon-side `append_history_event` + `read_history(tail=20)` integration (Phase K fix) is producing rich context for the AI session.

### N3 bin/ask fallback — in-process path is 30s cold
With daemon stopped, `bin/ask --no-daemon` loads BGE-M3 in-process and produces correct output (2807 chars). Cold load takes ~30s due to model load. Acceptable for debug/CI; daemon path remains the daily-use default.

### N5/N6 idempotency — re-runs are safe
Both `bin/mission-patterns-build` and `bin/cross-crew-build` are deterministic. Re-running produces identical artifact counts and anchor distribution. Safe to incorporate into CI or daily refresh hooks.

### N8 token budget — well under cap
- coaching mode uses **~15%** of 5500 budget (823 tokens)
- F11 mode uses **~5%** of 12000 budget (621 tokens)
- Plenty of headroom for richer artifact inclusion if needed

### N9 all 3 personas surface in coaching
MENTOR + REVIEWER + SOCRATIC labels all present in coaching markdown. Multi-agent single-call composition working as designed (plan §D-B).

### N12 history.jsonl atomicity
Sequential 5 asks → exactly 5 appends, all well-formed JSON with `event_id` starting `ask-`. No partial writes, no race condition between concurrent asks (single-thread daemon).

---

## Reproduction

```bash
WOOWA_SESSION_MODE=development python3 tests/benchmarks/uncovered_scenarios_phase_n.py
```

Output:
- `reports/phase_n_uncovered2.json`
- This document

---

## Cumulative scenario coverage (all phases)

| Phase | Scenarios | Pass | Method |
|---|---|---|---|
| J | 14 mode dispatch + 4 deepdive | 18/18 | router classification + side-by-side legacy |
| K | F1 RAG quality + F5 mastery | 2/2 | 200 stratified queries + sqlite inspect |
| L | 9 plan gates | 9/9 | per-feature target check |
| M | 12 uncovered scenarios | 12/12 | edge cases, latency, isolation, injection |
| N | 12 second-wave uncovered | 12/12 | persistence, idempotency, integration |
| **Total** | **49 scenarios** | **49/49** | — |

Plan §verification F7 + F10 gap remain longitudinal-only (14-30d learner data).
