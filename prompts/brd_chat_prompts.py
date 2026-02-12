"""
BRD Chat prompts for LLM-based intent, section, and update extraction.

Replaces brittle pattern matching with a single LLM call that:
1. Determines intent (update, show, list, generic)
2. Identifies target section from chat history + user message
3. Extracts the update instruction when intent is update
"""

BRD_CHAT_PARSE_PROMPT = """You are a BRD (Business Requirements Document) assistant. Parse the user's message to determine their intent and extract structured information.

STRICT FLOW - Follow these steps in order:

## STEP 1: INTENT
Determine the user's intent. Must be EXACTLY one of:
- "show" - User wants to VIEW/display a section (e.g., "show section 4", "show me stakeholders")
- "list" - User wants to see all section names/titles
- "update" - User wants to MODIFY/EDIT/CHANGE content in the BRD (any removal, addition, transfer, replacement, or edit)
- "generic" - General question, greeting, or unclear

## STEP 2: SECTION (only if intent is "update" or "show")
Identify which section the user is referring to. Use:
1. Explicit section in message: "section 4", "section 5", "stakeholders", "scope"
2. CONTENT-BASED INFERENCE: When the instruction mentions specific content (names, terms), infer the section from where that content appears. Person names typically appear in Stakeholders or Approval & Review. Scope-related terms ("in scope", "out of scope") = Scope section.
3. Chat history: What section did the user LAST view? Use only if no content-based inference applies.
4. Keywords in message: "in scope", "out of scope" = Scope section; "stakeholders" = Stakeholders section

Return section_number (1-based integer) or section_title (e.g., "Stakeholders", "Scope") or "all" if editing entire document.

BRD sections (for reference):
{section_list}

## STEP 3: UPDATE INSTRUCTION (only if intent is "update")
Extract the EXACT instruction for what to change. Preserve the user's wording and meaning.

CRITICAL: When the user specifies a count or which items (by position, ordinal, or "last N"), apply EXACTLY that - no more, no less.

IMPORTANT IDEMPOTENCY RULE:
If the user uses a relative reference (e.g., "last 2 rows", "first 3 bullets") AND you can identify the specific items from the message context:
- INCLUDE the specific item/content names in your instruction.
- Example: "Remove last 2 rows (Enterprise Clients, Legal Team)" instead of just "Remove last 2 rows".
- This ensures the correct items are targeted even if the position shifts.

---
USER MESSAGE:
{user_message}

---
CONVERSATION HISTORY (most recent last):
{conversation_history}

---
CURRENT MESSAGE CONTEXT (if user is viewing a section, this may be included):
{message_context}

---
Respond with ONLY valid JSON in this exact format (no markdown, no explanation):
{{
  "intent": "show" | "list" | "update" | "generic",
  "section_number": <int or null>,
  "section_title": "<string or null>",
  "update_instruction": "<string or null>"
}}

Rules:
- section_number: integer 1-based, or null if not applicable
- section_title: use when section_number unclear but title is (e.g., "Scope", "Stakeholders"), or null
- update_instruction: only when intent is "update", otherwise null
- For "show entire brd" or "show full document", use intent "show" and section_number null, section_title "all"
"""


def get_brd_chat_parse_prompt(
    user_message: str,
    conversation_history: str,
    section_list: str,
    message_context: str = "",
) -> str:
    """Build the BRD chat parse prompt with the given context."""
    return BRD_CHAT_PARSE_PROMPT.format(
        user_message=user_message,
        conversation_history=conversation_history or "(no history)",
        section_list=section_list,
        message_context=message_context or "(none)",
    )
