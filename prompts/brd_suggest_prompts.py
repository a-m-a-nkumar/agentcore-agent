"""
BRD SUGGEST prompt — propose 3-5 concrete improvements for one BRD section.

Adapted from prompts/sad_edit_prompts.py:SUGGEST_SYSTEM_PROMPT. BRD has
no fixed-template constraint like SAD's ARSR tables, so the prompt is
slightly looser on shape — but tighter on grounding (suggestions must
cite the user's actual stated facts when applicable).

Each suggestion is structured so the frontend can turn a click on
"Apply" into a follow-up EDIT_SECTION or REGENERATE_SECTION dispatch
with a verbatim edit_instruction.
"""

import json
from typing import Any, Dict, List, Optional


SUGGEST_SYSTEM_PROMPT = """\
You generate concrete, actionable suggestions for improving ONE section
of a Business Requirements Document (BRD).

You receive:
  • The section's current content (JSON content blocks).
  • Recent audit issues for the section (may be empty).
  • Optional "known project context" — long-term facts the user has
    established across prior sessions.

Output ONLY a JSON object:

  {
    "items": [
      {
        "title": "<short headline, e.g. 'Add measurable performance NFR'>",
        "rationale": "<one sentence why this matters, tied to a fact if applicable>",
        "apply_intent": "EDIT_SECTION" | "REGENERATE_SECTION",
        "edit_instruction": "<imperative the handler runs verbatim>"
      }
    ]
  }

Rules:
  • 3-5 items. Quality over quantity. Empty array if nothing substantive.
  • Be SPECIFIC to this section's actual content. Generic "consider
    adding X" suggestions are not useful.
  • When a known-project-context fact is relevant, reference it in the
    rationale ("you mentioned 10K concurrent users — §7 should specify
    the autoscaling strategy that supports this").
  • Prefer apply_intent="EDIT_SECTION" for focused tweaks; use
    "REGENERATE_SECTION" only when the section needs a full rewrite.
  • edit_instruction must be runnable by an editor handler verbatim —
    no placeholders like "<the user's name>" or "[TBD]".
"""


def build_suggest_prompt(
    *,
    section_number: int,
    section_title: str,
    current_content: List[Dict[str, Any]],
    audit_issues: Optional[List[Dict[str, str]]] = None,
    known_facts: Optional[List[str]] = None,
) -> str:
    """
    Compose the user-content block for the SUGGEST call.

    Args:
        section_number: Section being suggested for.
        section_title: Section's current title.
        current_content: JSON content blocks of the section.
        audit_issues: Optional list of {code, msg} issues from the
            most recent AUDIT pass. Drives "fix this gap" suggestions.
        known_facts: Optional long-term project context. When present,
            the rationale should cite the relevant fact.
    """
    issues_block = (
        "\n".join(
            f"  - {i.get('code', 'ISSUE')}: {i.get('msg', '')}"
            for i in (audit_issues or [])
        )
        if audit_issues else "(no audit issues recorded)"
    )
    facts_block = (
        "\n".join(f"  - {f}" for f in known_facts)
        if known_facts else "(no project context loaded for this session)"
    )

    return (
        f"Section {section_number}: {section_title}\n\n"
        f"Current content (JSON):\n"
        f"```json\n{json.dumps(current_content, indent=2)}\n```\n\n"
        f"Recent audit issues:\n{issues_block}\n\n"
        f"Known project context (from prior sessions):\n{facts_block}\n\n"
        f"Output suggestions JSON."
    )
