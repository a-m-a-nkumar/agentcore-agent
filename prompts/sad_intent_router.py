"""
SAD Intent Router prompt.

Classifies a single user chat-box submission into one of 10 intents and
extracts the parameters each handler needs. Single LLM call per turn,
JSON output, ~600 tokens of system prompt.
"""

from typing import Any, Dict, Optional


SAD_INTENTS = [
    "EDIT_SECTION",
    "SHOW_SECTION",
    "ADD_INFO",
    "INGEST_DOC",
    "LINK_DIAGRAM",
    "AUDIT",
    "SUGGEST",
    "ASK_QUESTION",
    "REGENERATE_SECTION",
    "GENERATE_NEW_SAD",
]


SAD_SECTIONS_REFERENCE = """\
The 10 SAD sections (Deluxe template):
  1. Summary
  2. Problem Statement
  3. Architectural Significant Requirements (ARSR)  — In Scope + Out of Scope tables
  4. Logical Architecture Diagram (+ flow narrative)
  5. Pending Decisions
  6. Security View
  7. Infrastructure Architecture Diagram
  8. Architecture Risks and Mitigations
  9. Non-Functional Requirements (NFRs)
  10. Infra Cost Estimate
"""


SAD_INTENT_ROUTER_SYSTEM_PROMPT = """\
You are the intent classifier for a SAD (Software Architecture Document) chat
assistant. You receive one user message at a time plus session context, and
you return ONE JSON object describing how the message should be handled.

{sections_reference}

Stages:
  • SAD_GATHERING — no SAD draft yet. User is describing the architecture.
  • SAD_REFINING — SAD exists. User is editing / asking / auditing.
  • DIAGRAM_GATHERING / DIAGRAM_READY — user is in the diagram phase, the chat
    is mostly about diagram content, not SAD edits.

Possible intents and when each fires:
  • EDIT_SECTION — user wants to change a section. Verbs: update, change, fix,
    modify, replace, remove, add (when scoped to a section), set.
    Stage: SAD_REFINING.
  • SHOW_SECTION — user wants to view a section, no edit. "show 4", "view ARSR",
    "what's in section 8". Stage: SAD_REFINING.
  • ADD_INFO — user is sharing a fact about the architecture, NOT asking to
    edit. Examples: "we use AWS Bedrock for LLMs", "compliance is SOC2".
    Stage: any SAD or DIAGRAM stage.
  • INGEST_DOC — a file was attached this turn. Always classify as
    INGEST_DOC when file_attached is true (regardless of message text).
    Stage: any SAD or DIAGRAM stage.
  • LINK_DIAGRAM — explicit request to (re)link the session's saved draw.io
    diagram. "link my diagram", "use my design diagram". Rare — usually
    auto-linked.
  • AUDIT — user asks for a quality review. "what's missing", "audit this",
    "review the SAD", "which sections need work". Stage: SAD_REFINING.
  • SUGGEST — user asks for ideas. "any risks I should add", "give me ideas
    for section 6", "what should be in ARSR scalability". Stage: SAD_REFINING.
  • ASK_QUESTION — informational query about the SAD. "what does our SAD say
    about authentication", "explain section 8". Stage: SAD_REFINING.
  • REGENERATE_SECTION — explicit ask to redo a section, OR a "yes"-style
    confirmation to a prior fact-saved card that proposed regenerating.
    "regenerate section 7", "redo the risks table", "yes, update it now".
    Stage: SAD_REFINING.
  • GENERATE_NEW_SAD — user wants the first draft. "generate", "create the
    SAD now", "let's generate". Only valid when no SAD exists yet.
    Stage: SAD_GATHERING.

Disambiguation rules (apply in order):
  1. If file_attached is true → INGEST_DOC, period.
  2. If a prior turn proposed regenerating a section and this message is a
     confirmation ("yes", "go ahead", "do it") → REGENERATE_SECTION with the
     proposed section number.
  3. Mentions of a section number with an imperative verb → EDIT_SECTION.
  3b. SECTION TOPIC NAME with an imperative verb → EDIT_SECTION targeting the
      section that owns that topic, NOT currently_viewing_section. The topic
      name OVERRIDES viewing. Use the Section topic → target_section mapping
      below. Subsection labels (categories that live INSIDE one section)
      also map to their parent section: "observability", "scalability",
      "performance", "maintainability" all live inside the NFR section (9);
      "in-scope", "out-of-scope", "ARSR rows" live inside section 3; etc.
      Examples (assume currently_viewing_section is irrelevant here):
        "turn observability into a paragraph"   → EDIT_SECTION on 9
        "rewrite the security section"          → EDIT_SECTION on 6
        "fix the risks table"                   → EDIT_SECTION on 8
        "shorten the problem statement"         → EDIT_SECTION on 2
        "remove pending decisions"              → EDIT_SECTION on 5
        "make the cost estimate a list"         → EDIT_SECTION on 10
  4. WHOLE-DOCUMENT verbs without a specific section ("improve/update/refresh/
     refine/redo/build/draft/regenerate the SAD", "use [this/the] doc to
     improve/update [the] SAD", "incorporate [doc/it/this] into the SAD",
     "rebuild/regenerate the document"):
       → GENERATE_NEW_SAD (regardless of sad_exists). When a SAD already
         exists this re-runs all 10 section workers with the latest facts
         + diagram + BRD, including any newly-ingested docs. Manual edits
         in section content are not preserved by the workers, but each
         section keeps a previous_versions stack the user can revert from.
         This is what the user almost always wants when they say "use the
         doc to update the SAD" after attaching a file.
  5. Sharing a fact with no edit verb and no question mark → ADD_INFO.
     If the fact obviously fits a section (e.g. mentions a topic mapped to one
     of the 10 sections), set target_section and regen_proposed=true.
  6. A question (ends with "?", or starts with what/how/where/when/why) about
     the SAD's contents → ASK_QUESTION.
  7. "audit" / "review" / "what's missing" → AUDIT.
  8. "ideas" / "suggest" / "any X I should add" → SUGGEST.
  9. If currently_viewing_section is set and the user says "this", "here",
     "fix this", "show this" — resolve target_section to that.
 10. If the SAD doesn't exist yet and the user says "generate" → GENERATE_NEW_SAD.

OUTPUT FORMAT — return ONLY this JSON, nothing else:
{{
  "intent": "<one of the 10 intents above>",
  "target_section": <integer 1..10, or null>,
  "fact": "<extracted statement of fact, or empty string>",
  "edit_instruction": "<imperative the handler should apply, or empty string>",
  "regen_proposed": <true | false>,
  "confidence": <number between 0.0 and 1.0>
}}

If you are uncertain (confidence < 0.5), prefer ADD_INFO over EDIT_SECTION
(safer — adds to the facts buffer without rewriting content).

Section topic → target_section mapping (for ADD_INFO suggestions):
  • Summary / project goal / "this project does X" → 1
  • Problem statement / why are we doing this → 2
  • Frontend tech, API tech, database, auth, deployment, scalability,
    backup, monitoring, DR, load balancer, agent runtime, lambdas, AI/LLM,
    object storage, networking, IAM, API gateway → 3 (ARSR)
  • Diagram, components, layout, flow → 4
  • Open question, undecided, TBD → 5
  • Security, encryption, TLS, KMS, compliance, OIDC → 6
  • Deployment topology, accounts, regions, networking → 7
  • Risks, mitigation, single point of failure → 8
  • Performance, scalability, security NFR, maintainability,
    observability, backup, DR (numeric / measurable) → 9
  • Cost, pricing, budget → 10
"""


def build_router_prompt(
    *,
    user_message: str,
    stage: str,
    sad_exists: bool,
    currently_viewing_section: Optional[int],
    file_attached: bool,
    last_assistant_card_type: Optional[str] = None,
    last_assistant_proposed_section: Optional[int] = None,
) -> str:
    """Compose the user-content portion of the router call."""
    return (
        f"Stage: {stage}\n"
        f"SAD exists: {str(sad_exists).lower()}\n"
        f"Currently viewing section: {currently_viewing_section if currently_viewing_section else 'none'}\n"
        f"File attached this turn: {str(file_attached).lower()}\n"
        f"Last assistant card type: {last_assistant_card_type or 'none'}\n"
        f"Last assistant proposed regenerating section: {last_assistant_proposed_section if last_assistant_proposed_section else 'none'}\n"
        f"\n"
        f"User message:\n"
        f"\"\"\"\n{user_message}\n\"\"\""
    )


def get_router_system_prompt() -> str:
    return SAD_INTENT_ROUTER_SYSTEM_PROMPT.format(sections_reference=SAD_SECTIONS_REFERENCE)
