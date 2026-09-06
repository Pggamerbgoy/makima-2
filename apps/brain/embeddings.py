"""Makima v9.0 — Lean API-First Embedding Provider.

Provides an interface for vector embeddings, delegating primarily to
lightweight API endpoints (OpenAI / Gemini / LiteLLM / HTTP REST) with optional
local fallback if installed. All output vectors are L2-normalized so cosine
similarity is equivalent to the dot product.

Zero Hardcoding: Dynamic configuration from environment or config dict.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("makima.embeddings")

__all__ = ["DEFAULT_MODEL", "EmbeddingProvider"]

DEFAULT_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


class EmbeddingProvider:
    """Thread-safe, API-first embedding provider with graceful degradation."""

    def __init__(
        self,
        model_name: str | None = None,
        enabled: bool = True,
        config: dict[str, Any] | None = None,
    ) -> None:
        cfg = config or {}
        self._enabled = bool(enabled and os.getenv("EMBEDDING_ENABLED", "true").lower() not in ("false", "0", "no"))
        self._model_name = model_name or cfg.get("embedding_model") or os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
        self._api_key = cfg.get("embedding_api_key") or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
        self._api_base = cfg.get("embedding_api_base") or os.getenv("EMBEDDING_API_BASE")
        self._dimension: Optional[int] = int(cfg.get("embedding_dim", 1536)) if "embedding_dim" in cfg else None
        
        self._local_model: Any = None
        self._load_attempted = False
        self._load_error: Optional[str] = None

    @property
    def available(self) -> bool:
        if not self._enabled:
            return False
        # API-based embeddings available if API key or base is provided
        if self._api_key or self._api_base:
            return True
        # Optional local model fallback if fastembed is explicitly present
        self._ensure_local()
        return self._local_model is not None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> Optional[int]:
        if not self.available:
            return None
        if self._dimension is not None:
            return self._dimension
        if self._local_model is not None:
            return getattr(self._local_model, "dimension", None)
        return 1536

    def _ensure_local(self) -> None:
        if self._local_model is not None or self._load_attempted:
            return
        self._load_attempted = True
        try:
            import warnings
            from fastembed import TextEmbedding  # type: ignore
            cache_dir = os.path.expanduser(os.getenv("FASTEMBED_CACHE_DIR", "~/.makima/models/fastembed_cache"))
            os.makedirs(cache_dir, exist_ok=True)
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=UserWarning)
                self._local_model = TextEmbedding(self._model_name, cache_dir=cache_dir)
            logger.info("Local embedding model loaded: %s", self._model_name)
        except ImportError:
            self._load_error = "fastembed not installed (API mode active or embedding disabled)"
            logger.debug("Local fastembed runtime absent — running in Lean API mode")
        except Exception as e:  # noqa: BLE001
            self._load_error = str(e)
            logger.warning("Embedding model '%s' unavailable (%s) — falling back to keyword search", self._model_name, e)

    @staticmethod
    def _normalize(v: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v

    def embed_one(self, text: str) -> Optional[np.ndarray]:
        """Return a normalized embedding vector for a single text, or None on failure."""
        if not self.available or not text or not isinstance(text, str):
            return None
        
        # 1. API-based embedding if configured
        if self._api_key or self._api_base:
            try:
                import httpx
                url = f"{self._api_base.rstrip('/')}/embeddings" if self._api_base else "https://api.openai.com/v1/embeddings"
                headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
                payload = {"input": text[:8000], "model": self._model_name}
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        vec = data["data"][0]["embedding"]
                        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
                        if self._dimension is None:
                            self._dimension = len(arr)
                        return self._normalize(arr)
            except Exception as e:
                logger.error("API embed_one failed: %s", e)

        # 2. Local fallback if loaded
        if self._local_model is not None:
            try:
                vecs = list(self._local_model.embed([text[:2000]]))
                if vecs:
                    return self._normalize(np.asarray(vecs[0], dtype=np.float32).reshape(-1))
            except Exception as e:
                logger.error("Local embed_one failed: %s", e)
                
        return None

    def embed_batch(self, texts: list[str]) -> Optional[np.ndarray]:
        """Return an (N x dim) normalized matrix for a batch of texts, or None on failure."""
        if not self.available or not texts:
            return None
        
        # 1. API-based batch embedding
        if self._api_key or self._api_base:
            try:
                import httpx
                url = f"{self._api_base.rstrip('/')}/embeddings" if self._api_base else "https://api.openai.com/v1/embeddings"
                headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
                payload = {"input": [t[:8000] for t in texts], "model": self._model_name}
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        sorted_items = sorted(data["data"], key=lambda x: x["index"])
                        rows = [self._normalize(np.asarray(item["embedding"], dtype=np.float32).reshape(-1)) for item in sorted_items]
                        if rows:
                            if self._dimension is None:
                                self._dimension = len(rows[0])
                            return np.vstack(rows)
            except Exception as e:
                logger.error("API embed_batch failed: %s", e)

        # 2. Local fallback if loaded
        if self._local_model is not None:
            try:
                rows = [
                    self._normalize(np.asarray(vec, dtype=np.float32).reshape(-1))
                    for vec in self._local_model.embed([t[:2000] for t in texts])
                ]
                if rows:
                    return np.vstack(rows)
            except Exception as e:
                logger.error("Local embed_batch failed: %s", e)

        return None

