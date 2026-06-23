from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.response_capture import (  # noqa: E402
    append_repair_queue,
    capture_from_hook_payload,
    create_pending_capture,
    drain_repair_queue,
    expire_stale_pending_captures,
    is_internal_capture_meta_prompt,
    latest_pending_capture,
    load_pending_capture,
    pending_capture_dir,
    repair_queue_path,
    sync_pending_captures_from_quality,
)
from core.state import append_history_event  # noqa: E402


def _ask_event() -> dict:
    return {
        "event_id": "ask-hook",
        "event_type": "rag_ask",
        "mode": "learning",
        "learner_id": "DongKey777",
        "repo": "spring-roomescape-waiting",
        "payload": {
            "prompt": "격리 수준 설명",
            "repo": "spring-roomescape-waiting",
            "router_mode": "cs_qa",
            "top_concept_ids": ["database/transaction-isolation-locking"],
        },
    }


def test_internal_source_id_prompt_is_detected() -> None:
    assert is_internal_capture_meta_prompt("이 답변에 대한 ask source id 받기")
    assert not is_internal_capture_meta_prompt("트랜잭션 격리 수준 설명")


def test_hook_capture_writes_quality_and_marks_pending(tmp_path: Path) -> None:
    state = tmp_path / "state"
    event = _ask_event()
    append_history_event(event, state_root=state)
    create_pending_capture(event, state_root=state)
    body = (
        "[Mode: cs_qa]\n\n"
        "격리 수준은 다른 트랜잭션의 변경을 어디까지 볼지 정하는 약속이야.\n\n"
        "참고:\n"
        "- database/transaction-isolation-locking\n"
    )

    result = capture_from_hook_payload(
        {"last_assistant_message": body},
        client="claude",
        state_root=state,
        learner_id="DongKey777",
    )

    assert result["ok"] is True
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["source_event_id"] == "ask-hook"
    assert row["capture_method"] == "hook_claude"
    assert row["repo"] == "spring-roomescape-waiting"
    assert row["citation_paths_expected"] == ["database/transaction-isolation-locking"]
    pending = load_pending_capture("ask-hook", state_root=state)
    assert pending is not None
    assert pending["status"] == "captured"
    assert pending["response_body_path"] == row["response_body_path"]


def test_hook_capture_inherits_mode_from_source_event(monkeypatch, tmp_path: Path) -> None:
    # The Stop-hook env must NOT override the source rag_ask event's mode or
    # provenance — the rag_ask event is the single source of truth. Here env
    # says development, but the captured row must stay learning/explicit.
    monkeypatch.setenv("WOOWA_SESSION_MODE", "development")
    state = tmp_path / "state"
    event = _ask_event()
    event["mode_source"] = "explicit"
    append_history_event(event, state_root=state)
    create_pending_capture(event, state_root=state)

    result = capture_from_hook_payload(
        {"last_assistant_message": "[Mode: cs_qa]\n\n격리 수준 답변"},
        client="claude",
        state_root=state,
        learner_id="DongKey777",
    )

    assert result["ok"] is True
    row = json.loads((state / "learner" / "response-quality.jsonl").read_text())
    assert row["mode"] == "learning"
    assert row["mode_source"] == "explicit"


