"""
Section EDIT and SUGGEST prompts.

EDIT: takes the user's natural-language instruction + the current section's
JSON content, returns an updated JSON content array. Strictly preserves the
section's required shape (e.g. ARSR's two tables with fixed category rows
must remain after the edit).

SUGGEST: returns 3-5 actionable suggestions the user could click "Apply"
on. Each suggestion is structured so the frontend can turn the click into
a follow-up `EDIT_SECTION` or `REGENERATE_SECTION` call.
"""

import json
from typing import Any, Dict, List


EDIT_SYSTEM_PROMPT = """\
You are editing one section of a Software Architecture Document.

You receive:
  • the section's current content (JSON content blocks)
  • the user's instruction
  • optional context (BRD, diagram XML, facts) for grounding

You output ONLY the updated JSON content array — same shape as the input
(paragraph / heading / bullet_list / ordered_list / table). No prose, no
explanation, no markdown fences.

Rules:
  • Preserve the section's structural skeleton:
      - Tables keep their headers and category rows; you change cell content.
      - Ordered/bullet lists may grow or shrink, but stay the same kind.
      - The order of blocks is preserved unless the user explicitly asks to
        reorder.
  • Apply only the user's instruction. Do not rewrite content the
    instruction didn't ask to change.
  • If the instruction is ambiguous, prefer the safest minimal interpretation.
  • Never delete a fixed-template row (e.g. a required ARSR category) —
    set its cells to "" or "(to be confirmed)" instead.
"""


def build_edit_prompt(
    *,
    section_number: int,
    section_title: str,
    current_content: List[Dict[str, Any]],
    user_instruction: str,
    brd_excerpt: str = "",
    diagram_xml: str = "",
) -> str:
    parts = [
        f"Section {section_number}: {section_title}",
        "",
        "Current content (JSON):",
        "```json",
        json.dumps(current_content, indent=2),
        "```",
        "",
        f"User instruction: {user_instruction}",
    ]
    if brd_excerpt:
        parts += ["", "BRD excerpt for grounding:", brd_excerpt]
    if diagram_xml and section_number in (4, 6, 7):
        parts += ["", "Diagram XML:", "```xml", diagram_xml[:4000], "```"]
    parts += ["", "Output ONLY the updated JSON content array."]
    return "\n".join(parts)


# ============================================
# Suggest prompt
# ============================================

SUGGEST_SYSTEM_PROMPT = """\
You generate concrete, actionable suggestions for improving one SAD section.

Output ONLY a JSON object:

  {
    "items": [
      {
        "title": "<short headline, e.g. 'Add a measurable RTO target'>",
        "rationale": "<one sentence why this matters>",
        "apply_intent": "EDIT_SECTION",  # or REGENERATE_SECTION
        "edit_instruction": "<imperative an EDIT_SECTION handler can run verbatim>"
      }
    ]
  }

Rules:
  • 3-5 items. Quality over quantity.
  • Suggestions must be specific to THIS section's content, not generic.
  • If the section is well-populated and you can't find anything substantive,
    return {"items": []}.
  • Never propose changes that violate the section's template shape (e.g.
    don't suggest dropping a required ARSR category row).
"""


def build_suggest_prompt(
    *,
    section_number: int,
    section_title: str,
    current_content: List[Dict[str, Any]],
    audit_issues: List[Dict[str, str]],
    brd_excerpt: str = "",
) -> str:
    issues_block = (
        "\n".join(f"  - {i.get('code')}: {i.get('msg')}" for i in audit_issues)
        if audit_issues else "(no audit issues recorded)"
    )
    return (
        f"Section {section_number}: {section_title}\n\n"
        f"Current content (JSON):\n"
        f"```json\n{json.dumps(current_content, indent=2)}\n```\n\n"
        f"Recent audit issues:\n{issues_block}\n\n"
        f"BRD excerpt:\n{brd_excerpt or '(none)'}\n\n"
        f"Output suggestions JSON."
    )
