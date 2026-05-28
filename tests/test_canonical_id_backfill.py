from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "migration" / "canonical_id_backfill.py"
spec = importlib.util.spec_from_file_location("canonical_id_backfill", SCRIPT)
assert spec and spec.loader
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def test_backfill_canonicalizes_legacy_production_id() -> None:
    row = {"event_type": "rag_ask", "mode": "learning", "learner_id": "default"}

    new_line, changed, skipped, skipped_non_prod = backfill._rewrite_line(
        json.dumps(row),
        canonical="DongKey777",
    )
    out = json.loads(new_line)

    assert changed is True
    assert skipped is False
    assert skipped_non_prod is False
    assert out["learner_id"] == "DongKey777"
    assert out["_canonical_id_source"] == "default"


def test_backfill_skips_non_production_isolation_id_without_error() -> None:
    row = {"event_type": "rag_ask", "mode": "development", "learner_id": "learner-A"}

    new_line, changed, skipped, skipped_non_prod = backfill._rewrite_line(
        json.dumps(row),
        canonical="DongKey777",
    )

    assert json.loads(new_line)["learner_id"] == "learner-A"
    assert changed is False
    assert skipped is False
    assert skipped_non_prod is True
