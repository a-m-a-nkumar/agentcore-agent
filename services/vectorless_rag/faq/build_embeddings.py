"""
Offline one-shot: (re)generate the FAQ embedding sidecar `faq_embeddings.json`.

Run whenever `faq.json` changes (or you switch embedding model/env). Keeps the
per-process `EmbeddingsFaqRetriever.index()` cheap — it just loads this file and
re-embeds nothing when the sidecar is current.

    python -m services.vectorless_rag.faq.build_embeddings
"""

from __future__ import annotations

from .backend_embeddings import SIDECAR_PATH, embed_entries, load_sidecar, save_sidecar
from .store import load_faq


def main() -> None:
    entries = load_faq()
    reuse = load_sidecar()
    vectors, model = embed_entries(entries, reuse)
    save_sidecar(entries, vectors, model)
    dims = len(vectors[0]) if vectors else 0
    print(f"Wrote {SIDECAR_PATH.name}: {len(entries)} entries, dims={dims}, model={model}")


if __name__ == "__main__":
    main()
