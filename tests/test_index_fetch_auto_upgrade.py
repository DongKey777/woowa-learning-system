from __future__ import annotations

import json
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "index-fetch"
loader = SourceFileLoader("index_fetch", str(SCRIPT))
index_fetch = types.ModuleType(loader.name)
index_fetch.__file__ = str(SCRIPT)
loader.exec_module(index_fetch)


def test_known_sha_covers_default_tag() -> None:
    # bare `bin/index-fetch` resolves --tag=DEFAULT_TAG and must have an
    # integrity hash, otherwise the default fetch would silently skip verify.
    assert index_fetch.DEFAULT_TAG in index_fetch.KNOWN_SHA256
    for tag, sha in index_fetch.KNOWN_SHA256.items():
        assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), tag


def test_known_tags_follow_naming_convention() -> None:
    # main() derives --pattern as f"{tag}.tar.zst"; the release asset must be
    # named the same, so every known tag has to look like a release tag.
    for tag in index_fetch.KNOWN_SHA256:
        assert tag.startswith("paradigm-v2-index-v"), tag


def test_needs_auto_upgrade_on_release_drift() -> None:
    f = index_fetch._needs_auto_upgrade
    older = "paradigm-v2-index-v1.0.2"
    latest = index_fetch.DEFAULT_TAG
    assert f(older, latest, True) is True          # older released index → upgrade
    assert f(latest, latest, True) is False         # already latest → no-op
    assert f(older, latest, False) is False         # flag off → never upgrade
    assert f(None, latest, True) is False           # local build (no tag) → protected
    assert f("", latest, True) is False             # empty tag → protected


def test_manifest_release_tag_reads_field(tmp_path: Path) -> None:
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps({"release_tag": "paradigm-v2-index-v1.0.3"}),
                  encoding="utf-8")
    assert index_fetch._manifest_release_tag(mf) == "paradigm-v2-index-v1.0.3"


def test_manifest_release_tag_missing_and_invalid(tmp_path: Path) -> None:
    assert index_fetch._manifest_release_tag(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert index_fetch._manifest_release_tag(bad) is None
    no_tag = tmp_path / "no_tag.json"
    no_tag.write_text(json.dumps({"concepts_indexed": 3350}), encoding="utf-8")
    assert index_fetch._manifest_release_tag(no_tag) is None
