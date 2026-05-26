"""Phase Y11 PR-E (P1.4) — bin/learn-event drill_answer early branch.

core.drill.score_pending_answer는 이미 (1) pending offer 직접 read,
(2) drill_answer 이벤트 자체 emit, (3) record_turn, (4) pending clear까지
수행한다. bin/learn-event가 일반 append 흐름을 또 돌리면 drill_answer 이벤트가
중복 append되므로 *early branch + early return* 필수.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEARN_EVENT = REPO_ROOT / "bin" / "learn-event"


def _run(*args, state_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LEARN_EVENT), *args,
         "--state-root", str(state_root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _seed_drill_offer(state_root: Path, expected_terms: list[str]):
    """Stage a pending drill — mirrors what core.drill.build_offer_if_due saves."""
    learner_dir = state_root / "learner"
    learner_dir.mkdir(parents=True, exist_ok=True)
    offer = {
        "concept_id": "spring/di-basics",
        "question": "DI가 뭐야?",
        "expected_terms": expected_terms,
        "source": "test",
        "issued_at": 1700000000.0,
    }
    (learner_dir / "drill_pending.json").write_text(
        json.dumps(offer, ensure_ascii=False), encoding="utf-8"
    )


def _count_drill_events(state_root: Path) -> int:
    history = state_root / "learner" / "history.jsonl"
    if not history.exists():
        return 0
    n = 0
    for line in history.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("event_type") == "drill_answer":
            n += 1
    return n


def test_drill_answer_auto_score_appends_exactly_one_event(tmp_path):
    """drill_answer + --answer (free text) → score_pending_answer 자동 호출 →
    history.jsonl에 drill_answer 이벤트 정확히 +1만 추가 (중복 X)."""
    _seed_drill_offer(tmp_path, expected_terms=["주입", "container", "의존성"])
    before = _count_drill_events(tmp_path)

    result = _run(
        "--event-type", "drill_answer",
        "--answer", "DI는 의존성 주입이고 Spring container가 bean을 주입한다",
        "--silent",
        state_root=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    after = _count_drill_events(tmp_path)
    assert after - before == 1, (
        f"expected exactly +1 drill_answer event, got +{after - before}"
    )
    # pending should be cleared after scoring
    pending = tmp_path / "learner" / "drill_pending.json"
    assert not pending.exists() or pending.read_text().strip() == ""


def test_drill_answer_no_pending_returns_error(tmp_path):
    """pending drill 없는데 --answer 자동 채점 시도 → ERROR exit 2."""
    result = _run(
        "--event-type", "drill_answer",
        "--answer", "answer text",
        state_root=tmp_path,
    )
    assert result.returncode == 2
    assert "no pending drill" in result.stderr


def test_manual_drill_concept_score_keeps_legacy_path(tmp_path):
    """수동 모드 (--drill-concept + --drill-score) 는 early branch 안 타고
    일반 append 흐름 유지. 기존 자동화 호환."""
    result = _run(
        "--event-type", "drill_answer",
        "--drill-concept", "spring/di",
        "--drill-score", "0.8",
        state_root=tmp_path,
    )
    assert result.returncode == 0
    # 일반 append 흐름이라 history.jsonl에 drill_answer 1개 추가
    assert _count_drill_events(tmp_path) == 1


def test_silent_flag_suppresses_stdout(tmp_path):
    _seed_drill_offer(tmp_path, expected_terms=["a"])

    result = _run(
        "--event-type", "drill_answer",
        "--answer", "a b c d e f g h",
        "--silent",
        state_root=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == "" or result.stdout.strip() == ""
