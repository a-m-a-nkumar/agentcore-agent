"""
FAQ storage (spec §3) — a flat JSON file, NOT a database table.

Small corpus (tens of entries), slow-changing, dev-edited. Storage is decoupled
from retrieval: every backend reads the same in-memory `list[FaqEntry]`, so the
migration path to a DB (if non-dev live editing is ever needed) changes nothing
downstream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

FAQ_PATH = Path(__file__).parent / "faq.json"


@dataclass
class FaqEntry:
    faq_id: str                     # stable slug, record key — never changes
    category: str                   # one of the FAQ's categories (metadata/log dim)
    question: str                   # canonical question — primary match surface
    answer: str                     # canonical short answer — injected into synthesis
    keywords: list[str] = field(default_factory=list)  # synonym layer (strengthens BM25)
    guide_ref: str | None = None    # node_id of overlapping user-guide node, drives dedupe
    last_updated: str | None = None  # ISO date — future incremental reindex only

    @property
    def index_text(self) -> str:
        """The text every backend indexes/embeds: question + answer + keywords.
        Keeping this in one place means all three backends index the SAME surface."""
        return f"{self.question} {self.answer} {' '.join(self.keywords)}".strip()

    @property
    def text_hash(self) -> str:
        """sha256 of `index_text` — used to invalidate cached embeddings when the
        entry's text changes (spec §4.4 embedding storage)."""
        return hashlib.sha256(self.index_text.encode("utf-8")).hexdigest()


def load_faq(path: str | Path = FAQ_PATH) -> list[FaqEntry]:
    """Load the flat FAQ array into `FaqEntry` objects. utf-8-sig drops a BOM the
    same way `tree.py` does for the guide tree."""
    text = Path(path).read_text(encoding="utf-8-sig").lstrip("﻿ \t\r\n")
    raw = json.loads(text)
    entries: list[FaqEntry] = []
    seen: set[str] = set()
    for r in raw:
        fid = r["faq_id"]
        if fid in seen:
            raise ValueError(f"Duplicate faq_id: {fid!r}")
        seen.add(fid)
        entries.append(
            FaqEntry(
                faq_id=fid,
                category=r.get("category", ""),
                question=r["question"],
                answer=r["answer"],
                keywords=list(r.get("keywords") or []),
                guide_ref=r.get("guide_ref"),
                last_updated=r.get("last_updated"),
            )
        )
    return entries
