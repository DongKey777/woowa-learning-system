from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.response_capture import (  # noqa: E402
    capture_from_hook_payload,
    create_pending_capture,
    is_internal_capture_meta_prompt,
    load_pending_capture,
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


def test_hook_capture_failure_queues_repair_without_blocking(tmp_path: Path) -> None:
    state = tmp_path / "state"
    result = capture_from_hook_payload({}, client="codex", state_root=state)

    assert result["ok"] is False
    assert result["reason"] == "body_missing"
    queue = state / "learner" / "capture-repair-queue.jsonl"
    assert queue.exists()
    row = json.loads(queue.read_text(encoding="utf-8"))
    assert row["reason"] == "body_missing"


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
