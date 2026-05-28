from __future__ import annotations

import json
import sqlite3
import time
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "repo-readiness"
loader = SourceFileLoader("repo_readiness", str(SCRIPT))
repo_readiness = types.ModuleType(loader.name)
repo_readiness.__file__ = str(SCRIPT)
loader.exec_module(repo_readiness)


def _seed_base(tmp_path: Path, repo: str) -> Path:
    base = tmp_path / "repos" / repo
    (base / "archive").mkdir(parents=True)
    conn = sqlite3.connect(base / "archive" / "prs.sqlite3")
    conn.execute("CREATE TABLE prs(id INTEGER)")
    conn.commit()
    conn.close()
    (base / "mission_patterns.json").write_text('{"patterns":[]}', encoding="utf-8")
    (base / "contexts").mkdir()
    (base / "contexts" / "learner-state.json").write_text(
        json.dumps({"computed_at": time.time()}),
        encoding="utf-8",
    )
    return base


def test_cross_crew_not_required_when_repo_has_no_anchors(tmp_path: Path) -> None:
    repo = "spring-roomescape-waiting"
    _seed_base(tmp_path, repo)
    (tmp_path / "learner").mkdir()
    (tmp_path / "learner" / "review_anchors.json").write_text(
        json.dumps({"anchor_count": 0, "anchors": []}),
        encoding="utf-8",
    )

    result = repo_readiness.assess_readiness(repo, tmp_path)

    assert result["ready"] is True
    assert result["checks"]["cross_crew_built"] is True
    assert result["cross_crew_not_applicable"] is True


def test_cross_crew_required_when_repo_has_anchors(tmp_path: Path) -> None:
    repo = "spring-roomescape-member"
    _seed_base(tmp_path, repo)
    (tmp_path / "learner").mkdir()
    (tmp_path / "learner" / "review_anchors.json").write_text(
        json.dumps({"anchors": [{"repo": repo, "thread_id": "t1"}]}),
        encoding="utf-8",
    )

    result = repo_readiness.assess_readiness(repo, tmp_path)

    assert result["ready"] is False
    assert result["missing"] == ["cross_crew_built"]
