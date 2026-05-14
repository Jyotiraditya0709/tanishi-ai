"""
Local embeddings for offline memory retrieval.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import Optional


class LocalEmbedder:
    """CPU-friendly local embedder with keyword fallback."""

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self):
        self._model = None
        self._load_error: Optional[str] = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None or self._load_error is not None:
            return
        with self._lock:
            if self._model is not None or self._load_error is not None:
                return
            try:
                print("[embeddings] loading local model...")
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.MODEL_NAME)
            except Exception as e:
                self._load_error = str(e)

    def embed(self, text: str) -> list[float]:
        self._ensure_model()
        if self._model is not None:
            vec = self._model.encode([text or ""], normalize_embeddings=True)[0]
            return [float(x) for x in vec.tolist()]
        return self._keyword_vector(text)

    def similarity(self, a: str, b: str) -> float:
        self._ensure_model()
        if self._model is not None:
            va = self.embed(a)
            vb = self.embed(b)
            return _cosine(va, vb)
        return self._keyword_overlap(a, b)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", (text or "").lower())

    def _keyword_vector(self, text: str) -> list[float]:
        # Compact pseudo-vector used only for fallback compatibility/debugging.
        toks = self._tokens(text)
        c = Counter(toks)
        top = sorted(c.items(), key=lambda x: (-x[1], x[0]))[:32]
        return [float(v) for _, v in top]

    def _keyword_overlap(self, a: str, b: str) -> float:
        ta = self._tokens(a)
        tb = self._tokens(b)
        if not ta or not tb:
            return 0.0
        ca = Counter(ta)
        cb = Counter(tb)
        common = set(ca) & set(cb)
        dot = sum(ca[t] * cb[t] for t in common)
        na = math.sqrt(sum(v * v for v in ca.values()))
        nb = math.sqrt(sum(v * v for v in cb.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_GLOBAL_EMBEDDER: Optional[LocalEmbedder] = None
_GLOBAL_LOCK = threading.Lock()


def get_local_embedder() -> LocalEmbedder:
    global _GLOBAL_EMBEDDER
    if _GLOBAL_EMBEDDER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_EMBEDDER is None:
                _GLOBAL_EMBEDDER = LocalEmbedder()
    return _GLOBAL_EMBEDDER


def prewarm_local_embedder() -> None:
    emb = get_local_embedder()
    try:
        emb.embed("prewarm local embeddings")
    except Exception:
        # Never crash startup because of optional embedding model.
        pass
