"""
Utility for normalizing requirement IDs from LLM output.

Handles variations like:
  "FR-001: User registration with email" -> "FR-1"
  "NFR-003: Performance targets"         -> "NFR-3"
  "FR-7"                                 -> "FR-7"
  "BR-010"                               -> "BR-10"
"""

import re

_REQUIREMENT_PATTERN = re.compile(r'(FR|NFR|BR)-0*(\d+)')


def normalize_requirement_id(raw: str) -> str:
    """
    Extract and normalize a requirement ID from a mapped_to_requirement string.

    Strips leading zeros from the numeric part and discards any trailing title.
    Returns the raw string unchanged if no known pattern is found.
    """
    if not raw:
        return raw

    match = _REQUIREMENT_PATTERN.search(raw)
    if match:
        prefix, number = match.group(1), match.group(2)
        return f"{prefix}-{number}"

    return raw.strip()
