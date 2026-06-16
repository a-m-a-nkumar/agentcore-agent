"""
BRD Diff agent prompt.

Given the previous and current bodies of a BRD page, the agent returns a
structured list of requirements that changed, with a severity classification.
The orchestrator then routes each change to the Reconcile agent.
"""


def build_brd_diff_prompt(
    previous_page_text: str,
    current_page_text: str,
    page_title: str = "",
) -> str:
    return f"""You are reconciling two versions of a Business Requirements Document.
The document was edited on Confluence, bumping its version number. Your job is
to identify which functional requirements (FR-N) and non-functional requirements
(NFR-N) actually changed in meaning between the two versions, and classify how
significant each change is.

PREVIOUS VERSION
TITLE: {page_title}
---
{previous_page_text}
---

CURRENT VERSION
TITLE: {page_title}
---
{current_page_text}
---

Severity definitions (apply strictly):
  - MAJOR    Behaviour-altering change. A number, a contract, an acceptance
             criterion, or an externally observable behaviour was changed in
             a way that requires downstream Jira stories or test scenarios to
             be updated.
  - MINOR    Wording cleanup, tightened phrasing, or non-contractual edit
             that doesn't change the externally observable behaviour or
             acceptance criteria.
  - ADDED    A requirement appears in the CURRENT version with no matching
             entry (same ID) in the PREVIOUS version.
  - REMOVED  A requirement appears in the PREVIOUS version with no matching
             entry in the CURRENT version.

Rules:
1. Only list requirements that actually changed. Do NOT emit entries for
   requirements with identical text in both versions.
2. `requirement_id` must be normalised: "FR-7" not "FR-007", "NFR-3" not
   "NFR-03". Strip any leading zeros and any trailing title prose.
3. `summary` is ONE concise sentence describing the change in plain language
   (e.g. "Throttle rate changed from 100/sec to 250/sec with burst tolerance").
4. `old_text` is the verbatim body of the requirement from the PREVIOUS version
   (or null for ADDED).
5. `new_text` is the verbatim body of the requirement from the CURRENT version
   (or null for REMOVED).
6. Do NOT propose any downstream Jira / test updates. That is a separate agent.
7. Skip pure formatting / whitespace / heading-style changes.

Return ONLY valid JSON in this exact shape, no prose, no markdown fences:
{{
  "changes": [
    {{
      "requirement_id": "FR-7",
      "severity": "MAJOR" | "MINOR" | "ADDED" | "REMOVED",
      "summary": "string",
      "old_text": "string or null",
      "new_text": "string or null"
    }}
  ]
}}"""
