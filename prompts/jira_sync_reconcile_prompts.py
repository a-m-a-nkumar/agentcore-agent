"""
Reconcile agent prompt.

For a single (changed requirement × linked downstream artifact), the agent
proposes how the artifact should be updated to stay in sync with the new
requirement text, plus a confidence score and a short rationale.

Called once per `proposed_artifact_updates` row.
"""

import json


def build_reconcile_gate_prompt(
    requirement_id: str,
    severity: str,
    old_requirement_text: str,
    new_requirement_text: str,
    artifact_type: str,
    artifact_id: str,
    artifact_summary: str,
) -> str:
    """
    CHEAP triage gate, run before the (expensive) reconcile. Decides only whether
    a requirement change affects ONE downstream artifact enough to need updating —
    so we skip the full reconcile call for artifacts a change doesn't touch.

    Conservative by design: when unsure it must answer true, because a wrong
    'true' only costs one extra reconcile, while a wrong 'false' silently drops a
    real propagation.
    """
    return f"""A requirement in a BRD changed. Decide ONLY whether this change affects the
ONE downstream artifact below enough that it needs updating. This is a fast,
cheap triage before a more expensive update is drafted — be decisive.

REQUIREMENT {requirement_id} (severity: {severity}) changed:
PREVIOUS:
{old_requirement_text or "(new requirement — no previous text)"}
CURRENT:
{new_requirement_text or "(requirement was removed)"}

DOWNSTREAM ARTIFACT — {artifact_type} {artifact_id}:
{artifact_summary or "(no summary available)"}

Rules:
  - affects = true if the change plausibly alters what this artifact should say,
    do, or test.
  - affects = false ONLY if the change clearly does not touch this artifact's
    scope (e.g. it changes a different requirement, or is pure wording cleanup).
  - When uncertain, answer true. A wrong 'true' just costs one extra step; a
    wrong 'false' silently drops a real update.

Return ONLY valid JSON, no prose, no markdown fences:
{{
  "affects": true | false,
  "reason": "one short sentence"
}}"""


def build_reconcile_prompt(
    requirement_id: str,
    severity: str,
    old_requirement_text: str,
    new_requirement_text: str,
    artifact_type: str,            # "jira_story" or "test_scenario"
    artifact_id: str,              # "PROJ-22" or "TS-012"
    artifact_current_content: dict,
) -> str:
    """
    The output schema is conditional on artifact_type so the LLM doesn't
    have to guess the shape. The orchestrator parses based on artifact_type
    when storing the proposal.
    """
    current_block = json.dumps(artifact_current_content, indent=2, sort_keys=True)

    if artifact_type == "jira_story":
        schema_block = """  "action": "UPDATE_IN_PLACE" | "NEW_ARTIFACT" | "NO_CHANGE",
  "proposed": {
    "title": "string",
    "description": "string",
    "acceptance_criteria": ["string", "string"],
    "story_points": 5,
    "priority": "Low" | "Medium" | "High"
  },
  "confidence": 0.0 - 1.0,
  "rationale": "string (one paragraph, ≤ 4 sentences)"
"""
        artifact_label = "Jira story"
    elif artifact_type == "test_scenario":
        schema_block = """  "action": "UPDATE_IN_PLACE" | "NEW_ARTIFACT" | "NO_CHANGE",
  "proposed": {
    "title": "string",
    "steps": ["string", "string", "string"]
  },
  "confidence": 0.0 - 1.0,
  "rationale": "string (one paragraph, ≤ 4 sentences)"
"""
        artifact_label = "test scenario"
    else:
        # Defensive fallback so the agent never sees an unfamiliar type.
        schema_block = """  "action": "UPDATE_IN_PLACE" | "NEW_ARTIFACT" | "NO_CHANGE",
  "proposed": {},
  "confidence": 0.0 - 1.0,
  "rationale": "string"
"""
        artifact_label = artifact_type

    return f"""You are propagating a change in a Business Requirements Document down to
a single artifact that was generated from that requirement. Your job is to
propose a minimally invasive update to the artifact so it stays consistent
with the new requirement.

SOURCE REQUIREMENT
ID: {requirement_id}
Change severity: {severity}

PREVIOUS requirement text:
---
{old_requirement_text or "(no previous text — this requirement is new)"}
---

CURRENT requirement text:
---
{new_requirement_text or "(no current text — this requirement was removed)"}
---

DOWNSTREAM ARTIFACT
Type: {artifact_label}
ID: {artifact_id}
Current content (JSON snapshot from when it was generated):
---
{current_block}
---

Action rules:
  - UPDATE_IN_PLACE → the artifact still serves the same purpose but needs
                      field-level edits to reflect the new requirement.
                      Use this for almost all MAJOR / MINOR severities.
  - NEW_ARTIFACT    → the change is so structural that a new artifact should
                      be created alongside (e.g. a new test scenario for a
                      newly-added burst behaviour) rather than modifying the
                      existing one.
  - NO_CHANGE       → the requirement change does not affect this artifact
                      (e.g. wording cleanup that doesn't touch its scope).

Field rules:
  1. Preserve fields you have no reason to change. Do not invent priorities or
     story points; keep the existing values unless the requirement implies a
     change (e.g. larger scope -> higher points).
  2. `acceptance_criteria` must be a list of standalone, testable statements,
     not a single concatenated paragraph.
  3. `confidence` is your subjective certainty that this proposal is correct,
     in [0.00, 1.00]. Below 0.5 means the human reviewer should probably
     edit it. Above 0.85 means you are quite sure.
  4. `rationale` is for a reviewer scanning the proposal — explain WHY this
     change (or non-change) is justified, referencing the requirement diff.
  5. If action is NO_CHANGE, `proposed` may mirror the current content.
  6. Do not invent new tests / story content unrelated to the requirement.

Return ONLY valid JSON in this exact shape, no prose, no markdown fences:
{{
{schema_block}}}"""
