"""Phase N — second wave of uncovered scenarios.

Focus on integration, persistence, idempotency, edge cases that Phase M
didn't cover.

  N1  state persistence — daemon stop → start → mastery survives
  N2  3-turn deep dialogue + history fold-in correctness
  N3  bin/ask fallback path (daemon down → in-process)
  N4  long-prompt handling (2KB+)
  N5  mission-patterns-build idempotency (re-run → same output)
  N6  cross-crew-build idempotency
  N7  F11 trigger with no anchors for repo (graceful)
  N8  token budget enforcement (coaching ≤5500, F11 ≤15000)
  N9  coaching mode surfaces all 3 personas in markdown
  N10 emoji / special char prompt
  N11 history.jsonl tail seek correctness on large file
  N12 daemon ask + history append are atomic (no partial writes on error)
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path("/Users/idonghun/IdeaProjects/woowa-learning-system")
STATE = REPO_ROOT / "state"
LAST_RESTART_MASTERY_CHECK: dict | None = None

sys.path.insert(0, str(REPO_ROOT))


@dataclass
class ScenarioResult:
    name: str
    description: str
    observed: str
    passed: bool
    method: str
    elapsed_ms: float = 0.0
    details: dict = field(default_factory=dict)


def daemon_ask(prompt: str, repo: str | None = None,
               learner_id: str = "default", state_root: Path = STATE
               ) -> tuple[dict | None, float, str | None]:
    sock_path = str(state_root / "rag-daemon.sock")
    if not os.path.exists(sock_path):
        return None, 0.0, "daemon down"
    try:
        t0 = time.perf_counter()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(60)
        sock.connect(sock_path)
        req = {"action": "ask", "query": prompt, "learner_id": learner_id}
        if repo:
            req["repo"] = repo
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        return json.loads(data.decode("utf-8").strip()), (time.perf_counter() - t0) * 1000, None
    except Exception as e:
        return None, 0.0, f"{type(e).__name__}: {e}"[:120]


def restart_daemon(log_path: str) -> tuple[bool, dict]:
    subprocess.run(["bin/rag-daemon", "stop"], cwd=REPO_ROOT,
                   capture_output=True, text=True)
    res = subprocess.run(
        ["bin/rag-daemon", "start-bg", "--log-path", log_path,
         "--timeout-s", "90"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=95,
    )
    ok = res.returncode == 0 and "started" in res.stdout
    return ok, {"exit_code": res.returncode, "stdout": res.stdout.strip(),
                "stderr_preview": res.stderr[:200]}


def mastered_concepts() -> list[str]:
    conn = sqlite3.connect(STATE / "learner" / "mastery_graph.sqlite")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT concept_id FROM mastery WHERE bloom_level='mastered'"
    ).fetchall()
    conn.close()
    return [r["concept_id"] for r in rows]


# ── N1 state persistence ──────────────────────────────────────────────────

def n1_state_persistence() -> ScenarioResult:
    """Mastery in SQLite must survive daemon restart."""
    global LAST_RESTART_MASTERY_CHECK
    if LAST_RESTART_MASTERY_CHECK:
        mastered_before = LAST_RESTART_MASTERY_CHECK["before"]
        mastered_after = LAST_RESTART_MASTERY_CHECK["after"]
        restarted = LAST_RESTART_MASTERY_CHECK["restarted"]
        restart_details = LAST_RESTART_MASTERY_CHECK["restart_details"]
        method = "reuse N3 daemon restart evidence → compare mastery before/after"
    else:
        mastered_before = mastered_concepts()
        restarted, restart_details = restart_daemon("/tmp/daemon_n1.log")
        mastered_after = mastered_concepts()
        method = "dump mastery sqlite → restart daemon → re-dump → compare"

    survived = sorted(mastered_before) == sorted(mastered_after)
    return ScenarioResult(
        name="N1_state_persistence",
        description="mastery survives daemon stop→start",
        observed=f"{len(mastered_before)} mastered before, {len(mastered_after)} after, "
                 f"identical: {survived}, restarted: {restarted}",
        passed=restarted and survived and len(mastered_after) > 0,
        method=method,
        details={"mastered_before_n": len(mastered_before),
                 "mastered_after_n": len(mastered_after),
                 "restart": restart_details},
    )


# ── N2 3-turn deep dialogue + history fold-in ─────────────────────────────

def n2_three_turn_history_foldin() -> ScenarioResult:
    """Turn 1, 2, 3 sequential — turn 3's prompt should reference recent
    rag_ask events in 'Recent history' block."""
    r1, _, _ = daemon_ask("Bean DI 기본")
    r2, _, _ = daemon_ask("그럼 IoC 컨테이너는?")
    r3, _, _ = daemon_ask("두 개 차이가 뭐야")
    if not r3:
        return ScenarioResult("N2_three_turn_history", "—", "daemon down", False, "ask × 3")
    md3 = r3.get("markdown", "")
    has_recent_block = "Recent history" in md3 or "recent_history" in md3.lower()
    # Count rag_ask references in recent_history block
    rag_ask_refs = md3.count("rag_ask")
    return ScenarioResult(
        name="N2_three_turn_history",
        description="turn 3 sees recent rag_ask events in prompt",
        observed=f"recent_history block: {has_recent_block}, rag_ask refs in md: {rag_ask_refs}",
        passed=has_recent_block and rag_ask_refs >= 2,
        method="3 sequential asks; check turn-3 markdown contains recent rag_ask events",
        details={"has_recent_block": has_recent_block, "rag_ask_refs": rag_ask_refs},
    )


# ── N3 bin/ask fallback path ──────────────────────────────────────────────

def n3_fallback_no_daemon() -> ScenarioResult:
    """Stop daemon → bin/ask --no-daemon should still work (in-process)."""
    global LAST_RESTART_MASTERY_CHECK
    mastered_before = mastered_concepts()
    # Stop daemon.
    subprocess.run(["bin/rag-daemon", "stop"], cwd=REPO_ROOT,
                   capture_output=True, text=True)
    time.sleep(0.5)

    res = subprocess.run(
        ["python3", "bin/ask", "DI가 뭐야", "--no-daemon"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "WOOWA_SESSION_MODE": "development"},
    )

    # Restart daemon for subsequent scenarios. start-bg already waits for ping.
    restarted, restart_details = restart_daemon("/tmp/daemon_n3.log")
    mastered_after = mastered_concepts()
    LAST_RESTART_MASTERY_CHECK = {
        "before": mastered_before,
        "after": mastered_after,
        "restarted": restarted,
        "restart_details": restart_details,
    }

    ok = (res.returncode == 0 and len(res.stdout) > 500
          and "mode" in res.stdout.lower() and restarted)
    return ScenarioResult(
        name="N3_fallback_no_daemon",
        description="bin/ask --no-daemon in-process fallback works",
        observed=f"exit={res.returncode}, stdout_len={len(res.stdout)}, ok={ok}",
        passed=ok,
        method="daemon stop → bin/ask --no-daemon → check stdout produced",
        details={"exit_code": res.returncode, "stdout_chars": len(res.stdout),
                 "stderr_preview": res.stderr[:200],
                 "restart": restart_details},
    )


# ── N4 long-prompt handling ───────────────────────────────────────────────

def n4_long_prompt() -> ScenarioResult:
    """2KB prompt — verify no truncation crash, daemon still returns."""
    base = "Spring Bean lifecycle에서 BeanPostProcessor와 BeanFactoryPostProcessor의 차이는?"
    long_prompt = base + " " + ("코드 컨텍스트: " * 80)  # ~2KB
    r, ms, err = daemon_ask(long_prompt)
    return ScenarioResult(
        name="N4_long_prompt",
        description="2KB prompt → daemon returns without error",
        observed=f"len={len(long_prompt)} chars, daemon ok: {r is not None}, ms={ms:.0f}",
        passed=r is not None and not err,
        method="long prompt × daemon ask",
        details={"prompt_len": len(long_prompt), "err": err,
                 "response_mode": r.get("mode") if r else None},
    )


# ── N5 mission-patterns-build idempotency ─────────────────────────────────

def n5_mission_patterns_idempotency() -> ScenarioResult:
    """Two builds should produce identical patterns list."""
    pre_path = STATE / "repos" / "spring-roomescape-member" / "mission_patterns.json"
    if not pre_path.exists():
        return ScenarioResult("N5_mission_patterns_idempotency", "—",
                              "no pre-built file", False, "bin/mission-patterns-build × 2")
    before = json.loads(pre_path.read_text(encoding="utf-8"))
    res = subprocess.run(
        ["python3", "bin/mission-patterns-build", "--repo", "spring-roomescape-member"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    after = json.loads(pre_path.read_text(encoding="utf-8"))
    same_patterns = (
        len(before.get("patterns", [])) == len(after.get("patterns", []))
        and sorted(p.get("matched_concept_id", "") for p in before["patterns"])
            == sorted(p.get("matched_concept_id", "") for p in after["patterns"])
    )
    return ScenarioResult(
        name="N5_mission_patterns_idempotency",
        description="rebuild produces identical pattern set",
        observed=f"before={len(before['patterns'])}, after={len(after['patterns'])}, same={same_patterns}",
        passed=res.returncode == 0 and same_patterns,
        method="json compare patterns before/after re-run",
        details={"exit": res.returncode, "before_n": len(before["patterns"]),
                 "after_n": len(after["patterns"])},
    )


# ── N6 cross-crew-build idempotency ───────────────────────────────────────

def n6_cross_crew_idempotency() -> ScenarioResult:
    """Re-build cross_crew parquet → same row count + same anchor distribution."""
    import pyarrow.parquet as pq
    p = STATE / "repos" / "spring-roomescape-member" / "cross_crew_review_graph.parquet"
    if not p.exists():
        return ScenarioResult("N6_cross_crew_idempotency", "—", "no parquet",
                              False, "bin/cross-crew-build × 2")
    before = pq.read_table(p).to_pandas()
    res = subprocess.run(
        ["python3", "bin/cross-crew-build", "--repo", "spring-roomescape-member"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "WOOWA_SESSION_MODE": "development"},
    )
    after = pq.read_table(p).to_pandas()
    same = (len(before) == len(after)
            and before["anchor_thread_id"].nunique() == after["anchor_thread_id"].nunique())
    return ScenarioResult(
        name="N6_cross_crew_idempotency",
        description="cross-crew parquet rebuild → identical row+anchor count",
        observed=f"before={len(before)}, after={len(after)}, anchors_same={same}",
        passed=res.returncode == 0 and same,
        method="parquet compare row count + unique anchors before/after",
        details={"exit": res.returncode, "before_rows": len(before),
                 "after_rows": len(after)},
    )


# ── N7 F11 trigger with no anchors for repo ───────────────────────────────

def n7_f11_no_anchors_for_repo() -> ScenarioResult:
    """admin repo has 0 anchors (verified Phase L). F11 query → graceful."""
    r, _, err = daemon_ask("다른 크루는 어떻게 작성했어?", repo="spring-roomescape-admin")
    if err or not r:
        return ScenarioResult("N7_f11_no_anchors", "—", f"err: {err}", False, "ask")
    md = r.get("markdown", "")
    mode = r.get("mode")
    f11_mode = mode == "f11_anchor"
    has_response = len(md) > 500
    return ScenarioResult(
        name="N7_f11_no_anchors",
        description="F11 trigger on repo with no anchors → graceful",
        observed=f"mode={mode}, len={len(md)}, f11_mode_correct={f11_mode}",
        passed=f11_mode and has_response,
        method="F11 keyword + repo without anchors → daemon ask",
        details={"mode": mode, "markdown_len": len(md)},
    )


# ── N8 token budget enforcement ───────────────────────────────────────────

def n8_token_budget() -> ScenarioResult:
    """coaching ≤5500 tokens, F11 ≤12000. Approximate tokens = chars/4."""
    rc, _, _ = daemon_ask("내 ReservationController 리팩토링", repo="spring-roomescape-member")
    rf, _, _ = daemon_ask("다른 크루는 어떻게 작성했어?", repo="spring-roomescape-member")
    if not (rc and rf):
        return ScenarioResult("N8_token_budget", "—", "daemon down", False, "ask × 2")
    coaching_tokens = len(rc.get("markdown", "")) // 4
    f11_tokens = len(rf.get("markdown", "")) // 4
    coaching_budget = rc.get("budget", 0)
    f11_budget = rf.get("budget", 0)
    ok = coaching_tokens <= coaching_budget and f11_tokens <= f11_budget
    return ScenarioResult(
        name="N8_token_budget",
        description="markdown tokens within router-declared budget",
        observed=f"coaching {coaching_tokens}≤{coaching_budget}, F11 {f11_tokens}≤{f11_budget}",
        passed=ok,
        method="prompt char count // 4 vs router budget_tokens",
        details={"coaching_tokens_est": coaching_tokens, "coaching_budget": coaching_budget,
                 "f11_tokens_est": f11_tokens, "f11_budget": f11_budget},
    )


# ── N9 coaching surfaces all 3 personas ───────────────────────────────────

def n9_coaching_personas() -> ScenarioResult:
    """coaching mode should declare MENTOR + REVIEWER + SOCRATIC in markdown."""
    r, _, _ = daemon_ask("내 ReservationController 리팩토링", repo="spring-roomescape-member")
    if not r:
        return ScenarioResult("N9_coaching_personas", "—", "daemon down", False, "ask")
    md = r.get("markdown", "")
    has = {p: f"[{p}]" in md for p in ("MENTOR", "REVIEWER", "SOCRATIC")}
    all_three = all(has.values())
    return ScenarioResult(
        name="N9_coaching_personas",
        description="coaching markdown includes all 3 persona labels",
        observed=f"MENTOR={has['MENTOR']} REVIEWER={has['REVIEWER']} SOCRATIC={has['SOCRATIC']}",
        passed=all_three,
        method="grep [MENTOR]/[REVIEWER]/[SOCRATIC] in coaching prompt",
        details={"present": has},
    )


# ── N10 emoji / special char ──────────────────────────────────────────────

def n10_emoji_special_chars() -> ScenarioResult:
    """Emoji + special chars in prompt → daemon handles UTF-8 cleanly."""
    r, _, err = daemon_ask("@Transactional 좀 알려줘 🤔 (특수문자 < > & 포함!)")
    return ScenarioResult(
        name="N10_emoji_special_chars",
        description="emoji + special chars in prompt → daemon ok",
        observed=f"daemon ok: {r is not None}, err: {err}",
        passed=r is not None and not err,
        method="daemon ask with emoji + < > & special chars",
        details={"mode": r.get("mode") if r else None, "err": err},
    )


# ── N11 history.jsonl tail seek correctness ───────────────────────────────

def n11_history_tail_correctness() -> ScenarioResult:
    """read_history(tail=20) must return last 20 events, not random 20."""
    from core.state import read_history
    events = read_history(STATE, tail=20)
    if not events:
        return ScenarioResult("N11_history_tail", "—", "no history",
                              False, "read_history(tail=20)")
    # Verify ts is non-decreasing within returned events
    ts_list = [e.get("ts", 0) for e in events]
    monotonic = all(ts_list[i] <= ts_list[i + 1] for i in range(len(ts_list) - 1))
    # And read all (sample) to check the last 20 of all is what we got
    all_events = read_history(STATE)
    last_20_ids = [e.get("event_id") for e in all_events[-20:]]
    tail_ids = [e.get("event_id") for e in events]
    same = last_20_ids == tail_ids
    return ScenarioResult(
        name="N11_history_tail",
        description="read_history(tail=20) == last 20 of full history",
        observed=f"n={len(events)} ts_monotonic={monotonic} matches_full_tail={same}",
        passed=monotonic and same and len(events) == 20,
        method="tail=20 vs full[-20:] comparison",
        details={"tail_n": len(events), "monotonic": monotonic, "match": same},
    )


# ── N12 daemon ask + history append atomicity ─────────────────────────────

def n12_history_append_atomicity() -> ScenarioResult:
    """5 asks in a row → history grows by exactly 5, each event well-formed."""
    hist_path = STATE / "learner" / "history.jsonl"
    before_n = sum(1 for line in hist_path.read_text(encoding="utf-8").splitlines()
                   if line.strip())
    for i in range(5):
        daemon_ask(f"테스트 query {i}")
    after_n = sum(1 for line in hist_path.read_text(encoding="utf-8").splitlines()
                  if line.strip())
    grew_by_5 = (after_n - before_n) == 5

    # Check last 5 events are well-formed
    lines = hist_path.read_text(encoding="utf-8").splitlines()[-5:]
    wellformed = sum(1 for L in lines if json.loads(L).get("event_id", "").startswith("ask-"))
    return ScenarioResult(
        name="N12_history_atomicity",
        description="5 asks → 5 well-formed events in history.jsonl",
        observed=f"grew by {after_n - before_n}, wellformed={wellformed}/5",
        passed=grew_by_5 and wellformed == 5,
        method="line count before/after + JSON parse of last 5",
        details={"before": before_n, "after": after_n, "wellformed": wellformed},
    )


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    print("=== Phase N additional uncovered scenarios ===\n", flush=True)
    run_t0 = time.perf_counter()
    fns = [
        n2_three_turn_history_foldin,
        n4_long_prompt,
        n5_mission_patterns_idempotency,
        n7_f11_no_anchors_for_repo,
        n8_token_budget,
        n9_coaching_personas,
        n10_emoji_special_chars,
        n11_history_tail_correctness,
        n12_history_append_atomicity,
        n6_cross_crew_idempotency,  # slower, do near end
        n3_fallback_no_daemon,       # restarts daemon
        n1_state_persistence,        # restarts daemon (last)
    ]
    all_results: list[ScenarioResult] = []
    for fn in fns:
        t0 = time.perf_counter()
        try:
            r = fn()
        except Exception as e:
            r = ScenarioResult(
                name=fn.__name__,
                description="scenario raised an exception",
                observed=f"{type(e).__name__}: {e}",
                passed=False,
                method="exception guard",
                details={"error_type": type(e).__name__, "error": str(e)[:500]},
            )
        r.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        marker = "✅" if r.passed else "❌"
        print(f"  {marker} {r.name:<35} {r.observed[:80]}  ({r.elapsed_ms:.0f}ms)",
              flush=True)
        all_results.append(r)

    passed = sum(1 for r in all_results if r.passed)
    total_elapsed_ms = round((time.perf_counter() - run_t0) * 1000, 1)
    out = REPO_ROOT / "reports" / "phase_n_uncovered2.json"
    out.write_text(json.dumps({
        "metadata": {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "branch": "paradigm-v2",
                     "total_elapsed_ms": total_elapsed_ms},
        "scenarios": [asdict(r) for r in all_results],
        "passed_n": passed, "total_n": len(all_results),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out}\nPass: {passed}/{len(all_results)}")
    print(f"Elapsed: {total_elapsed_ms:.0f}ms")
    return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
