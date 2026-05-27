"""Unit tests for rag.encoder configuration helpers."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag import encoder  # noqa: E402


def test_encoder_backend_defaults_to_direct(monkeypatch) -> None:
    monkeypatch.delenv("WOOWA_ENCODER_BACKEND", raising=False)
    assert encoder._encoder_backend_name() == "direct"


def test_encoder_backend_accepts_sentence_transformers_alias(monkeypatch) -> None:
    monkeypatch.setenv("WOOWA_ENCODER_BACKEND", "st")
    assert encoder._encoder_backend_name() == "sentence-transformers"


def test_encoder_local_candidates_respect_offline(monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert encoder._local_files_only_candidates() == (True,)


def test_encoder_local_first_can_be_disabled(monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("WOOWA_ENCODER_LOCAL_FIRST", "0")
    assert encoder._local_files_only_candidates() == (False,)


def test_encoder_model_source_prefers_local_snapshot(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "hub"
    model_dir = cache / "models--BAAI--bge-m3"
    snapshot = model_dir / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (model_dir / "refs").mkdir()
    (model_dir / "refs" / "main").write_text("abc123", encoding="utf-8")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    (snapshot / "pytorch_model.bin").write_bytes(b"weights")

    monkeypatch.setenv("HF_HUB_CACHE", str(cache))

    assert encoder._model_source(local_files_only=True) == str(snapshot)
    assert encoder._model_source(local_files_only=False) == encoder.MODEL_ID


def test_encoder_auto_dtype_uses_half_on_accelerators(monkeypatch) -> None:
    monkeypatch.delenv("WOOWA_ENCODER_DTYPE", raising=False)

    assert encoder._direct_torch_dtype_name("mps") == "float16"
    assert encoder._direct_torch_dtype_name("cuda") == "float16"
    assert encoder._direct_torch_dtype_name("cpu") is None


def test_encoder_dtype_float32_disables_half(monkeypatch) -> None:
    monkeypatch.setenv("WOOWA_ENCODER_DTYPE", "float32")

    assert encoder._direct_torch_dtype_name("mps") is None


def test_encoder_parallel_load_defaults_on_with_env_rollback(monkeypatch) -> None:
    monkeypatch.delenv("WOOWA_ENCODER_PARALLEL_LOAD", raising=False)
    assert encoder._parallel_load_enabled() is True

    monkeypatch.setenv("WOOWA_ENCODER_PARALLEL_LOAD", "0")
    assert encoder._parallel_load_enabled() is False


def test_direct_tokenizer_backend_defaults_to_tokenizers(monkeypatch) -> None:
    monkeypatch.delenv("WOOWA_ENCODER_TOKENIZER_BACKEND", raising=False)
    assert encoder._direct_tokenizer_backend_name() == "tokenizers"


def test_direct_tokenizer_backend_accepts_auto_tokenizer_rollback(monkeypatch) -> None:
    monkeypatch.setenv("WOOWA_ENCODER_TOKENIZER_BACKEND", "auto")
    assert encoder._direct_tokenizer_backend_name() == "auto-tokenizer"
