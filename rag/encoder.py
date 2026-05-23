"""BGE-M3 encoder wrapper — dense embedding only.

Hypothesis: same encoder as legacy (BGE-M3, 1024-d) so D10 holds without
re-measurement. M4 16GB RAM: encode in small batches to avoid spike.

Lazy import — sentence_transformers download (~3GB) only triggered on first
.encode() call. Tests can mock _MODEL by monkeypatching `_get_model`.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

MODEL_ID = "BAAI/bge-m3"
EMBED_DIM = 1024


@lru_cache(maxsize=1)
def _get_model():
    """Module-level lazy singleton — first call downloads + loads BGE-M3."""
    from sentence_transformers import SentenceTransformer

    device = os.environ.get("WOOWA_ENCODER_DEVICE", "mps")
    return SentenceTransformer(MODEL_ID, device=device)


def encode(texts: list[str], batch_size: int = 16, normalize: bool = True) -> "np.ndarray":
    """Encode list of strings → [N, 1024] float32 dense vectors.

    batch_size=16 keeps M4 16GB RAM usage <4GB during encode.
    normalize=True so cosine = dot product downstream (Lance default).
    """
    if not texts:
        import numpy as np

        return np.zeros((0, EMBED_DIM), dtype="float32")
    model = _get_model()
    return model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=normalize,
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def encode_query(text: str) -> "np.ndarray":
    """Single-query convenience wrapper → [1024] float32 vector."""
    arr = encode([text], batch_size=1)
    return arr[0]
