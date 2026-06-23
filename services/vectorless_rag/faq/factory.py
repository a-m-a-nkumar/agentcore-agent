"""
Backend factory (spec §4.1 / §5) — the single switch that selects the active
FAQ retrieval method. `router.py` / the API call `build_retriever()` once at
startup and then only ever talk to the returned `FaqRetriever`.

Config single-source-of-truth lives in env_vdi.py / env_local.py (imported via
`environment`); we fall back to os.getenv so the faq package stays importable
standalone (eval scripts, smoke tests) — same pattern as embedding_service.py.
"""

from __future__ import annotations

import os

from .retriever import FaqRetriever
from .store import FaqEntry, load_faq

# ── config (env_vdi.py / env_local.py win; os.getenv is the standalone fallback) ──
try:
    from environment import FAQ_BACKEND as _BACKEND
    from environment import FAQ_W_BM25 as _W_BM25
    from environment import FAQ_W_EMB as _W_EMB
except Exception:  # noqa: BLE001 — standalone use, env_* import chain unavailable
    _BACKEND = os.getenv("FAQ_BACKEND", "bm25")
    _W_BM25 = float(os.getenv("FAQ_W_BM25", "0.5"))
    _W_EMB = float(os.getenv("FAQ_W_EMB", "0.5"))


def _make(backend: str, w_bm25: float, w_emb: float) -> FaqRetriever:
    backend = (backend or "bm25").lower()
    if backend == "bm25":
        from .backend_bm25 import Bm25FaqRetriever
        return Bm25FaqRetriever()
    if backend == "embeddings":
        from .backend_embeddings import EmbeddingsFaqRetriever
        return EmbeddingsFaqRetriever()
    if backend == "hybrid":
        from .backend_hybrid import HybridFaqRetriever
        return HybridFaqRetriever(w_bm25=w_bm25, w_emb=w_emb)
    raise ValueError(f"Unknown FAQ_BACKEND: {backend!r} (expected bm25|embeddings|hybrid)")


def build_retriever(
    backend: str | None = None,
    *,
    entries: list[FaqEntry] | None = None,
    w_bm25: float | None = None,
    w_emb: float | None = None,
) -> FaqRetriever:
    """Build the active retriever and index the FAQ corpus once. Pass `backend`
    to override the config switch (the A/B harness builds all three this way)."""
    retriever = _make(
        backend if backend is not None else _BACKEND,
        w_bm25 if w_bm25 is not None else _W_BM25,
        w_emb if w_emb is not None else _W_EMB,
    )
    retriever.index(entries if entries is not None else load_faq())
    return retriever
