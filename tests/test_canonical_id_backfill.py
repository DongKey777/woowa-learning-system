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


def test_rewrite_line_skips_dev_test_row_with_legacy_id() -> None:
    # A dev/test row carrying a legacy learner_id must NOT be canonicalized to the
    # real learner identity (the legacy check used to run before the mode skip).
    import json as _json

    from scripts.migration.canonical_id_backfill import _rewrite_line

    line = _json.dumps({"learner_id": "default", "mode": "development", "e": "x"})
    out, changed, _backfilled, skipped = _rewrite_line(line, canonical="DongKey777")
    assert changed is False
    assert skipped is True
    assert _json.loads(out)["learner_id"] == "default"  # untouched

    # a production row with a legacy id is still canonicalized
    prod = _json.dumps({"learner_id": "default", "mode": "learning"})
    out2, changed2, _b2, _s2 = _rewrite_line(prod, canonical="DongKey777")
    assert changed2 is True
    assert _json.loads(out2)["learner_id"] == "DongKey777"
