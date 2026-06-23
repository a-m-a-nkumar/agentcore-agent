"""
Backend A — bm25 (spec §4.2). Keyword only, zero new dependency.

A SEPARATE BM25 index over the FAQ entries (index field = question + answer +
keywords). Deliberately NOT merged into the user-guide BM25 corpus (different
content, different score scales — spec §12 guardrail). Reuses the same Okapi
formula and tokenizer as `services/vectorless_rag/bm25.py`.

Note: on a small FAQ (~49 docs) IDF estimates are noisier than on the 47-node
guide — this is exactly why we A/B bm25 against embeddings.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .retriever import FaqRetriever, Hit, saturating_squash
from .store import FaqEntry

_TOKEN = re.compile(r"[a-z0-9]+")

# Score that maps to 0.5 under the saturating squash. A strong multi-term keyword
# match on this corpus lands well above this; an incidental single-term hit below.
# Tunable, but the eval tunes FAQ_THRESHOLD against it (Phase 6), not this.
BM25_SQUASH_HALF = 6.0

# Light stopword filter — FAQ-only (the guide BM25 stays unfiltered). On a ~49-doc
# corpus, common question words ("how do I to a") still carry small IDF and leak
# BM25 mass into out-of-scope queries, inflating their top score above threshold.
# Dropping them means an OOS query shares NO content tokens -> score 0 -> no false
# injection, without hurting real matches (which rely on content words).
_STOP = frozenset(
    "a an and are as at be by can do does for from how i in is it its me my of on "
    "or our that the their then there these this to use using want we what when where "
    "which who why will with you your".split()
)


def _tok(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


class Bm25FaqRetriever(FaqRetriever):
    name = "bm25"

    def __init__(self, k1: float = 1.5, b: float = 0.75, squash_half: float = BM25_SQUASH_HALF) -> None:
        self.k1, self.b, self.squash_half = k1, b, squash_half
        self.entries: list[FaqEntry] = []
        self.docs: list[Counter] = []
        self.lens: list[int] = []
        self.avgdl = 0.0
        self.idf: dict[str, float] = {}

    def index(self, entries: list[FaqEntry]) -> None:
        self.entries = list(entries)
        self.docs = [Counter(_tok(e.index_text)) for e in self.entries]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avgdl = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        N = len(self.docs)
        self.idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def _score_doc(self, q_tokens: list[str], i: int) -> float:
        d, dl = self.docs[i], self.lens[i]
        s = 0.0
        for t in q_tokens:
            f = d.get(t)
            if not f:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / denom
        return s

    def search(self, query: str, k: int) -> list[Hit]:
        if not self.entries:
            return []
        q = _tok(query)
        scored = [(i, self._score_doc(q, i)) for i in range(len(self.entries))]
        scored.sort(key=lambda x: x[1], reverse=True)
        out: list[Hit] = []
        for i, raw in scored[:k]:
            out.append((self.entries[i], saturating_squash(raw, self.squash_half)))
        return out
