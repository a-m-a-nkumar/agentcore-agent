"""
Per-section BRD prompt builders (Phase 6).

Two exports drive the in-Lambda parallel section generator:

  SHARED_SYSTEM_PROMPT  — the cacheable prose block. Contains the
                          template-fidelity rules, tiered sourcing
                          guidance, empty handling, no-padding rules,
                          cross-section reference policy, and the
                          output JSON schema. It's stable across every
                          section call within a generation, so we mark
                          the system block with cache_control: ephemeral
                          and Anthropic Sonnet 4.5 caches it for 5 min.

  SECTION_PROMPT_BUILDERS — dict[int, Callable[[Dict], str]] keyed by
                          section number (1..16). Each builder returns
                          the *short* per-section user-message body —
                          section-specific guidance only, no context
                          duplication. Context lives in the cached
                          prefix once.

Helpers:

  build_cached_system_blocks(context_bundle: dict) -> list[dict]
      Produces the [SHARED_SYSTEM_PROMPT block, JSON spec+context block]
      pair the Lambda passes as `system_prompt=` to chat_completion.
      The JSON block carries cache_control so the whole prefix is cached
      as one unit.

  build_section_user_message(n: int, context: dict | None = None) -> str
      Returns the per-section instruction for section `n`. Looks up the
      builder by number, raises KeyError on bad input.

Architecture: see C:/Users/T479888/.claude/plans/hazy-gliding-hammock.md
"Phase 6 — Parallel section generation with prompt caching".
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from prompts.brd_section_definitions import (
    BRD_SECTIONS,
    SECTION_FORMATS,
    section_title,
)


# ============================================================================
# SHARED_SYSTEM_PROMPT — the cacheable rules block.
# Stays exactly the same across every section call within a generation.
# Token count target: ~1.5K tokens (well over Sonnet 4.5's 1024 cache
# minimum). See .scratch/gateway_cache_passthrough_test.py for proof
# the DLX gateway forwards cache_control through to Bedrock.
# ============================================================================

SHARED_SYSTEM_PROMPT = """\
You are writing ONE section of a Deluxe Business Requirements Document
(BRD). The document is composed of 16 sections defined by a fixed
Deluxe template. Other parallel agents are concurrently writing the
other 15 sections from the same source input. You must not duplicate
their work, contradict their work, or rewrite the template.

═══════════════════════════════════════════════════════════════════════
TEMPLATE FIDELITY — non-negotiable
═══════════════════════════════════════════════════════════════════════

The section you are assigned has a FIXED format. The per-section
format specs live in the JSON `template_section_specs` block in the
context bundle (next system message). Your output must match the
assigned section's spec exactly:

  - "table" sections: produce one `table` content block whose
    `headers` array matches the spec's headers verbatim, in the
    same order, with no extra and no missing columns. Each row in
    `rows` has exactly as many cells as there are headers.
  - "prose" sections: produce one or more `paragraph` content
    blocks. No table, no bullets.
  - "bullet_list" sections: produce one `bullet_list` block. No
    paragraphs, no tables.
  - "subsection_bullets" sections (only §5 Scope): produce two
    `heading` blocks (level 3) followed by one `bullet_list` each,
    one per subsection in the spec, in the spec's order.
  - "glossary" sections: produce one `bullet_list` block where each
    item is formatted "Term — definition".

Never reorder, skip, merge, or split sections. Never change column
counts or names. Never replace prose with a table or vice versa.

═══════════════════════════════════════════════════════════════════════
CONTENT SOURCING — three tiers, strict precedence
═══════════════════════════════════════════════════════════════════════

The context bundle below contains source material: a transcript, a
chat history, extracted facts, or any combination. Treat that as the
ONLY source of truth for content.

  TIER 1 — Explicit facts in the input.
    Use these directly. If the input says "Sarah Johnson, Product
    Owner, owns backlog prioritization", write exactly that.

  TIER 2 — Directly inferable from explicit facts.
    Conservative inference only. If the input names an engineering
    lead but not their title, "Engineering Lead" is OK as a role.
    Inferring "VP of Product" because most projects have one is NOT
    OK. The bar is: would a careful reader agree this is *stated*
    or *strongly implied* by the input?

  TIER 3 — Conservative placeholder (last resort).
    Only when Tier 1 and Tier 2 give you nothing for the section.
    Produce ONE placeholder row appropriate to the section. Mark
    Tier 3 content by ending the last field with `[assumption]`.

  NEVER:
    - Invent named individuals not in the input.
    - Invent specific dates, ROI numbers, or compliance details.
    - Invent technology choices the input did not mention.
    - Promote a Tier 2 inference to a Tier 1 statement of fact.

  When in doubt, prefer fewer rows or "TBD" over invention.

