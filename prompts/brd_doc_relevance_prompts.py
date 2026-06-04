"""
INGEST_DOC handler prompts.

When the user attaches a document mid-conversation (single-file path),
the orchestrator runs TWO LLM calls on the doc:

  1. Relevance classifier — score the doc against each existing BRD
     section so the handler knows which sections to propose
     regenerating. Cheap (T=0, ~200 tokens).
  2. Fact extraction       — pull stakeholders / NFRs / constraints /
     integrations / assumptions / open-questions from the doc text and
     append to the long-term facts buffer. Slightly larger
     (T=0.3, ~800 tokens).

Modeled on the inline doc-relevance classifier at
lambda_sad_orchestrator.py:904-931 plus the SEMANTIC override prompt
shape used by prompts/brd_facts_extraction_prompt.py.
"""

import json
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# 1. Relevance classifier — which BRD sections does this doc inform?
# ---------------------------------------------------------------------------

DOC_RELEVANCE_SYSTEM_PROMPT = """\
You classify whether an uploaded document is relevant to each section of
a Business Requirements Document.

You receive:
  • Document filename + extracted text (first ~4K chars).
  • The list of BRD sections (number + title) currently in the draft.

Output ONLY a JSON object:

  {
    "suggested_sections": [<int>, <int>, ...],
    "summary": "<one-sentence summary of what the doc contributes>"
  }

Rules:
  • Return 0-5 section numbers, ordered by relevance (most relevant first).
  • Only include a section if the doc materially informs it.
  • If the doc is off-topic for the entire BRD, return suggested_sections=[]
    with summary="document is not relevant to this BRD".
  • The summary is shown to the user verbatim, so be concrete: "Tech spec
    documenting the order-events SQS topic" beats "general background".
"""


def build_doc_relevance_prompt(
    *,
    filename: str,
    doc_text: str,
    available_sections: List[Dict[str, Any]],
) -> str:
    """User-content block for the relevance classifier."""
    sections_block = "\n".join(
        f"  {s['number']}. {s.get('title') or '(untitled)'}"
        for s in available_sections
        if s.get("number") is not None
    ) or "  (no sections yet)"

    # Cap doc_text at 4000 chars to keep the input cost bounded. The
    # full doc still goes to the fact-extraction call below.
    excerpt = (doc_text or "")[:4000]
    if len(doc_text or "") > 4000:
        excerpt += "\n... (truncated for relevance classification)"

    return (
        f"Document: {filename}\n\n"
        f"Document text (excerpt):\n"
        f"```\n{excerpt}\n```\n\n"
        f"BRD sections currently in the draft:\n"
        f"{sections_block}\n\n"
        f"Return suggested_sections JSON."
    )


# ---------------------------------------------------------------------------
# 2. Fact extraction — pull structured facts the doc reveals.
# ---------------------------------------------------------------------------
# Kept separate from prompts/brd_facts_extraction_prompt.py because the
# *AgentCore Memory* strategy override runs against chat events, while
# this one runs against an uploaded document. Same JSON schema so both
# feed the same facts buffer.

DOC_FACTS_SYSTEM_PROMPT = """\
You extract structured project facts from an uploaded document.

You receive:
  • Document filename + extracted text.

Output ONLY a JSON object with these six keys (each value is a list):

  {
    "stakeholders":             [{"name": "...", "role": "...", "team": "..."}],
    "non_functional_reqs":      [{"category": "scale|latency|...", "value": "..."}],
    "constraints":              [{"type": "deadline|budget|mandate", "value": "..."}],
    "integrations":             [{"system": "...", "interaction": "..."}],
    "assumptions":              [{"statement": "..."}],
    "open_questions":           [{"question": "...", "blocks_section": "<title or empty>"}]
  }

Rules:
  • Only extract facts the document explicitly states. Do not hallucinate
    or fill gaps with general knowledge.
  • Skip categories the document doesn't address — empty list is fine.
  • Each fact must be a standalone statement readable out of context.
  • Quote the document where possible; do not paraphrase aggressively.
  • Do not include opinions, recommendations, or marketing language as
    facts. "We believe X is great" is NOT a fact.
"""


def build_doc_facts_prompt(*, filename: str, doc_text: str) -> str:
    """User-content block for the fact-extraction call."""
    return (
        f"Document: {filename}\n\n"
        f"Document text:\n"
        f"```\n{doc_text or ''}\n```\n\n"
        f"Return facts JSON."
    )
