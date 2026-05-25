"""
BRD Intent Router prompt.

Classifies one user chat-box submission into one of 12 intents and
extracts the parameters each handler needs. Single LLM call per turn,
JSON-only output, deterministic (T=0.0).

Design notes:
  * The system prompt is INTENTIONALLY STABLE — same text every call —
    so Bedrock prompt caching (cache_control: ephemeral on the system
    message) can amortise its ~700 tokens across many calls. Only the
    user-content block built by build_router_prompt() varies per call.
  * BRD sections are dynamic (from brd_structure.json) unlike SAD's
    fixed 10-section template, so we embed available section titles
    into the user-content block rather than the system prompt.
  * Stage validity is enforced server-side AFTER classification:
    e.g. router may emit EDIT_SECTION in NEW stage if the user message
    fits the verb pattern, but the orchestrator will reject it and
    fall back to GATHER_REQUIREMENTS or ASK_GENERAL. This lets the
    router prompt stay stage-agnostic where possible (smaller prompt,
    better caching).
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Intent enum — keep in lock-step with lambda_brd_orchestrator's dispatch
# table and the tests/test_dispatch_coverage.py assertion.
# ---------------------------------------------------------------------------
BRD_INTENTS: List[str] = [
    "ASK_GENERAL",
    "ASK_QUESTION",
    "SHOW_SECTION",
    "SUGGEST",
    "ADD_INFO",
    "EDIT_SECTION",
    "GATHER_REQUIREMENTS",
    "GENERATE_FROM_DOCS",
    "GENERATE_FROM_HISTORY",
    "AUDIT",
    "REGENERATE_SECTION",
    "INGEST_DOC",
]


# Stages where each intent is valid. The orchestrator uses this to
# down-grade router output when it picks an intent that doesn't apply
# at the current stage (e.g. EDIT_SECTION in NEW → GATHER_REQUIREMENTS).
INTENT_VALID_STAGES: Dict[str, frozenset] = {
    "ASK_GENERAL":           frozenset({"NEW", "GATHERING", "DRAFTED", "REFINING"}),
    "ASK_QUESTION":          frozenset({"DRAFTED", "REFINING"}),
    "SHOW_SECTION":          frozenset({"DRAFTED", "REFINING"}),
    "SUGGEST":               frozenset({"DRAFTED", "REFINING"}),
    "ADD_INFO":              frozenset({"NEW", "GATHERING", "DRAFTED", "REFINING"}),
    "EDIT_SECTION":          frozenset({"DRAFTED", "REFINING"}),
    "GATHER_REQUIREMENTS":   frozenset({"NEW", "GATHERING", "DRAFTED", "REFINING"}),
    "GENERATE_FROM_DOCS":    frozenset({"NEW", "GATHERING"}),
    "GENERATE_FROM_HISTORY": frozenset({"GATHERING"}),
    "AUDIT":                 frozenset({"DRAFTED", "REFINING"}),
    "REGENERATE_SECTION":    frozenset({"DRAFTED", "REFINING"}),
    "INGEST_DOC":            frozenset({"NEW", "GATHERING", "DRAFTED", "REFINING"}),
}


# Output JSON schema description, kept exactly in sync with the system
# prompt's OUTPUT FORMAT block below.
ROUTER_OUTPUT_KEYS = (
    "intent",
    "target_section",
    "target_title",
    "fact",
    "edit_instruction",
    "regen_proposed",
    "confidence",
)


# ---------------------------------------------------------------------------
# System prompt — STABLE across all calls. This is what Bedrock prompt
# caching tags as cacheable. ~700 tokens.
# ---------------------------------------------------------------------------

BRD_INTENT_ROUTER_SYSTEM_PROMPT = """\
You are the intent classifier for a Business Requirements Document (BRD)
chat assistant. You receive ONE user message at a time plus session
context and you return ONE JSON object describing how the message should
be handled. Output NOTHING else — no commentary, no markdown, no
explanation. Just the JSON.

The BRD assistant has two personas the handler layer chooses between:
  • Mary — gathers requirements by asking ONE focused follow-up question
    at a time. Active when no BRD exists yet or when the user is
    discovering / elaborating mid-session.
  • PM — performs precise edits, regenerations, audits, and answers on
    an existing BRD. Active when the user is refining a drafted BRD.

Stages (already validated server-side, but inform your choice):
  • NEW         — session created, no chat yet.
  • GATHERING   — Mary is actively gathering requirements; no BRD draft.
  • GENERATING  — long-poll only, your output won't be consulted.
  • DRAFTED     — BRD JSON exists, no edits yet.
  • REFINING    — BRD exists and is being refined.

