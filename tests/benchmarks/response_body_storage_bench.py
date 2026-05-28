#!/usr/bin/env python3
"""Measure response full-body sidecar dedupe.

The benchmark preserves the key product invariant: every row has full-body
metadata, while identical redacted bodies are stored once on disk.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from core.response_quality import record_response_quality  # noqa: E402


def main() -> int:
    body = "[Mode: cs_qa]\n\n" + ("락 설명 본문. " * 1200) + "\n참고:\n- database/lock-basics\n"
    rows = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        for i in range(100):
            rows.append(record_response_quality(
                source_event_id=f"ask-{i}",
                response_summary="same body",
                response_body=body,
                expected_citation_paths=["database/lock-basics"],
                declared_citation_paths=["database/lock-basics"],
                state_root=state,
                append_event=False,
            ))
        elapsed_ms = (time.perf_counter() - started) * 1000
        body_files = list((state / "learner" / "response-bodies").rglob("*.md"))
        stored_bytes = sum(int(r.get("response_body_stored_bytes") or 0) for r in rows)
        report = {
            "rows": len(rows),
            "body_chars": len(body),
            "body_files": len(body_files),
            "deduped_rows": sum(1 for r in rows if r.get("response_body_deduped")),
            "stored_bytes_total": stored_bytes,
            "naive_bytes_total": len(body.encode("utf-8")) * len(rows),
            "disk_savings_pct": round((1 - stored_bytes / (len(body.encode("utf-8")) * len(rows))) * 100, 2),
            "elapsed_ms": round(elapsed_ms, 3),
            "all_rows_have_body_path": all(r.get("response_body_path") for r in rows),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if (
        report["body_files"] == 1
        and report["deduped_rows"] == 99
        and report["all_rows_have_body_path"]
        and report["disk_savings_pct"] >= 99.0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
