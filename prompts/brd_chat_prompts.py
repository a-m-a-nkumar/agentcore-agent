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
- "show" - User wants to VIEW/display a section (e.g., "show section 4", "show me stakeholders", "display section 5")
- "list" - User wants to see all section names/titles
- "update" - User wants to MODIFY/EDIT/CHANGE content in the BRD (e.g., "remove last two rows", "change X to Y", "transfer items from in scope to out of scope", "add Z to section 4")
- "generic" - General question, greeting, or unclear (e.g., "what sections did I update?", "hello", "help")

IMPORTANT for "update" intent:
- "remove last two rows" = update (removing rows from a table)
- "transfer last 3 points of in scope to out of scope" = update (moving content between In Scope and Out of Scope)
- "change X to Y" = update
- "add Z" = update
- "delete the last two entries" = update
- Typos like "roes" instead of "rows" = still update intent

## STEP 2: SECTION (only if intent is "update" or "show")
Identify which section the user is referring to. Use:
1. Explicit section in message: "section 4", "section 5", "stakeholders", "scope"
2. Chat history: What section did the user LAST view? Look for "show section N" or assistant messages displaying "## N. SectionTitle"
3. Section content in message: If message contains "SECTION 4: Stakeholders" or "## 5. Scope", that's the section
4. Keywords: "in scope", "out of scope" = Scope section; "stakeholders" = Stakeholders section

Return section_number (1-based integer) or section_title (e.g., "Stakeholders", "Scope") or "all" if editing entire document.
If intent is "show" and user said "show section 5", return section_number: 5.
If intent is "update" and message contains "SECTION 5: Scope" with "transfer last 3 points...", return section_number: 5.

BRD sections (for reference):
{section_list}

## STEP 3: UPDATE INSTRUCTION (only if intent is "update")
Extract the EXACT instruction for what to change. Be precise:
- "remove last two rows" -> "Remove the last two rows from the table"
- "transfer last 3 points of in scope to out of scope" -> "Move the last 3 items from In Scope to Out of Scope"
- "change Sarah to Aman" -> "Replace all occurrences of Sarah with Aman"
- "add security requirements" -> "Add security requirements to the section"

Preserve the user's meaning even with typos. "roes" = "rows".

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
