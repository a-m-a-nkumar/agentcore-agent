"""
BRD Q&A prompt — answer informational questions about an existing BRD
with section-level citations.

Mirrors prompts/sad_qa_prompts.py with two BRD-specific adjustments:
  1. BRD sections are dynamic (no fixed 10-section list) so the
     "relevant sections" payload carries whatever the orchestrator
     retrieved.
  2. BRD also has a long-term semantic facts buffer (AgentCore Memory)
     that may answer the question even when no section does — the
     handler may inject those facts as a separate "Known project
     context" block.
"""

import json
from typing import Any, Dict, List, Optional


QA_SYSTEM_PROMPT = """\
You answer questions about a Business Requirements Document (BRD).

You receive:
  • The user's question.
  • A list of relevant BRD sections (number, title, JSON content).
  • Optional "known project context" — long-term facts gathered across
    prior sessions (only present when use_long_term_context is True).

Output ONLY a JSON object:

  {
    "answer": "<conversational answer, 1-3 sentences>",
    "citations": [
      {
        "source": "section" | "fact",
        "section_number": <int or null>,
        "section_title": "<title or empty>",
        "snippet": "<short verbatim excerpt, max 120 chars>"
      }
    ]
  }

Rules:
  • Ground the answer STRICTLY in the provided sections + context. If
    the answer isn't supported by either, say so explicitly:
        "The BRD doesn't cover that yet — want me to draft it?"
  • At least one citation per substantive claim. Cite by section_number
    when the claim comes from a section; cite source="fact" when it
    comes from the project-context facts buffer (section_number=null).
  • Snippet must be verbatim from the source (no paraphrasing). Trim to
    ≤ 120 chars; ellipsis if truncated.
  • Never speculate or fill gaps with general knowledge.
  • Never invent section numbers or titles. If only one section was
    provided and it's empty, return source="none" with snippet="".
"""


def build_qa_prompt(
    *,
    question: str,
    relevant_sections: List[Dict[str, Any]],
    known_facts: Optional[List[str]] = None,
) -> str:
    """
    Compose the user-content block for the Q&A call.

    Args:
        question: The user's question, verbatim.
        relevant_sections: List of {number, title, content} dicts the
            orchestrator selected as topical matches.
        known_facts: Optional long-term facts from AgentCore Memory
            (formatted strings, e.g. "stack: AWS Bedrock + Postgres").
            Only present when session.use_long_term_context is True.
    """
    rendered_sections: List[str] = []
    for s in relevant_sections:
        rendered_sections.append(f"--- Section {s.get('number')}: {s.get('title')} ---")
        rendered_sections.append(json.dumps(s.get("content", []), indent=2))
        rendered_sections.append("")

    facts_block = ""
    if known_facts:
        facts_block = (
            "\nKnown project context (from prior sessions):\n"
            + "\n".join(f"  - {f}" for f in known_facts)
            + "\n"
        )

    sections_text = (
        "\n".join(rendered_sections)
        if rendered_sections else "(no sections matched this question)"
    )

    return (
        f"Question: {question}\n"
        f"{facts_block}\n"
        f"Relevant BRD sections:\n"
        f"```\n{sections_text}\n```\n\n"
        f"Return JSON answer."
    )
