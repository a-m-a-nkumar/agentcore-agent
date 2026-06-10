"""
Drift amendment prompt.

When a Jira story has drifted from its BRD requirement and the team decides the
BRD (not the story) is out of date, this prompt asks the model to propose an
amended requirement text that brings the BRD in line with the story.

The result is staged for human review (stored on the drift row) — it is never
written to Confluence automatically.
"""

import json


def build_brd_amendment_prompt(
    requirement_id: str,
    current_requirement_text: str,
    drifted_artifact: dict,
    page_title: str = "",
) -> str:
    artifact_block = json.dumps(drifted_artifact or {}, indent=2, sort_keys=True)

    return f"""You maintain a Business Requirements Document. A linked Jira story was edited
by hand and now says something the BRD requirement does not. The team has
decided the STORY is correct and the BRD is out of date. Propose an amended
requirement text that brings the BRD in line with the story.

BRD PAGE: {page_title or "(untitled)"}
REQUIREMENT ID: {requirement_id}

CURRENT requirement text (what the BRD says today):
---
{current_requirement_text or "(not found — propose fresh text for this requirement)"}
---

DRIFTED JIRA STORY (the source of truth now):
---
{artifact_block}
---

Rules:
  1. `amended_text` is the full replacement text for this ONE requirement,
     written in the same voice and format as the current requirement — not a
     diff, not commentary.
  2. Change only what the story implies. Preserve any part of the requirement
     the story does not contradict.
  3. Keep the same requirement id and intent; you are reconciling wording and
     scope, not inventing new requirements.
  4. `change_summary` is one sentence a reviewer can scan.
  5. `rationale` (≤ 3 sentences) explains what in the story drove the change.

Return ONLY valid JSON in this exact shape, no prose, no markdown fences:
{{
  "requirement_id": "{requirement_id}",
  "amended_text": "string",
  "change_summary": "string",
  "rationale": "string"
}}"""
