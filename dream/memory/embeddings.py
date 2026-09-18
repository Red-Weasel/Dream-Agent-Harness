"""Local, CPU-only text embeddings for semantic memory recall.

Uses ``fastembed`` (ONNX, no PyTorch) so it stays light and needs no GPU or paid API.
Everything degrades gracefully: if the model can't load (not installed, offline, first
download failed), ``available()`` is False and callers fall back to keyword (FTS5)
recall. Vectors are L2-normalized so cosine similarity is a plain dot product.
"""

from __future__ import annotations

import threading

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, ~130MB, fast on CPU


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None
        self._failed = False
        self.dim: int | None = None
        # The engine's boot-time backfill runs in a worker thread while the session
        # embeds on its own: without this, both would build their own copy of the
        # model. Re-entrant because the dim probe below re-enters embed → _ensure.
        self._lock = threading.RLock()

    def _ensure(self) -> None:
        if self._model is not None or self._failed:
            return
        with self._lock:
            if self._model is not None or self._failed:
                return
            try:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name=self.model_name)
                probe = self.embed("probe")
                self.dim = len(probe) if probe is not None else None
                if self.dim is None:
                    self._failed = True
                    self._model = None
            except Exception:
                self._failed = True
                self._model = None

    def available(self) -> bool:
        self._ensure()
        return self._model is not None

    def embed(self, text: str):
        """Return an L2-normalized float32 vector, or None if unavailable."""
        # Safe against recursion: _ensure sets _model before it probes, so a call
        # from within the probe returns early here.
        self._ensure()
        if self._model is None:
            return None
        try:
            import numpy as np

            vec = next(iter(self._model.embed([text])))
            vec = np.asarray(vec, dtype="float32")
            norm = float(np.linalg.norm(vec))
            return vec / norm if norm else vec
        except Exception:
            return None


class Reranker:
    """Cross-encoder reranker: re-scores (query, document) pairs for precision. Used to
    re-order the top hybrid candidates before returning them. Graceful: returns None
    when unavailable, and callers keep the pre-rerank order."""

    def __init__(self, model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model = None
        self._failed = False

    def _ensure(self) -> None:
        if self._model is not None or self._failed:
            return
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(model_name=self.model_name)
        except Exception:
            self._failed = True
            self._model = None

    def available(self) -> bool:
        self._ensure()
        return self._model is not None

    def rerank(self, query: str, documents: list[str]) -> list[float] | None:
        """Return a relevance score per document (aligned), or None if unavailable."""
        self._ensure()
        if self._model is None or not documents:
            return None
        try:
            return list(self._model.rerank(query, documents))
        except Exception:
            return None
