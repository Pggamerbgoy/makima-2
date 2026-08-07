"""Makima v8.1 — Local ONNX Embedding Provider (RAG vector layer).

Wraps fastembed (ONNX Runtime) with lazy model loading and graceful
degradation: if the model cannot load, `available` is False and callers
(EternalMemory) fall back to keyword search. All vectors are L2-normalized
so cosine similarity == dot product.

Default model: paraphrase-multilingual-MiniLM-L12-v2 (384-dim, supports
Hindi/Hinglish + 50 other languages, ~120MB, downloaded once and cached).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("makima.embeddings")

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingProvider:
    """Thread-safe wrapper around fastembed.TextEmbedding (normalized vectors)."""

    def __init__(self, model_name: str = DEFAULT_MODEL, enabled: bool = True) -> None:
        self._model_name = model_name
        self._enabled = enabled
        self._model: Any = None
        self._load_attempted = False
        self._load_error: Optional[str] = None

    @property
    def available(self) -> bool:
        if not self._enabled:
            return False
        self._ensure()
        return self._model is not None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> Optional[int]:
        if not self.available:
            return None
        return getattr(self._model, "dimension", None)

    def _ensure(self) -> None:
        if self._model is not None or self._load_attempted:
            return
        self._load_attempted = True
        try:
            from fastembed import TextEmbedding  # type: ignore

            self._model = TextEmbedding(self._model_name)
            logger.info(
                "Embedding model loaded: %s (dim=%s)",
                self._model_name,
                getattr(self._model, "dimension", "?"),
            )
        except Exception as e:  # noqa: BLE001
            self._load_error = str(e)
            logger.warning(
                "Embedding model '%s' unavailable (%s) — falling back to keyword search",
                self._model_name,
                e,
            )

    @staticmethod
    def _normalize(v: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v

    def embed_one(self, text: str) -> Optional[np.ndarray]:
        """Return a normalized embedding vector for a single text, or None on failure."""
        if not self.available or not text or not isinstance(text, str):
            return None
        try:
            vecs = list(self._model.embed([text[:2000]]))
            if not vecs:
                return None
            return self._normalize(np.asarray(vecs[0], dtype=np.float32).reshape(-1))
        except Exception as e:  # noqa: BLE001
            logger.error("embed_one failed: %s", e)
            return None

    def embed_batch(self, texts: list[str]) -> Optional[np.ndarray]:
        """Return an (N x dim) normalized matrix for a batch of texts, or None on failure."""
        if not self.available or not texts:
            return None
        try:
            rows = [
                self._normalize(np.asarray(vec, dtype=np.float32).reshape(-1))
                for vec in self._model.embed([t[:2000] for t in texts])
            ]
            if not rows:
                return None
            return np.vstack(rows)
        except Exception as e:  # noqa: BLE001
            logger.error("embed_batch failed: %s", e)
            return None