def test_hook_capture_supersedes_older_pending_in_same_turn(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = _ask_event()
    first["event_id"] = "ask-first"
    first["payload"]["prompt"] = "학습 시작"
    second = _ask_event()
    second["event_id"] = "ask-second"
    second["payload"]["prompt"] = "학습 시작 구체화"
    append_history_event(first, state_root=state)
    create_pending_capture(first, state_root=state)
    append_history_event(second, state_root=state)
    create_pending_capture(second, state_root=state)

    result = capture_from_hook_payload(
        {"last_assistant_message": "[Mode: cs_qa]\n\n최종 답변"},
        client="claude",
        state_root=state,
        learner_id="DongKey777",
    )

    assert result["ok"] is True
    assert result["source_event_id"] == "ask-second"
    assert result["superseded_pending_n"] == 1
    first_pending = load_pending_capture("ask-first", state_root=state)
    assert first_pending is not None
    assert first_pending["status"] == "superseded_by_later_capture"
    assert first_pending["superseded_by"] == "ask-second"


def test_hook_capture_failure_queues_repair_without_blocking(tmp_path: Path) -> None:
    state = tmp_path / "state"
    result = capture_from_hook_payload({}, client="codex", state_root=state)

    assert result["ok"] is False
    assert result["reason"] == "body_missing"
    assert result["ignored"] is True
    queue = state / "learner" / "capture-repair-queue.jsonl"
    assert queue.exists()
    row = json.loads(queue.read_text(encoding="utf-8"))
    assert row["reason"] == "body_missing"
    assert row["status"] == "ignored_non_learning"


def test_progress_hook_message_without_pending_is_ignored(tmp_path: Path) -> None:
    state = tmp_path / "state"
    result = capture_from_hook_payload(
        {"last_assistant_message": "sync-prs 백그라운드 실행 중."},
        client="claude",
        state_root=state,
    )

    assert result["ok"] is True
    assert result["ignored"] is True
    queue = state / "learner" / "capture-repair-queue.jsonl"
    row = json.loads(queue.read_text(encoding="utf-8"))
    assert row["status"] == "ignored_non_learning"
    assert row["reason"] == "non_learning_hook_message"
    assert not (state / "learner" / "capture-repair-bodies").exists()


def test_successful_capture_drains_repairable_queue_entry(tmp_path: Path) -> None:
    """A learning capture should opportunistically repair a stale queue row
    whose source_event_id is already in history (was a race-time miss)."""
    state = tmp_path / "state"

    # 1) historic ask that previously failed to capture — body sits in queue
    stale = _ask_event()
    stale["event_id"] = "ask-stale"
    stale["payload"]["prompt"] = "예전 미스 캡쳐"
    append_history_event(stale, state_root=state)
    stale_body = (
        "[Mode: cs_qa]\n\n"
        "예전 답변 본문.\n\n"
        "참고:\n"
        "- database/transaction-isolation-locking\n"
    )
    append_repair_queue(
        "pending_missing",
        state_root=state,
        source_event_id="ask-stale",
        client="claude",
        response_body=stale_body,
    )

    # 2) new ask comes in and the hook successfully captures it
    fresh = _ask_event()
    fresh["event_id"] = "ask-fresh"
    fresh["payload"]["prompt"] = "새 질문"
    append_history_event(fresh, state_root=state)
    create_pending_capture(fresh, state_root=state)
    fresh_body = (
        "[Mode: cs_qa]\n\n"
        "새 답변 본문.\n\n"
        "참고:\n"
        "- database/transaction-isolation-locking\n"
    )
    result = capture_from_hook_payload(
        {"last_assistant_message": fresh_body, "source_event_id": "ask-fresh"},
        client="claude",
        state_root=state,
        learner_id="DongKey777",
    )

    # fresh capture succeeds AND the stale queue row gets repaired in the same call.
    # pending_missing queue rows have no pending-capture file by definition (that's
    # why they were queued); drain only needs to record response-quality telemetry.
    assert result["ok"] is True
    assert result["drained_repaired_n"] == 1
    quality = [
        json.loads(line)
        for line in (state / "learner" / "response-quality.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert {q["source_event_id"] for q in quality} == {"ask-stale", "ask-fresh"}
    # And drain is idempotent — re-running finds nothing new to repair.
    rerun = drain_repair_queue(state_root=state, learner_id="DongKey777")
    assert rerun["applied"] == 0


def test_drain_is_idempotent_when_nothing_repairable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "learner").mkdir(parents=True)
    # empty queue → drain returns zeros, no file created beyond what exists
    summary = drain_repair_queue(state_root=state, learner_id="DongKey777")
    assert summary == {"scanned": 0, "repairable": 0, "applied": 0, "unrecoverable": 0, "drain_errors": 0}
    assert not repair_queue_path(state).exists()


def test_sync_pending_captures_from_quality_marks_stale_pending(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    learner = state / "learner"
    pending_dir = learner / "pending-captures"
    pending_dir.mkdir(parents=True)
    (pending_dir / "ask-synced.json").write_text(json.dumps({
        "schema_id": "response-capture-pending-v1",
        "source_event_id": "ask-synced",
        "repo": "spring-roomescape-waiting",
        "prompt": "테스트",
        "status": "pending",
        "created_at": 1.0,
        "updated_at": 1.0,
        "attempts": 0,
    }, ensure_ascii=False), encoding="utf-8")
    (learner / "response-quality.jsonl").write_text(json.dumps({
        "source_event_id": "ask-synced",
        "logged_at": 2.0,
        "response_body_path": "learner/response-bodies/sha256/aa/aa.md",
        "quality_flags": [],
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = sync_pending_captures_from_quality(state_root=state)

    assert summary["synced"] == 1
    pending = load_pending_capture("ask-synced", state_root=state)
    assert pending is not None
    assert pending["status"] == "captured"
    assert pending["response_body_path"] == "learner/response-bodies/sha256/aa/aa.md"
    assert pending["synced_from_response_quality"] is True


def test_learning_data_clean_hard_deletes_orphans(tmp_path: Path) -> None:
    state = tmp_path / "state"
    learner = state / "learner"
    learner.mkdir(parents=True)
    body = learner / "response-bodies" / "sha256" / "aa" / "aa.md"
    body.parent.mkdir(parents=True)
    body.write_text("[Mode: cs_qa]\n\norphan", encoding="utf-8")
    (learner / "history.jsonl").write_text(
        "\n".join([
            json.dumps({
                "event_id": "ask-ok",
                "event_type": "rag_ask",
                "mode": "learning",
                "learner_id": "DongKey777",
                "payload": {"prompt": "락 설명", "router_mode": "cs_qa"},
            }, ensure_ascii=False),
            json.dumps({
                "event_id": "ask-meta",
                "event_type": "rag_ask",
                "mode": "learning",
                "learner_id": "DongKey777",
                "payload": {"prompt": "이 답변에 대한 ask source id 받기", "router_mode": "tier_0_fallback"},
            }, ensure_ascii=False),
        ]) + "\n",
        encoding="utf-8",
    )
    (learner / "response-quality.jsonl").write_text(json.dumps({
        "source_event_id": "ask-ghost-PARTIAL",
        "mode": "learning",
        "response_body_path": "learner/response-bodies/sha256/aa/aa.md",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "bin/learning-data-clean",
            "--state-root", str(state),
            "--learner-id", "DongKey777",
            "--hard-delete-contamination",
            "--apply",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["history_removed_n"] == 1
    assert out["response_quality_removed_n"] == 1
    assert not body.exists()
    remaining_history = (learner / "history.jsonl").read_text(encoding="utf-8")
    assert "ask-ok" in remaining_history
    assert "ask-meta" not in remaining_history


def _write_pending(state: Path, sid: str, created_at: float, status: str = "pending") -> None:
    pending_dir = pending_capture_dir(state)
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{sid}.json").write_text(json.dumps({
        "schema_id": "response-capture-pending-v1",
        "source_event_id": sid,
        "repo": "spring-roomescape-waiting",
        "prompt": "테스트",
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "attempts": 0,
    }, ensure_ascii=False), encoding="utf-8")


def test_age_cap_excludes_stale_pending_from_latest_lookup(tmp_path: Path) -> None:
    # A pending older than the age cap is a stale orphan — latest_pending_capture
    # must not return it, so a much later answer can't attach to it.
    state = tmp_path / "state"
    _write_pending(state, "ask-stale", created_at=time.time() - 5000)
    assert latest_pending_capture(state_root=state) is None
    # ...but with the cap disabled the stale row is still reachable.
    assert latest_pending_capture(state_root=state, max_age_s=None) is not None


def test_modeb_narration_does_not_pollute_stale_learning_pending(tmp_path: Path) -> None:
    # The pollution case: a genuine learning pending sat open (the learner answer
    # never came), then a later Mode-B narration Stop message arrives. The stale
    # pending must be expired (not absorb the narration), and the narration is
    # routed to the repair queue as non-learning rather than captured.
    state = tmp_path / "state"
    _write_pending(state, "ask-orphan", created_at=time.time() - 5000)
    result = capture_from_hook_payload(
        {"last_assistant_message": "## 검증 완료\n\npytest 745/745 passed, 커밋·푸시 끝."},
        client="claude",
        state_root=state,
    )
    assert result["ok"] is True
    assert result["ignored"] is True  # not attached to the stale pending
    orphan = load_pending_capture("ask-orphan", state_root=state)
    assert orphan is not None
    assert orphan["status"] == "expired_orphan"  # GC closed it, observable
    assert not (state / "learner" / "response-quality.jsonl").exists()


def test_answer_attaches_to_fresh_pending_not_stale_orphan(tmp_path: Path) -> None:
    # With both a stale orphan and a fresh pending open, a fresh learner answer
    # must attach to the fresh one — not the older orphan (the mis-attribution
    # that inflated capture latency to hours).
    state = tmp_path / "state"
    _write_pending(state, "ask-old-orphan", created_at=time.time() - 5000)
    fresh = _ask_event()
    fresh["event_id"] = "ask-fresh"
    append_history_event(fresh, state_root=state)
    create_pending_capture(fresh, state_root=state)

    result = capture_from_hook_payload(
        {"last_assistant_message": "[Mode: cs_qa]\n\n격리 수준 답변\n\n참고:\n- database/transaction-isolation-locking\n"},
        client="claude",
        state_root=state,
        learner_id="DongKey777",
    )
    assert result["ok"] is True
    assert result["source_event_id"] == "ask-fresh"
    assert load_pending_capture("ask-fresh", state_root=state)["status"] == "captured"
    assert load_pending_capture("ask-old-orphan", state_root=state)["status"] == "expired_orphan"


def test_expire_stale_pending_captures_marks_only_old(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _write_pending(state, "ask-old", created_at=time.time() - 5000)
    _write_pending(state, "ask-recent", created_at=time.time() - 60)
    n = expire_stale_pending_captures(state_root=state)
    assert n == 1
    assert load_pending_capture("ask-old", state_root=state)["status"] == "expired_orphan"
    assert load_pending_capture("ask-recent", state_root=state)["status"] == "pending"


def test_drain_scans_repairable_beyond_last_limit_lines(tmp_path: Path) -> None:
    # Regression: drain used to scan only the last `limit` queue lines, stranding
    # repairable rows older than that window. The repairable row is enqueued first,
    # then pushed past the window by filler rows; drain must still find + apply it.
    state = tmp_path / "state"
    early = _ask_event()
    early["event_id"] = "ask-early-repairable"
    append_history_event(early, state_root=state)
    append_repair_queue(
        "pending_missing",
        state_root=state,
        source_event_id="ask-early-repairable",
        client="claude",
        response_body="[Mode: cs_qa]\n\n예전 본문\n\n참고:\n- database/transaction-isolation-locking\n",
    )
    queue_path = repair_queue_path(state)
    for i in range(5):  # filler non-repairable rows push the early row out of a small window
        with queue_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"reason": "body_missing", "status": "ignored_non_learning", "i": i}) + "\n")

    summary = drain_repair_queue(state_root=state, learner_id="DongKey777", limit=2)
    assert summary["applied"] == 1  # found despite being beyond the last 2 lines
    quality = (state / "learner" / "response-quality.jsonl").read_text(encoding="utf-8")
    assert "ask-early-repairable" in quality


def test_non_learning_body_does_not_overwrite_fresh_learning_pending(tmp_path: Path) -> None:
    # A fresh learning pending is open; a non-learning Stop message (no source_event_id,
    # inside the age-cap window) attaches via recency only — it must NOT overwrite the
    # real answer. The body-shape guard previously ran only when no pending was found.
    state = tmp_path / "state"
    event = _ask_event()
    append_history_event(event, state_root=state)
    create_pending_capture(event, state_root=state)
    result = capture_from_hook_payload(
        {"last_assistant_message": "네, 시스템 작업 완료했습니다. 빌드 통과."},
        client="claude", state_root=state,
    )
    assert result["ignored"] is True
    assert result["reason"] == "non_learning_hook_message"
    assert load_pending_capture("ask-hook", state_root=state)["status"] == "pending"
    assert not (state / "learner" / "response-quality.jsonl").exists()


def test_learning_body_with_preamble_attaches_to_fresh_pending(tmp_path: Path) -> None:
    # A genuine answer with a short conversational preamble before [Mode: must still
    # attach (the broadened guard accepts [Mode: within the first 400 chars).
    state = tmp_path / "state"
    event = _ask_event()
    append_history_event(event, state_root=state)
    create_pending_capture(event, state_root=state)
    body = ("읽었어요. 정리할게요.\n\n[Mode: cs_qa]\n\n격리 수준 설명\n\n"
            "참고:\n- database/transaction-isolation-locking\n")
    result = capture_from_hook_payload(
        {"last_assistant_message": body}, client="claude", state_root=state,
        learner_id="DongKey777")
    assert result["ok"] is True and result.get("ignored") is not True
    assert load_pending_capture("ask-hook", state_root=state)["status"] == "captured"
