"""BGE-M3 dense encoder with direct transformers fast path."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

MODEL_ID = "BAAI/bge-m3"
EMBED_DIM = 1024
_FALSE_VALUES = {"0", "false", "off", "no"}
_ST_BACKENDS = {"sentence-transformers", "sentence_transformers", "st"}
_AUTO_TOKENIZER_BACKENDS = {"auto", "autotokenizer", "auto-tokenizer", "transformers"}
_NONE_DTYPE_VALUES = {"", "none", "off", "false", "float32", "fp32"}
_DTYPE_ALIASES = {
    "float16": "float16", "fp16": "float16", "half": "float16",
    "bfloat16": "bfloat16", "bf16": "bfloat16",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().lower()


def _parallel_load_enabled() -> bool:
    return _env("WOOWA_ENCODER_PARALLEL_LOAD", "1") not in _FALSE_VALUES


def _resolve_device() -> str:
    if override := os.environ.get("WOOWA_ENCODER_DEVICE"):
        return override
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _encoder_backend_name() -> str:
    return "sentence-transformers" if _env("WOOWA_ENCODER_BACKEND", "direct") in _ST_BACKENDS else "direct"


def _direct_tokenizer_backend_name() -> str:
    return "auto-tokenizer" if _env("WOOWA_ENCODER_TOKENIZER_BACKEND", "tokenizers") in _AUTO_TOKENIZER_BACKENDS else "tokenizers"


def _local_files_only_candidates() -> tuple[bool, ...]:
    if os.environ.get("HF_HUB_OFFLINE"):
        return (True,)
    return (True, False) if _env("WOOWA_ENCODER_LOCAL_FIRST", "1") not in _FALSE_VALUES else (False,)


def _hf_cache_root() -> Path:
    for env_name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if raw := os.environ.get(env_name):
            return Path(raw).expanduser()
    if hf_home := os.environ.get("HF_HOME"):
        return Path(hf_home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _local_snapshot_path() -> Path | None:
    model_dir = _hf_cache_root() / f"models--{MODEL_ID.replace('/', '--')}"
    candidates: list[Path] = []
    refs_main = model_dir / "refs" / "main"
    if refs_main.exists() and (revision := refs_main.read_text(encoding="utf-8").strip()):
        candidates.append(model_dir / "snapshots" / revision)
    snapshots_dir = model_dir / "snapshots"
    if snapshots_dir.exists():
        candidates.extend(sorted(snapshots_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True))
    for snapshot in candidates:
        if not snapshot.is_dir():
            continue
        has_tokenizer = (snapshot / "tokenizer.json").exists() or (snapshot / "sentencepiece.bpe.model").exists()
        has_weights = (snapshot / "model.safetensors").exists() or (snapshot / "pytorch_model.bin").exists()
        if (snapshot / "config.json").exists() and has_tokenizer and has_weights:
            return snapshot
    return None


def _model_source(local_files_only: bool) -> str:
    snapshot = _local_snapshot_path() if local_files_only else None
    return str(snapshot) if snapshot is not None else MODEL_ID


def _direct_torch_dtype_name(device: str) -> str | None:
    raw = _env("WOOWA_ENCODER_DTYPE", "auto")
    if raw == "auto":
        return "float16" if device in {"cuda", "mps"} else None
    return None if raw in _NONE_DTYPE_VALUES else _DTYPE_ALIASES.get(raw)


def prime_encoder_backend_imports() -> str:
    backend = _encoder_backend_name()
    if backend == "sentence-transformers":
        from sentence_transformers import SentenceTransformer
        _ = SentenceTransformer
        return backend
    import torch
    from transformers import AutoModel
    if _direct_tokenizer_backend_name() == "auto-tokenizer":
        from transformers import AutoTokenizer as TokenizerCls
    else:
        from transformers import PreTrainedTokenizerFast as TokenizerCls
    _ = (torch, AutoModel, TokenizerCls)
    return backend


def _max_token_length(source_path: Path) -> int:
    try:
        config = json.loads((source_path / "config.json").read_text(encoding="utf-8"))
        raw = int(config.get("max_position_embeddings", 8194))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raw = 8194
    return max(raw - 2, 1)


def _load_fast_tokenizer(source: str):
    source_path = Path(source)
    tokenizer_file = source_path / "tokenizer.json"
    if not tokenizer_file.exists():
        raise FileNotFoundError(tokenizer_file)
    from transformers import PreTrainedTokenizerFast
    return PreTrainedTokenizerFast(
        tokenizer_file=str(tokenizer_file),
        model_max_length=_max_token_length(source_path),
        pad_token="<pad>",
        cls_token="<s>",
        sep_token="</s>",
        unk_token="<unk>",
        mask_token="<mask>",
    )


def _load_direct_tokenizer(source: str, *, local_files_only: bool):
    if _direct_tokenizer_backend_name() == "tokenizers":
        try:
            return _load_fast_tokenizer(source)
        except OSError:
            pass
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(source, local_files_only=local_files_only)


@lru_cache(maxsize=1)
def _get_sentence_transformer_model():
    from sentence_transformers import SentenceTransformer
    device = _resolve_device()
    last_exc: Exception | None = None
    for local_files_only in _local_files_only_candidates():
        try:
            return SentenceTransformer(
                _model_source(local_files_only),
                device=device,
                local_files_only=local_files_only,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    assert last_exc is not None
    raise last_exc


@lru_cache(maxsize=1)
def _get_direct_model():
    import torch
    from transformers import AutoModel
    device = _resolve_device()
    dtype_name = _direct_torch_dtype_name(device)
    last_exc: Exception | None = None
    for local_files_only in _local_files_only_candidates():
        try:
            source = _model_source(local_files_only)
            model_kwargs = {"local_files_only": local_files_only}
            if dtype_name is not None:
                model_kwargs["torch_dtype"] = getattr(torch, dtype_name)
            if _parallel_load_enabled():
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=2) as pool:
                    tok_f = pool.submit(_load_direct_tokenizer, source, local_files_only=local_files_only)
                    model_f = pool.submit(AutoModel.from_pretrained, source, **model_kwargs)
                    tokenizer, model = tok_f.result(), model_f.result()
            else:
                tokenizer = _load_direct_tokenizer(source, local_files_only=local_files_only)
                model = AutoModel.from_pretrained(source, **model_kwargs)
            model.to(device)
            model.eval()
            return tokenizer, model, device, torch
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def encode(texts: list[str], batch_size: int = 16, normalize: bool = True) -> "np.ndarray":
    if not texts:
        import numpy as np
        return np.zeros((0, EMBED_DIM), dtype="float32")
    if _encoder_backend_name() == "sentence-transformers":
        model = _get_sentence_transformer_model()
        return model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    return _encode_direct(texts, batch_size=batch_size, normalize=normalize)


def _encode_direct(texts: list[str], batch_size: int = 16, normalize: bool = True) -> "np.ndarray":
    import numpy as np
    tokenizer, model, device, torch = _get_direct_model()
    rows = []
    for start in range(0, len(texts), batch_size):
        inputs = tokenizer(texts[start:start + batch_size], padding=True, truncation=True, return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            embeddings = model(**inputs).last_hidden_state[:, 0]
            if normalize:
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
            rows.append(embeddings.cpu().numpy().astype("float32", copy=False))
    return np.vstack(rows).astype("float32", copy=False) if rows else np.zeros((0, EMBED_DIM), dtype="float32")


@lru_cache(maxsize=200)
def _encode_query_cached(text: str) -> tuple:
    return tuple(encode([text], batch_size=1)[0].tolist())


def encode_query(text: str) -> "np.ndarray":
    import numpy as np
    return np.asarray(_encode_query_cached(text), dtype="float32")
