"""
Backend B — embeddings (spec §4.3). Dense semantic retrieval.

Reuses the existing `embedding_service` singleton (VDI: Titan-v2 over the Deluxe
gateway, 1024 dims; local: Bedrock Titan-v2, 1536 dims) — the embedding
dependency stays isolated to this backend.

PURE-PYTHON, zero new dependency (matches the zero-dep bm25 ethos): brute-force
cosine over ~49 L2-normalized vectors is microseconds. No numpy, no vector DB,
no pgvector (spec §12).

Embedding STORAGE (the "test different methods" case): corpus vectors live in a
precomputed sidecar `faq_embeddings.json` next to `faq.json`, keyed by faq_id and
stamped with (model, text_hash). At `index()` time we load the sidecar and
re-embed ONLY entries whose text or model changed — so per-process startup is a
disk load, not N gateway calls. The query path embeds once and dots against the
in-memory matrix.

Strength: paraphrase / synonym matches bm25 misses ("blank" vs "empty").
Weakness: topically-similar-but-wrong-qualifier collisions — the eval reveals
whether that bites.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .retriever import FaqRetriever, Hit, clamp01
from .store import FaqEntry

SIDECAR_PATH = Path(__file__).parent / "faq_embeddings.json"


def _l2_normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if n == 0.0:
        return list(vec)
    return [x / n for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def load_sidecar(path: str | Path = SIDECAR_PATH) -> dict | None:
    """Return {"model": str, "by_id": {faq_id: (vector, text_hash)}} or None."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "model": data.get("model"),
        "by_id": {r["faq_id"]: (r["vector"], r["text_hash"]) for r in data.get("entries", [])},
    }


def save_sidecar(entries: list[FaqEntry], vectors: list[list[float]], model: str,
                 path: str | Path = SIDECAR_PATH) -> None:
    payload = {
        "model": model,
        "entries": [
            {"faq_id": e.faq_id, "text_hash": e.text_hash, "vector": vectors[i]}
            for i, e in enumerate(entries)
        ],
    }
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def embed_entries(entries: list[FaqEntry], reuse: dict | None) -> tuple[list[list[float]], str]:
    """Return (vectors in `entries` order, model). Reuses cached vectors whose
    (model, text_hash) still match; batch-embeds only the stale/missing ones."""
    from services.embedding_service import embedding_service

    model = embedding_service.embedding_model_id
    cache = reuse if (reuse and reuse.get("model") == model) else None
    by_id = cache["by_id"] if cache else {}

    vectors: list[list[float] | None] = []
    stale_idx: list[int] = []
    for i, e in enumerate(entries):
        hit = by_id.get(e.faq_id)
        if hit is not None and hit[1] == e.text_hash:
            vectors.append(list(hit[0]))
        else:
            vectors.append(None)
            stale_idx.append(i)

    if stale_idx:
        fresh = embedding_service.generate_embeddings_batch([entries[i].index_text for i in stale_idx])
        for j, i in enumerate(stale_idx):
            vectors[i] = list(fresh[j])

    return [v for v in vectors], model  # type: ignore[misc]


class EmbeddingsFaqRetriever(FaqRetriever):
    name = "embeddings"

    def __init__(self) -> None:
        self.entries: list[FaqEntry] = []
        self.matrix: list[list[float]] = []  # L2-normalized row per entry
        self.model: str | None = None

    def index(self, entries: list[FaqEntry]) -> None:
        self.entries = list(entries)
        if not self.entries:
            self.matrix = []
            return
        raw, model = embed_entries(self.entries, load_sidecar())
        self.model = model
        self.matrix = [_l2_normalize(v) for v in raw]

    def search(self, query: str, k: int) -> list[Hit]:
        if not self.matrix or not self.entries:
            return []
        from services.embedding_service import embedding_service

        q = _l2_normalize(list(embedding_service.generate_embedding(query)))
        if not any(q):
            return []
        sims = [(_dot(row, q), i) for i, row in enumerate(self.matrix)]  # cosine (rows normalized)
        sims.sort(reverse=True)
        return [(self.entries[i], clamp01(s)) for s, i in sims[:k]]
