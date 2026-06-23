"""
FAQ synthesis fragments (spec §6 / §7 / §8).

These extend the EXISTING synthesis call — no second LLM call. `prompts.py`
appends `FAQ_SYNTHESIS_ADDENDUM` to `SYNTHESIS_SYSTEM`; `router.py` appends the
rendered `RELATED_FAQ` block(s) to the synthesis user message.
"""

from __future__ import annotations

import json


# Appended to SYNTHESIS_SYSTEM so the synthesizer knows how to weight a FAQ block
# and how to dedupe against an overlapping guide section. Grounding contract
# (only supplied content/links) is unchanged.
FAQ_SYNTHESIS_ADDENDUM = """

You may ALSO be given a RELATED_FAQ block (one or more curated Q&A pairs) and an
OVERLAP flag:
- RELATED_FAQ: short, canonical answers to common questions, each as {faq_id, question, answer}.
- OVERLAP: true when a related FAQ covers the SAME topic as the guide SECTION above.

How to use RELATED_FAQ:
- Treat the FAQ answer as ground truth you may use, exactly like a SECTION — use ONLY
  its supplied text, never invent beyond it.
- If there is NO guide SECTION (FAQ-only answer), answer directly and concisely from the
  FAQ; do not apologise or claim the guide lacks it.
- When OVERLAP is true, the SECTION and the FAQ describe the same thing: produce ONE
  answer — prefer the FAQ's concise phrasing, use the SECTION for any extra depth, emit
  the guide_link exactly once, and never repeat the same steps twice.
- When OVERLAP is false but a FAQ is present, weave its answer in naturally where it adds
  value; don't force it if it's unrelated to what the user actually asked."""


def render_faq_block(candidates: list[tuple]) -> str:
    """Render top-N (entry, score) hits into the RELATED_FAQ user-message block.
    Scores are omitted from the prompt — they gate inclusion, they aren't content."""
    items = [
        {"faq_id": entry.faq_id, "question": entry.question, "answer": entry.answer}
        for entry, _score in candidates
    ]
    return "RELATED_FAQ:\n" + json.dumps(items, ensure_ascii=False)
