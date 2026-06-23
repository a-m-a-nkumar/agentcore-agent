"""
Pluggable FAQ retrieval layer for the Velox user-guide RAG (spec: FAQ integration).

A second source for platform-help queries, retrieved in parallel with user-guide
routing and folded into the *existing* synthesis call. The retrieval method is
swappable behind one config switch (`FAQ_BACKEND`): bm25 / embeddings / hybrid,
so an A/B eval picks the winner.

Public surface:
  - FaqEntry, load_faq            (store.py)
  - FaqRetriever                  (retriever.py)
  - build_retriever               (factory.py — lazy: it imports `environment`)

`build_retriever` is exposed lazily via __getattr__ so that importing light
submodules (e.g. prompts_faq) does NOT pull in the factory -> environment chain.
"""

from __future__ import annotations

from .retriever import FaqRetriever
from .store import FaqEntry, FAQ_PATH, load_faq

__all__ = ["FaqEntry", "FAQ_PATH", "load_faq", "FaqRetriever", "build_retriever"]


def __getattr__(name: str):
    if name == "build_retriever":
        from .factory import build_retriever
        return build_retriever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
