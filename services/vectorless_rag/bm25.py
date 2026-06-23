"""
Compact BM25 (Okapi) over the tree — zero dependencies, keeps the vectorless ethos.

Indexes each node's title + summary + details, so exact technical terms ("MCP configured",
"PAT", "Katalon", "mcp.json", "Sync Docs") become a deterministic, sub-millisecond signal
that disambiguates close siblings the LLM fumbles. Used as a hybrid tie-breaker, never alone.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .tree import GuideTree

_TOKEN = re.compile(r"[a-z0-9]+")


def _tok(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


class BM25Index:
    def __init__(self, tree: GuideTree, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.ids: list[str] = []
        self.docs: list[Counter] = []
        self.lens: list[int] = []
        for n in tree.nodes.values():
            if n.depth < 1:  # skip synthetic root
                continue
            toks = _tok(f"{n.title} {n.title} {n.summary} {n.details or ''}")  # title weighted x2
            self.ids.append(n.node_id)
            self.docs.append(Counter(toks))
            self.lens.append(len(toks))
        self.avgdl = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        # idf
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        N = len(self.docs)
        self.idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        self._pos = {nid: i for i, nid in enumerate(self.ids)}

    def _score_doc(self, q_tokens: list[str], i: int) -> float:
        d, dl = self.docs[i], self.lens[i]
        s = 0.0
        for t in q_tokens:
            if t not in d:
                continue
            f = d[t]
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / denom
        return s

    def rank(self, query: str) -> list[tuple[str, float]]:
        q = _tok(query)
        scored = [(self.ids[i], self._score_doc(q, i)) for i in range(len(self.ids))]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def score(self, query: str, node_id: str) -> float:
        i = self._pos.get(node_id)
        return self._score_doc(_tok(query), i) if i is not None else 0.0

    def best_among(self, query: str, node_ids: list[str]) -> tuple[str | None, float]:
        q = _tok(query)
        best, best_s = None, -1.0
        for nid in node_ids:
            i = self._pos.get(nid)
            if i is None:
                continue
            s = self._score_doc(q, i)
            if s > best_s:
                best, best_s = nid, s
        return best, max(best_s, 0.0)
