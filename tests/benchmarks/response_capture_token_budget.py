#!/usr/bin/env python3
"""Estimate AI transcript cost for response-quality capture modes.

This benchmark does not measure model billing. It measures the extra text that
the AI session must place into the conversation transcript after the learner has
already seen the final answer.
"""
from __future__ import annotations

import json
from pathlib import Path


def _token_estimate(chars: int) -> int:
    # Conservative proxy for mixed Korean/code/markdown transcript text.
    return max(1, round(chars / 2.5))


def main() -> int:
    source_event_id = "ask-1779955019957-4183"
    path = Path("state/learner/response-bodies") / f"{source_event_id}.md"
    body = (
        "[Mode: cs_qa]\n\n"
        "Pessimistic locking blocks the second transaction at SELECT FOR UPDATE. "
        "Optimistic locking lets both transactions read and fails the stale "
        "UPDATE with WHERE version = original. "
        "Insert races need UNIQUE or a guard row because no existing version "
        "exists yet.\n\n"
        "참고:\n- database/lock-basics\n"
    ) * 45
    stdin_extra = len(body)
    path_cmd = (
        "bin/learn-response-quality --source-event-id "
        f"{source_event_id} --response-path {path} --silent"
    )
    summary_cmd = (
        "bin/learn-response-quality --source-event-id "
        f"{source_event_id} --summary-only --contract-flag body_not_captured "
        "--contract-flag token_efficient_summary_only --silent"
    )
    path_extra = len(path_cmd)
    summary_extra = len(summary_cmd)
    report = {
        "answer_chars": len(body),
        "current_stdin_extra_chars": stdin_extra,
        "path_capture_extra_chars": path_extra,
        "summary_only_extra_chars": summary_extra,
        "current_stdin_extra_tokens_est": _token_estimate(stdin_extra),
        "path_capture_extra_tokens_est": _token_estimate(path_extra),
        "summary_only_extra_tokens_est": _token_estimate(summary_extra),
        "path_capture_savings_pct": round((1 - path_extra / stdin_extra) * 100, 2),
        "summary_only_savings_pct": round((1 - summary_extra / stdin_extra) * 100, 2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["path_capture_savings_pct"] < 90:
        return 1
    if report["summary_only_savings_pct"] < 90:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
