"""
Q&A prompt — answer informational questions about the SAD with section-level
citations.

The handler turns the user's question into a small RAG over the SAD's own
sections (each section_number/section_title becomes a retrievable source).
This prompt formats that into a grounded answer.
"""

import json
from typing import Any, Dict, List


QA_SYSTEM_PROMPT = """\
You answer questions about a Software Architecture Document.

You receive:
  • the user's question
  • a list of relevant SAD sections (with their content)

You output ONLY a JSON object:

  {
    "answer": "<conversational answer, 1-3 sentences>",
    "citations": [
      {"section_number": <int>, "section_title": "<title>", "snippet": "<short excerpt>"}
    ]
  }

Rules:
  • Ground the answer strictly in the provided sections. If the answer
    isn't supported, say so explicitly: "The SAD doesn't cover that yet."
  • At least one citation per substantive claim. Cite the section number
    and a short verbatim snippet (≤ 120 chars).
  • Don't speculate or fill gaps with general knowledge.
"""


def build_qa_prompt(
    *,
    question: str,
    relevant_sections: List[Dict[str, Any]],
) -> str:
    rendered: List[str] = []
    for s in relevant_sections:
        rendered.append(f"--- Section {s.get('number')}: {s.get('title')} ---")
        rendered.append(json.dumps(s.get("content", []), indent=2))
        rendered.append("")
    return (
        f"Question: {question}\n\n"
        f"Relevant SAD sections:\n"
        f"```\n{chr(10).join(rendered) or '(no sections matched)'}\n```\n\n"
        f"Return JSON answer."
    )
