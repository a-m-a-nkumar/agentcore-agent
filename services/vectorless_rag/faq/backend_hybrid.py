"""
Backend C — hybrid (spec §4.4). Thin, hand-rolled fusion of bm25 + embeddings.

Run both backends, each producing absolute-normalized [0,1] scores, then fuse
with a weighted sum:  fused = w_bm25 * bm25_norm + w_emb * emb_norm.
Union the candidate sets; a candidate returned by only one backend gets 0 for
the missing side. Re-sort by fused, take top-k.

Deliberately hand-rolled — NO RRF / ensemble framework (spec §12 guardrail):
RRF is for many large lists; for two small lists a weighted sum is plenty and
keeps the score absolute so `FAQ_THRESHOLD` still means something.
"""

from __future__ import annotations

from .backend_bm25 import Bm25FaqRetriever
from .backend_embeddings import EmbeddingsFaqRetriever
from .retriever import FaqRetriever, Hit
from .store import FaqEntry


class HybridFaqRetriever(FaqRetriever):
    name = "hybrid"

    def __init__(self, w_bm25: float = 0.5, w_emb: float = 0.5) -> None:
        self.w_bm25 = w_bm25
        self.w_emb = w_emb
        self.bm25 = Bm25FaqRetriever()
        self.emb = EmbeddingsFaqRetriever()

    def index(self, entries: list[FaqEntry]) -> None:
        self.bm25.index(entries)
        self.emb.index(entries)

    def search(self, query: str, k: int) -> list[Hit]:
        # Pull a slightly wider pool from each so fusion has room to re-rank.
        pool = max(k, 5)
        bm = self.bm25.search(query, pool)
        em = self.emb.search(query, pool)

        bm_score = {e.faq_id: s for e, s in bm}
        em_score = {e.faq_id: s for e, s in em}
        entries = {e.faq_id: e for e, _ in bm} | {e.faq_id: e for e, _ in em}

        fused: list[Hit] = []
        for fid, entry in entries.items():
            s = self.w_bm25 * bm_score.get(fid, 0.0) + self.w_emb * em_score.get(fid, 0.0)
            fused.append((entry, s))
        fused.sort(key=lambda x: x[1], reverse=True)
        return fused[:k]