═══════════════════════════════════════════════════════════════════════
EMPTY HANDLING — never drop a section
═══════════════════════════════════════════════════════════════════════

If the input genuinely contains nothing for your section:

  - Table sections: produce the table header plus ONE row. First cell
    contains "[Awaiting input]". All other cells contain "TBD".
  - Bullet-list sections: produce ONE bullet:
    "[Awaiting input — to be defined in requirements review]".
  - Prose sections: produce ONE short paragraph:
    "[Section context to be confirmed with project sponsor.]"
  - Subsection-bullets sections (§5): produce BOTH subsections; each
    gets one bullet matching the empty-bullet rule above.
  - Glossary: produce ONE bullet:
    "[No domain-specific terms identified — to be populated as the
    project progresses.]"

Never output an empty table. Never drop a section heading. Never
emit zero content blocks.

═══════════════════════════════════════════════════════════════════════
LENGTH AND PADDING — match the input
═══════════════════════════════════════════════════════════════════════

  - Output exactly as much content as the input supports. No more.
  - Do NOT add template boilerplate ("Stakeholders are critical to
    project success because...").
  - Do NOT add transition sentences between rows or bullets.
  - Do NOT preface the section with "In this section, we will discuss…"
  - Each table row is one line of structured data. Not a paragraph
    in a cell.
  - A rich-input section may produce 20 rows. A thin-input section
    may produce 3 rows. Both are correct.
  - Do NOT target any length. The natural length is whatever the
    input supports.

═══════════════════════════════════════════════════════════════════════
CROSS-SECTION REFERENCES
═══════════════════════════════════════════════════════════════════════

You are running in parallel with 15 other section writers. You do NOT
have access to their output. To reference another section, use the
section number only — "see §4" — never reproduce content.

Example: in §7 (Functional Requirements), a row may say "Owner:
Engineering Lead (see §4)" rather than "Owner: Sarah Johnson".
Names belong in §4 only.

═══════════════════════════════════════════════════════════════════════
OUTPUT FORMAT — JSON array of content blocks
═══════════════════════════════════════════════════════════════════════

Your entire response is a JSON array. No markdown fences. No prose
before or after. The array elements are content blocks of these types:

  {"type": "paragraph", "text": "..."}
  {"type": "heading",   "level": 2 | 3, "text": "..."}
  {"type": "bullet_list", "items": ["...", "..."]}
  {"type": "table", "headers": ["...", "..."], "rows": [["..."], ...]}

Validity rules:
  - The top-level value is a JSON array, never an object or string.
  - For tables, `rows` is a list of lists; each inner list has
    `len(headers)` cells; all cells are strings.
  - For headings, `level` is an integer (2 or 3).
  - For bullet_list, `items` is a non-empty list of strings.

═══════════════════════════════════════════════════════════════════════

The next system message contains:
  - `template_section_specs`: the format for every section, so you
    can verify your assigned section's spec without rereading rules.
  - `context_bundle`: the input material (transcript / chat history /
    extracted facts) all sections share.

The user message tells you which section number you are writing.
"""


# ============================================================================
# Per-section user-message builders.
# Each builder returns a short instruction (a few hundred tokens at
# most). Context lives in the cached system prefix; the builder only
# carries the section number and any per-section emphasis the rules
# can't capture.
# ============================================================================

def _b1(context: Dict[str, Any]) -> str:
    return """\
Write Section 1: Document Overview.

Spec: 2-column table with headers ["Field", "Value"] and exactly
5 rows in this order:
  1. Document Name
  2. Author
  3. Version
  4. Last Updated
  5. Status

Filling rules:
  - Document Name: the project name from input. If multiple names
    appear, use the most-formal one. Tier-3 fallback: "[Awaiting input]".
  - Author: the BA/owner if named; else the project sponsor; else "TBD".
  - Version: "1.0" (every freshly generated BRD is v1.0).
  - Last Updated: leave as "TBD" — the platform fills this on save.
  - Status: "Draft".

Output a single `table` content block.
"""


def _b2(context: Dict[str, Any]) -> str:
    return """\
Write Section 2: Purpose.

Spec: 1–3 paragraphs of prose. Output is a list of one to three
`paragraph` content blocks.

What to cover (in this order, only what the input supports):
  1. The business need driving this project.
  2. The explicit problem being solved.
  3. The intended outcome / desired end-state.

Keep it tight. If the input gives you only one of the three, write
only one paragraph. Do not invent a "Why this matters" framing.
"""


def _b3(context: Dict[str, Any]) -> str:
    return """\
Write Section 3: Background / Context.

Spec: 1–3 paragraphs of prose. Output is a list of one to three
`paragraph` content blocks.

What to cover (only what the input supports):
  - The current state — what exists today.
  - Why the current state is insufficient.
  - What changed to prompt this initiative now.

If the input does not cover the history of the problem, omit the
"why now" paragraph rather than inventing one.
"""


def _b4(context: Dict[str, Any]) -> str:
    return """\
Write Section 4: Stakeholders.

Spec: 3-column table, headers exactly ["Name", "Role", "Responsibility"].

For each person or role named or strongly implied in the input,
write one row:
  - Name: the individual's name. If only a role is mentioned ("the
    engineering lead"), use "Engineering Lead" in the Name column.
  - Role: their title or function.
  - Responsibility: what they're accountable for on this project.

NEVER invent named individuals. NEVER assume there is a "Product
Manager" or "Project Sponsor" unless the input names one.

If input gives you no stakeholders at all, produce the empty-handling
row.
"""


def _b5(context: Dict[str, Any]) -> str:
    return """\
Write Section 5: Scope.

Spec: "subsection_bullets" — two subsections, each with a bullet list.
Output is FOUR blocks in this order:
  1. `heading` level 3, text "In Scope"
  2. `bullet_list` of in-scope items
  3. `heading` level 3, text "Out of Scope"
  4. `bullet_list` of out-of-scope items

Each bullet is one concrete item. Cross-reference other sections by
number where it helps ("Functional requirements detailed in §7").
Do NOT pad with vague items like "documentation"; only what the
input supports.

If the input is silent on one side, populate that subsection with
the empty-handling bullet.
"""


def _b6(context: Dict[str, Any]) -> str:
    return """\
Write Section 6: Business Objectives & ROI.

Spec: 3-column table, headers exactly
["Objective ID", "Description", "Priority"].

Filling rules:
  - Objective ID: pattern BO-001, BO-002, BO-003, … (zero-padded to 3).
  - Description: one sentence. The business outcome, not the feature.
  - Priority: one of MUST, SHOULD, COULD. Default SHOULD if input is
    silent on priority.

Only include objectives stated in the input. Specific ROI numbers
(percent savings, dollar amounts, headcount delta) ONLY if explicitly
provided — never invented.
"""


def _b7(context: Dict[str, Any]) -> str:
    return """\
Write Section 7: Functional Requirements.

Spec: 5-column table, headers exactly
["Req ID", "Description", "Priority", "Status", "Notes"].

Filling rules:
  - Req ID: pattern FR-001, FR-002, FR-003, … (zero-padded to 3).
  - Description: one sentence, imperative voice. "The system shall…"
    OR "Users can…". Pick one voice and use it for every row.
  - Priority: one of MUST, SHOULD, COULD. Use MUST sparingly — reserve
    for requirements explicitly called out as mandatory in input.
  - Status: default "Proposed" unless input says otherwise (e.g.
    "Approved", "In Review").
  - Notes: cross-references to other sections, edge cases the input
    mentions, or "—" if there's nothing to add.

Extract every distinct functional requirement from the input. Do not
merge two requirements into one row; do not split one requirement
across rows.
"""


def _b8(context: Dict[str, Any]) -> str:
    return """\
Write Section 8: Non-Functional Requirements.

Spec: 3-column table, headers exactly
["NFR ID", "Description", "Category"].

Filling rules:
  - NFR ID: pattern NFR-001, NFR-002, … (zero-padded to 3).
  - Description: the measurable quality attribute. Quantify where the
    input supports it ("p95 page load < 2s", "99.5% uptime").
  - Category: ONE of:
      Performance, Security, Reliability, Usability, Scalability,
      Maintainability, Portability, Compliance.

Only include NFRs the input mentions or strongly implies. Never
invent specific SLO numbers, compliance frameworks, or encryption
algorithms not in the input.
"""


def _b9(context: Dict[str, Any]) -> str:
    return """\
Write Section 9: User Stories / Use Cases.

Spec: 5-column table, headers exactly
["ID", "Title", "As a...", "I want to...", "So that..."].

Filling rules:
  - ID: pattern US-001, US-002, … (zero-padded to 3).
  - Title: 2–6 words, capturing the user story's essence.
  - As a…: the user role (must match a Section 4 stakeholder where
    possible; can be a broader user type like "End User" otherwise).
  - I want to…: the action the user wants to perform.
  - So that…: the business value the user gains.

Build user stories only from concrete user actions the input
describes. Don't generate a story just because you have a stakeholder
in §4 — the user must be described doing something specific.
"""


def _b10(context: Dict[str, Any]) -> str:
    return """\
Write Section 10: Assumptions.

Spec: one `bullet_list` content block.

Each bullet is one concrete assumption — something the project takes
as a given that, if invalidated, would change the requirements. Only
include assumptions the input states or strongly implies. Common
examples:
  - "The legacy claims API will remain available throughout v1."
  - "Active Directory is the single source of truth for user identity."

Do NOT pad with generic project-management assumptions ("Stakeholders
will be available"). Concrete, project-specific assumptions only.
"""


def _b11(context: Dict[str, Any]) -> str:
    return """\
Write Section 11: Constraints.

Spec: one `bullet_list` content block.

Each bullet is one concrete constraint — a bounded restriction on
scope, timeline, budget, technology, compliance, or team. Examples:
  - "Must run in AWS GovCloud."
  - "Budget capped at $1.2M for v1."
  - "Must be live by Q4 2026."

Only include constraints the input states. Distinguish constraints
(hard limits) from assumptions (working hypotheses).
"""


def _b12(context: Dict[str, Any]) -> str:
    return """\
Write Section 12: Acceptance Criteria / KPIs.

Spec: 2-column table, headers exactly ["Metric/Goal", "Target Value"].

Each row is one measurable success criterion:
  - Metric/Goal: what is being measured. Quantitative is ideal
    ("Average claim assignment time") but qualitative is OK if the
    input only provides qualitative goals ("Adjuster satisfaction").
  - Target Value: the value at which the goal is considered met
    ("< 4 hours", "≥ 4.0/5.0", "100% of claims have audit trails").

Only include KPIs the input mentions or strongly implies. Never
invent target values — if the input says "fast" but no number,
write "TBD" in Target Value with "[assumption]" suffix marker on
the Metric/Goal cell.
"""


def _b13(context: Dict[str, Any]) -> str:
    return """\
Write Section 13: Timeline / Milestones.

Spec: 4-column table, headers exactly
["Milestone", "Duration", "Owner", "Deliverables"].

Filling rules:
  - Milestone: a phase or major deliverable name (e.g. "Discovery",
    "MVP Build", "UAT", "Production Cutover").
  - Duration: relative ("4 weeks", "2 sprints") if input gives no
    dates. Absolute dates only if the input provides them. Never
    invent specific dates.
  - Owner: the responsible role/team. Cross-reference §4 by role
    name; never invent individuals.
  - Deliverables: 1–3 short bullets within the cell, separated by
    "; ". The concrete artifact produced.

If the input has no timeline information, produce one row with
"[Awaiting input]" in Milestone and "TBD" elsewhere.
"""


def _b14(context: Dict[str, Any]) -> str:
    return """\
Write Section 14: Risks and Dependencies.

Spec: 3-column table, headers exactly
["Risk/Dependency", "Impact", "Mitigation"].

Filling rules:
  - Risk/Dependency: one concrete risk OR external dependency.
    Risks describe what could go wrong; dependencies describe what
    we rely on outside this project. Either is valid; both go in
    this table.
  - Impact: ONE of High, Medium, Low. Default Medium if input is
    silent on impact.
  - Mitigation: what we are doing about it. "Monitor", "Accept",
    "Avoid", "Transfer", or a specific action.

Only include risks/dependencies the input mentions. NEVER invent
generic "Key personnel may leave" risks unless the input raises
team-stability concerns.
"""


def _b15(context: Dict[str, Any]) -> str:
    return """\
Write Section 15: Approval & Review.

Spec: 4-column table, headers exactly
["Reviewer Name", "Role", "Date", "Comments"].

Filling rules:
  - Reviewer Name: a named individual from input if specified;
    otherwise the role name (e.g. "Project Sponsor").
  - Role: the approver's function. Common values: Project Sponsor,
    Business Owner, IT Director, Compliance Lead.
  - Date: always "TBD" — the platform fills this on sign-off.
  - Comments: always "—" (signatures gathered post-generation).

For most v1 BRDs the input has not yet identified approvers. The
empty-handling row is appropriate when input gives you no
approval-chain information. If even one approver IS named, fill
their row and add empty-handling rows for the others ONLY if the
input implies a multi-step approval (e.g. "Compliance must sign off").
"""


def _b16(context: Dict[str, Any]) -> str:
    return """\
Write Section 16: Glossary & Appendix.

Spec: one `bullet_list` content block. Each item is formatted
"Term — definition" (em-dash, single space on each side).

Include ONLY domain-specific terms that actually appeared in the
input. Examples worth including:
  - Acronyms unique to the customer's domain ("SLA", "RACI", "ACA").
  - Product names, internal system names ("CSR Portal", "Vault").
  - Industry jargon used in the input that a generalist reader might
    not know.

Do NOT include generic engineering or business terms ("API",
"requirement", "stakeholder"). Do NOT pad with definitions for
words the BRD itself didn't use.

If the input has no glossary-worthy terms, produce the empty-handling
bullet.
"""


SECTION_PROMPT_BUILDERS: Dict[int, Callable[[Dict[str, Any]], str]] = {
    1: _b1, 2: _b2, 3: _b3, 4: _b4,  5: _b5,  6: _b6,  7: _b7,  8: _b8,
    9: _b9, 10: _b10, 11: _b11, 12: _b12, 13: _b13, 14: _b14, 15: _b15, 16: _b16,
}


# Sanity check at import time: every section in BRD_SECTIONS must have
# a builder, and no extra builders.
assert set(SECTION_PROMPT_BUILDERS) == set(n for n, _, _ in BRD_SECTIONS), (
    "SECTION_PROMPT_BUILDERS must have an entry for every section in BRD_SECTIONS"
)


# ============================================================================
# Helpers used by the Lambda — keep these stable since lambda code will
# import them directly.
# ============================================================================

def build_cached_system_blocks(context_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the cacheable system-block list for ALL section calls in
    a single generation.

    Structure: a SINGLE content block carrying both the prose rules and
    the JSON spec+context, with cache_control set:

      [{"type": "text", "text": <SHARED_SYSTEM_PROMPT + JSON>,
        "cache_control": {"type": "ephemeral"}}]

    Why one block, not two: the DLX gateway proxy collapses multi-block
    system content unreliably — empirically a two-block system payload
    arrives at Bedrock with the prose block dropped (verified in the
    Phase 6 cache smoke test: two-block call showed prompt_tokens=16).
    One-block payloads pass through cleanly and cache as expected.

    Anthropic Sonnet 4.5 requires ≥1024-token cached prefix to activate
    caching. SHARED_SYSTEM_PROMPT alone is ~1640 tokens; combined with
    the spec+context JSON the prefix is ~2300+ tokens — comfortably above.

    Context bundle shape (caller-controlled):
      {
        "transcript": str,          # docs path
        "chat_history": [...],      # history path
        "style_constraints": str,   # Deluxe tone constraints
        "extracted_facts": [...],   # Phase 6.5 skeleton (optional)
      }
    Any subset is fine; missing keys are omitted from the JSON.
    """
    spec_and_context = {
        "template_section_specs": _serializable_section_specs(),
        "context_bundle": context_bundle,
    }
    # Concatenate the prose rules and the JSON spec+context into one
    # text block. The "<CONTEXT BUNDLE BELOW>" sentinel gives the model a
    # clear delimiter so it knows where the JSON starts.
    combined_text = (
        SHARED_SYSTEM_PROMPT
        + "\n\n<CONTEXT BUNDLE BELOW — JSON>\n"
        + json.dumps(spec_and_context, indent=2, default=str)
    )
    return [
        {
            "type": "text",
            "text": combined_text,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _serializable_section_specs() -> Dict[str, Dict[str, Any]]:
    """Render SECTION_FORMATS with section titles inlined, keyed by
    string section number (JSON serialises string keys cleanly).
    The model sees this in the cached prefix and references its
    assigned section by number."""
    out: Dict[str, Dict[str, Any]] = {}
    for n, _title, _slug in BRD_SECTIONS:
        spec = dict(SECTION_FORMATS[n])
        spec["title"] = section_title(n)
        out[str(n)] = spec
    return out


def build_section_user_message(n: int, context: Dict[str, Any] | None = None) -> str:
    """Return the per-section user-message body for section `n`.

    The body is a few hundred tokens at most; the heavy lifting (rules,
    format specs, context) is in the cached prefix. The user message is
    the variable per-call tail — it tells the model which section to
    write and any section-specific emphasis.
    """
    if n not in SECTION_PROMPT_BUILDERS:
        raise KeyError(f"no builder registered for section {n}")
    return SECTION_PROMPT_BUILDERS[n](context or {})
