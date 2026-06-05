"""
Mock RAG over an internal knowledge base.

`internal_knowledge_base.json` holds ~10 hand-curated entries. This module
provides a lightweight keyword-overlap search (no embeddings) that returns
all entries scored against a query. The router pushes results to the UI as
a `state.kb_results` SSE event so the user can see ranked hits and pick
which to consume.

Why no embeddings: this is a POC. Keyword matching is good enough to
demonstrate "agent searches internal docs, ranks them, user curates." If
this graduates to production, swap `_score` for a real embedding call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MOCK_DIR = Path(__file__).parent.parent / "mock_data"

_KB_CACHE: list[dict[str, Any]] | None = None


def _load_kb() -> list[dict[str, Any]]:
    global _KB_CACHE
    if _KB_CACHE is None:
        try:
            with open(MOCK_DIR / "internal_knowledge_base.json", "r", encoding="utf-8") as f:
                _KB_CACHE = json.load(f)
        except Exception as e:
            logger.warning("Could not load internal_knowledge_base.json: %s", e)
            _KB_CACHE = []
    return _KB_CACHE


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _score(query_tokens: set[str], entry: dict[str, Any]) -> float:
    """Score 0..1: overlap of query tokens with title + snippet + keywords."""
    if not query_tokens:
        return 0.0

    keyword_tokens = set()
    for kw in entry.get("keywords", []):
        keyword_tokens.update(_tokenize(kw))
    title_tokens = _tokenize(entry.get("title", ""))
    snippet_tokens = _tokenize(entry.get("snippet", ""))

    keyword_hits = len(query_tokens & keyword_tokens) * 3
    title_hits = len(query_tokens & title_tokens) * 2
    snippet_hits = len(query_tokens & snippet_tokens) * 1

    raw_score = keyword_hits + title_hits + snippet_hits
    max_possible = len(query_tokens) * (3 + 2 + 1)
    return min(1.0, raw_score / max_possible) if max_possible else 0.0


@dataclass
class KBSearchResult:
    id: str
    title: str
    url: str
    snippet: str
    type: str
    icon: str
    relevance: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "type": self.type,
            "icon": self.icon,
            "relevance": round(self.relevance, 3),
        }


def search(query: str, limit: int = 10) -> list[KBSearchResult]:
    """Return all KB entries scored against the query, sorted highest first.
    Up to `limit` entries; lower-scored ones are returned anyway (with low
    relevance) so the UI can dim them — that's part of the demo story.
    """
    kb = _load_kb()
    if not kb:
        return []
    qt = _tokenize(query)
    scored: list[KBSearchResult] = []
    for entry in kb:
        scored.append(
            KBSearchResult(
                id=entry["id"],
                title=entry["title"],
                url=entry["url"],
                snippet=entry["snippet"],
                type=entry.get("type", "other"),
                icon=entry.get("icon", "other"),
                relevance=_score(qt, entry),
            )
        )
    scored.sort(key=lambda r: r.relevance, reverse=True)
    return scored[:limit]


def fetch_full_content(kb_id: str) -> tuple[dict[str, Any], str] | None:
    """Return (entry_dict, full_text) for a KB id, or None if not found.

    For entries with a `source_file` pointer (the 3 real fixtures), reads
    the existing fixture file and returns its content. For entries with
    inline `content`, returns that directly.
    """
    kb = _load_kb()
    entry = next((e for e in kb if e.get("id") == kb_id), None)
    if not entry:
        return None

    if "content" in entry and isinstance(entry["content"], str):
        return entry, entry["content"]

    source_file = entry.get("source_file")
    source_format = entry.get("source_format", "")
    if source_file:
        path = MOCK_DIR / source_file
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            logger.warning("KB %s source_file %s unreadable: %s", kb_id, source_file, e)
            return entry, f"[Could not load source file: {source_file}]"

        if source_format == "confluence-json":
            try:
                data = json.loads(raw)
                title = data.get("title", "")
                body = data.get("body", "")
                owner = data.get("owner", "")
                return entry, f"# {title}\n\n(Owner: {owner})\n\n{body}"
            except Exception:
                return entry, raw
        if source_format == "jira-json":
            try:
                data = json.loads(raw)
                key = data.get("key", "")
                summary = data.get("summary", "")
                status = data.get("status", "")
                desc = data.get("description", "")
                comments = data.get("comments", [])
                comments_text = "\n".join(
                    f"  - {c.get('author', '')} ({c.get('created', '')}): {c.get('body', '')}"
                    for c in comments
                )
                return entry, (
                    f"# {key}: {summary}\n\n"
                    f"Status: {status}\n\n"
                    f"## Description\n{desc}\n\n"
                    f"## Comments\n{comments_text}"
                )
            except Exception:
                return entry, raw
        # markdown or anything else — return as-is
        return entry, raw

    return entry, entry.get("snippet", "")


def lookup_by_id_or_title(needle: str) -> dict[str, Any] | None:
    """Resolve a user-typed string like 'kb-001', '01', or '"ResponseAI proposal"'
    to a KB entry."""
    kb = _load_kb()
    needle = (needle or "").strip().lower().strip("\"'")
    if not needle:
        return None

    # Direct id match (kb-001)
    for entry in kb:
        if entry["id"].lower() == needle:
            return entry

    # Numeric shorthand: "01" -> "kb-001"
    if needle.isdigit():
        padded = f"kb-{int(needle):03d}"
        for entry in kb:
            if entry["id"] == padded:
                return entry

    # Title substring (case-insensitive)
    for entry in kb:
        title = entry["title"].lower()
        if needle in title:
            return entry
    return None