Available intents and when each fires:
  • ASK_GENERAL — greetings, capabilities ("what can you do?"), small
    talk. No BRD context needed.
  • ASK_QUESTION — informational query about the EXISTING BRD. "What
    does §4 say about user roles?" "How are stakeholders defined?"
    Question form, expects an answer grounded in BRD content.
  • SHOW_SECTION — display a section verbatim, no LLM rewrite.
    Verbs: show, view, display, list. "Show §3", "view stakeholders",
    "list sections" (target_section=null → full table of contents).
  • SUGGEST — ask for ideas / what to add. "Any risks I should add?"
    "What's missing from §7?" "Suggest improvements for stakeholders."
  • ADD_INFO — user volunteers a fact, NO edit verb. "Also we use
    Redis for caching." "BTW the API is REST, not GraphQL." Buffer it
    into the facts store; may propose regenerating an affected section.
  • EDIT_SECTION — IMPERATIVE edit. Verbs: change, update, replace,
    modify, fix, remove, add (when scoped to a section). "Change the
    deadline to Q4 in §3." "Update §6 to mention TLS." Sets
    target_section/target_title + edit_instruction.
  • GATHER_REQUIREMENTS — Mary-style probe. Three triggers:
      (a) No BRD AND declarative project description.
      (b) Open-ended elaboration mid-session ("tell me more about
          scale", "I'm not sure about the security model").
      (c) Hesitation that invites probing.
    Distinct from ADD_INFO (user volunteered a definite fact) and
    ASK_QUESTION (user asked about existing BRD content).
  • GENERATE_FROM_DOCS — template + transcript present in payload OR
    user explicitly says "use this doc/transcript to generate the
    BRD." Runs the full BRD generator.
  • GENERATE_FROM_HISTORY — "generate the BRD now", "I'm done", "let's
    draft it" with NO doc attached. Uses accumulated chat history.
    Only fires when stage is GATHERING.
  • AUDIT — quality review. "Audit the BRD", "score it", "what's
    missing", "find issues." Returns per-section badges + issue list.
  • REGENERATE_SECTION — explicit redo of one section OR a "yes"-style
    confirmation to a prior fact_saved card that proposed regenerating.
    "Regenerate §5", "redo stakeholders", "yes, update it now."
  • INGEST_DOC — a file was attached this turn AND the user did NOT
    explicitly ask for full generation. Summarises doc into facts.

Disambiguation rules — apply in order, first match wins:
  1. payload.template AND payload.transcript both present → GENERATE_FROM_DOCS.
  2. file_attached AND message says "use this to generate/create the BRD" /
     "regenerate from this doc" → GENERATE_FROM_DOCS.
  3. file_attached AND message DOES NOT request full generation → INGEST_DOC.
  4. No BRD exists AND user description is declarative (not a question) →
     GATHER_REQUIREMENTS.
  5. No BRD exists AND "generate" / "draft" / "I'm done" → GENERATE_FROM_HISTORY.
  6. Section reference (number OR topic name) + imperative verb →
     EDIT_SECTION.
  7. "Redo" / "regenerate" + section reference, OR confirmation
     ("yes"/"go ahead") to a prior fact_saved with regen_proposed=true
     → REGENERATE_SECTION.
  8. "Audit" / "score" / "find issues" / "what's missing" → AUDIT.
  9. "Suggest" / "what should I add" / "any X I should include" → SUGGEST.
 10. Question form ("?", what/how/why/when about EXISTING BRD content) →
     ASK_QUESTION.
 11. "Show" / "view" / "list" with section reference (or bare "list") →
     SHOW_SECTION.
 12. Declarative fact, no edit verb, no question mark → ADD_INFO.
     If topic clearly maps to a section, set target_section and
     regen_proposed=true.
 13. Open-ended elaboration / hesitation / abstract questions about how
     to think about a topic → GATHER_REQUIREMENTS.
 14. Greetings, capabilities, off-topic → ASK_GENERAL.

Negative examples — important "this looks like X but it's actually Y":
  • "I'm not sure about the security model" → GATHER_REQUIREMENTS
    (hesitation; not a volunteered fact, not a question about BRD).
  • "Let's update the security section to mention TLS" → EDIT_SECTION
    (imperative verb "update" + section reference — NOT elaboration).
  • "Also we use Redis for caching" → ADD_INFO with regen_proposed=true
    if "caching" maps to a section — NOT EDIT_SECTION (no imperative).
  • "How does §4 handle PII?" → ASK_QUESTION (question), NOT SHOW_SECTION.
  • "Show me what you have for §4" → SHOW_SECTION (display verb), NOT
    ASK_QUESTION (no real question, just a view request).
  • "What should we put in §3?" → SUGGEST (asking for ideas), NOT
    ASK_QUESTION (which is about EXISTING content) and NOT
    GATHER_REQUIREMENTS (which is open-ended).

Safety fallback — if confidence < 0.5:
  • Prefer ADD_INFO over EDIT_SECTION (safer — adds to buffer, doesn't
    rewrite content).
  • If no BRD exists, prefer GATHER_REQUIREMENTS over ASK_GENERAL.

OUTPUT FORMAT — return ONLY this JSON, with NO surrounding text:
{
  "intent": "<one of the 12 intents>",
  "target_section": <integer or null>,
  "target_title": "<section title string or empty>",
  "fact": "<extracted fact for ADD_INFO/GATHER, else empty>",
  "edit_instruction": "<imperative for EDIT_SECTION/REGENERATE_SECTION, else empty>",
  "regen_proposed": <true | false>,
  "confidence": <0.0 to 1.0>
}
"""


# ---------------------------------------------------------------------------
# User-content builder — variable per call. NOT cacheable.
# ---------------------------------------------------------------------------

def build_router_prompt(
    *,
    user_message: str,
    stage: str,
    brd_exists: bool,
    available_sections: Optional[List[Dict[str, Any]]] = None,
    currently_viewing_section: Optional[int] = None,
    file_attached: bool = False,
    template_attached: bool = False,
    transcript_attached: bool = False,
    last_assistant_card_type: Optional[str] = None,
    last_assistant_proposed_section: Optional[int] = None,
) -> str:
    """
    Compose the user-content portion of the router call.

    Variable inputs only — the system prompt stays static for caching.

    Args:
        user_message: Raw chat-box text the user just submitted.
        stage: Current BRD session stage (NEW | GATHERING | … | REFINING).
        brd_exists: Whether brds/{brd_id}/brd_structure.json exists in S3.
        available_sections: List of {number, title} dicts from the
            current brd_structure.json, so the router can resolve topic
            references like "the security section" to a number. Empty
            list or None when no BRD exists yet.
        currently_viewing_section: If the frontend is showing a section
            (user clicked into §4 then typed), pass the number — helps
            resolve "this" / "here" references.
        file_attached: True if `file` is present this turn.
        template_attached / transcript_attached: True when both are
            present we route to GENERATE_FROM_DOCS without LLM.
        last_assistant_card_type / last_assistant_proposed_section:
            Context for resolving "yes"-style confirmations to prior
            cards (drives REGENERATE_SECTION when applicable).
    """
    sections_block = _format_sections_block(available_sections)
    return (
        f"Stage: {stage}\n"
        f"BRD exists: {str(brd_exists).lower()}\n"
        f"File attached this turn: {str(file_attached).lower()}\n"
        f"Template + transcript both attached: "
            f"{str(template_attached and transcript_attached).lower()}\n"
        f"Currently viewing section: "
            f"{currently_viewing_section if currently_viewing_section else 'none'}\n"
        f"Last assistant card type: {last_assistant_card_type or 'none'}\n"
        f"Last assistant proposed regenerating section: "
            f"{last_assistant_proposed_section if last_assistant_proposed_section else 'none'}\n"
        f"\n"
        f"Available BRD sections (for resolving topic references to numbers):\n"
        f"{sections_block}\n"
        f"\n"
        f"User message:\n"
        f"\"\"\"\n{user_message}\n\"\"\""
    )


def _format_sections_block(sections: Optional[List[Dict[str, Any]]]) -> str:
    """Render the section table for the router's user-content block.
    Empty / None → '(no BRD draft yet — no sections to reference)'."""
    if not sections:
        return "  (no BRD draft yet — no sections to reference)"
    lines = []
    for s in sections:
        num = s.get("number")
        title = s.get("title") or "(untitled)"
        if num is None:
            continue
        lines.append(f"  {num}. {title}")
    return "\n".join(lines) if lines else "  (no BRD draft yet — no sections to reference)"


def get_router_system_prompt() -> str:
    """Return the stable system prompt. Identical text every call so the
    Bedrock prompt-caching hit-rate is maximised."""
    return BRD_INTENT_ROUTER_SYSTEM_PROMPT
