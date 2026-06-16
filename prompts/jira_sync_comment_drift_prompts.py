"""
Comment-driven drift prompts.

Developers usually don't edit a story's title/description — they add scope and
clarifications in the COMMENTS. These prompts (1) judge whether the new comments
imply the story has moved away from its requirement, and (2) draft an updated
title/description that folds the implied change in.
"""

import json


# Few-shot exemplars (in-context learning). Deliberately chosen to anchor the
# hard boundaries, not the obvious cases:
#   1. status/noise            -> false
#   2. real scope addition     -> true
#   3. implementation detail   -> false (refactor ≠ requirement change)
#   4. product scope change    -> true
# Order matters for in-context learning; we go noise → drift → tricky-noise →
# drift so the model sees both classes early and the subtle false case mid-list.
_FEWSHOT_EXAMPLES = """Examples (study these, then judge the real comments the same way):

Comment: "[qa] Done, verified on staging. LGTM, deploying tomorrow."
Answer: {"drifted": false, "implied_change": "", "comment_excerpt": ""}

Comment: "[dev] After review we also need to rate-limit this endpoint to 100 req/s and audit-log every denied request."
Answer: {"drifted": true, "implied_change": "The story must now enforce a 100 req/s rate limit and audit-log denied requests — constraints the description doesn't mention.", "comment_excerpt": "we also need to rate-limit this endpoint to 100 req/s and audit-log every denied request"}

Comment: "[dev] Refactored the service layer to use the shared client — no behaviour change, just cleanup."
Answer: {"drifted": false, "implied_change": "", "comment_excerpt": ""}

Comment: "[pm] Product decided this should also support partial refunds, not just full refunds."
Answer: {"drifted": true, "implied_change": "Scope expands to support partial refunds in addition to full refunds.", "comment_excerpt": "also support partial refunds, not just full refunds"}

"""


def build_comment_drift_judge_prompt(
    requirement_id: str,
    title: str,
    description: str,
    requirement_text: str,
    comments_text: str,
    include_examples: bool = True,
) -> str:
    """
    Decide whether the comments indicate the story has effectively drifted from
    its title/description (which were derived from the BRD requirement). Be
    conservative: status updates, questions, "done", "LGTM", links, or pure
    discussion that doesn't change scope/behaviour/acceptance are NOT drift.

    `include_examples` toggles the few-shot block (in-context learning). On by
    default; turned off only to measure the few-shot lift in the eval harness.
    """
    examples_block = _FEWSHOT_EXAMPLES if include_examples else ""
    return f"""A Jira story was generated from a BRD requirement. Its title and description
should still reflect that requirement. Developers often add new scope or change
decisions in the COMMENTS rather than editing the title/description. Decide
whether the comments below mean the story has effectively changed and no longer
matches its title/description.

REQUIREMENT {requirement_id}:
---
{requirement_text or "(requirement text unavailable)"}
---

STORY TITLE:
{title or "(none)"}

STORY DESCRIPTION:
---
{description or "(none)"}
---

NEW COMMENTS (newest first):
---
{comments_text or "(no comments)"}
---

Rules:
  - drifted = true ONLY if a comment introduces or changes scope, behaviour,
    acceptance criteria, constraints, or data such that the title/description is
    now out of date.
  - drifted = false for status notes, questions, blockers, "done"/"LGTM",
    links, deployment notes, or discussion that doesn't change what to build.
  - `implied_change`: one or two sentences describing what the comments imply
    the story should now be (only meaningful when drifted = true).
  - `comment_excerpt`: the single most relevant quoted comment line driving it.

{examples_block}Return ONLY valid JSON, no prose, no markdown fences:
{{
  "drifted": true | false,
  "implied_change": "string (empty when drifted is false)",
  "comment_excerpt": "string (empty when drifted is false)"
}}"""


def build_story_update_prompt(
    requirement_id: str,
    title: str,
    description: str,
    requirement_text: str,
    implied_change: str,
    comments_text: str,
) -> str:
    """Draft an updated title + description that folds the implied change in."""
    return f"""A Jira story has drifted from its title/description because of decisions made
in its comments. Produce an updated title and description that fold the implied
change in, so the story body reflects what the team actually decided.

REQUIREMENT {requirement_id}:
---
{requirement_text or "(requirement text unavailable)"}
---

CURRENT TITLE:
{title or "(none)"}

CURRENT DESCRIPTION:
---
{description or "(none)"}
---

WHAT THE COMMENTS IMPLY:
{implied_change or "(see comments)"}

RELEVANT COMMENTS:
---
{comments_text or "(none)"}
---

Rules:
  1. Keep the same intent and voice/format; change only what the comments imply.
  2. `description` is the full replacement body (not a diff). Preserve parts the
     comments don't touch, including any "Acceptance Criteria:" section.
  3. Don't invent scope the comments didn't introduce.
  4. `change_summary` is one scannable sentence; `rationale` ≤ 3 sentences.

Return ONLY valid JSON, no prose, no markdown fences:
{{
  "title": "string",
  "description": "string",
  "change_summary": "string",
  "rationale": "string"
}}"""
