"""
Utilities for content hashing used by the lineage system.

Provides deterministic SHA-256 hashing of text content and
section extraction from Confluence HTML pages.
"""

import hashlib
import re
from html import unescape


def hash_text(text: str) -> str:
    """Return the SHA-256 hex digest of the given text (stripped of surrounding whitespace)."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _strip_html(html: str) -> str:
    """Remove HTML tags, unescape entities, and collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_section_text(html_content: str, requirement_id: str) -> str:
    """
    Given full Confluence page HTML and a requirement ID (e.g. 'FR-7'),
    extract the text of just that requirement's section.

    Looks for headings or table cells containing the requirement ID and
    captures everything until the next requirement heading of the same level.
    Falls back to the full page text if the section can't be isolated.
    """
    if not html_content or not requirement_id:
        return _strip_html(html_content) if html_content else ""

    # Build a pattern that matches the requirement ID with optional leading zeros
    # e.g. FR-7 matches FR-7, FR-07, FR-007
    parts = requirement_id.split('-')
    if len(parts) == 2:
        prefix, number = parts[0], parts[1]
        id_pattern = rf'{prefix}-0*{number}\b'
    else:
        id_pattern = re.escape(requirement_id)

    # Try to find section boundaries using heading tags (h1-h6) or bold/strong markers
    # Pattern: find start of section containing the requirement ID
    section_start = re.search(
        rf'<(?:h[1-6]|th|td|strong|b)[^>]*>[^<]*{id_pattern}[^<]*</(?:h[1-6]|th|td|strong|b)>',
        html_content,
        re.IGNORECASE
    )

    if section_start:
        start_pos = section_start.start()
        # Find the next heading of the same level or next requirement ID
        remaining = html_content[section_start.end():]
        next_section = re.search(
            rf'<(?:h[1-6]|th|td|strong|b)[^>]*>[^<]*(?:FR|NFR|BR)-\d+',
            remaining,
            re.IGNORECASE
        )
        if next_section:
            end_pos = section_start.end() + next_section.start()
        else:
            end_pos = len(html_content)

        section_html = html_content[start_pos:end_pos]
        return _strip_html(section_html)

    # Fallback: return full page text
    return _strip_html(html_content)
