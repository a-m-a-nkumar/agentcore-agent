"""
BRD section EDIT and REGENERATE prompts.

Two entry points live in this module:

  1. `get_brd_update_prompt(...)` -- verbatim port of the prompt that
     was inline at `lambda_brd_chat.py:915-1028`. Kept BIT-FOR-BIT
     identical so the existing Phase-3-shim path keeps behaving the
     same as before the unification. Old caller signature preserved
     (conversation_history + section_list are unused but kept for
     compat with the existing lambda_brd_chat call site at line 1637).

  2. `EDIT_SYSTEM_PROMPT` + `build_edit_prompt(...)` -- split form used
     by the new `lambda_brd_orchestrator`. The system prompt is
     STABLE across all calls (no per-section variables) so Bedrock
     prompt caching can amortise its tokens. Variable parts go into
     the user-content block built by `build_edit_prompt`.

Both forms produce semantically equivalent prompts; the split form
is the preferred entry point for any new code path because it cuts
input-token cost by ~90% on cache-hit and shaves real latency.

Trailing whitespace on certain lines of get_brd_update_prompt is
INTENTIONAL -- it matches the legacy inline prompt byte-for-byte so
behaviour does not drift during the dual-ship window.
"""

import json
import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Split form -- STABLE system prompt + VARIABLE user content.
# Preferred by lambda_brd_orchestrator. Bedrock prompt caching applies
# to the system message only (cache_control: ephemeral on it).
# ---------------------------------------------------------------------------

EDIT_SYSTEM_PROMPT = """\
You are a documentation assistant editing one section of a Business
Requirements Document.

You MUST interpret the user's instruction LITERALLY and PRECISELY.
Your goal is to modify ONLY the specific items requested and PRESERVE
everything else exactly as it was.

RULE 1 -- UNIQUE ITEM NUMBERING
The <current_section_content_numbered> block uses GLOBAL unique IDs:
  - [ITEM N] for bullet points (unique across all lists in the section)
  - [ROW N]  for table data rows
ALWAYS resolve user references to these unique identifiers first.
Example: if [ITEM 1]..[ITEM 5] are in List A and [ITEM 6]..[ITEM 8] in
List B, "delete 1st item of List B" means DELETE [ITEM 6].

RULE 2 -- CONTENT IDENTIFICATION PRIORITY
When the instruction identifies an item by its content ("remove
Enterprise Clients", "change role of Sarah"), match that content. Do
NOT fall back to position when the named content is present.

RULE 3 -- STALE REFERENCE HANDLING (double-deletion prevention)
If the instruction names specific items by content but those items do
NOT appear in the numbered view:
  1. STOP. Do NOT apply the operation to different items at the same
     position.
  2. Assume the action has already been taken (stale instruction).
  3. Return the section UNCHANGED, or apply only the parts of the
     instruction that DO match.

RULE 4 -- QUANTITY SAFEGUARD
If the instruction specifies a quantity ("remove 2 rows"), you MUST
NOT remove more than that quantity. If ambiguous interpretation would
remove more, prefer the safer minimum.

RULE 5 -- POSITIONAL-ONLY references ("remove last 2 rows") may be
applied AS-IS, but only when the instruction does NOT also name
specific content that is missing from the view.

RESPONSE FORMAT
Respond ONLY with JSON in this exact structure:

  {
    "title": "<unchanged section title, verbatim from the user block>",
    "content": [
      { "type": "paragraph", "text": "..." },
      { "type": "bullet",    "items": ["..."] },
      { "type": "table",     "rows": [["col1","col2"], ["v1","v2"]] }
    ]
  }

No prose, no explanation, no markdown fences around the JSON.

VERIFICATION CHECKLIST (apply silently before returning)
  - I used [ITEM N] / [ROW N] to uniquely identify targets.
  - I matched specific content names when the instruction provided them.
  - I did NOT delete items at the same position when the named target
    was missing from the view.
  - All untouched items are preserved EXACTLY as they appeared.
  - The "title" key matches the section title given in the user block,
    verbatim and without rewording.
"""


def build_edit_prompt(
    *,
    section_number: int,
    section_title: str,
    current_section_content_numbered: str,
    section_json: Optional[Dict[str, Any]],
    user_instruction: str,
) -> str:
    """
    Compose the variable user-content block for the new-orchestrator
    edit call. Pair this output with `EDIT_SYSTEM_PROMPT` as the
    system message.
    """
    title_clean = _clean_section_title(section_title)
    section_json_block = (
        json.dumps(section_json, indent=2)
        if section_json is not None
        else "(section data not provided)"
    )

    return (
        f"<section_context>\n"
        f'You are updating Section #{section_number} titled "{title_clean}".\n'
        f"Make edits ONLY in this section.\n"
        f"</section_context>\n\n"
        f"<current_section_content_numbered>\n"
        f"{current_section_content_numbered}\n"
        f"</current_section_content_numbered>\n\n"
        f"<full_section_json>\n"
        f"THIS IS THE STARTING POINT. EDIT THIS JSON:\n"
        f"{section_json_block}\n"
        f"</full_section_json>\n\n"
        f"<user_instruction>\n"
        f"{user_instruction}\n"
        f"</user_instruction>\n"
    )


# ---------------------------------------------------------------------------
# Backward-compat form -- used unchanged by lambda_brd_chat.py:1637
# during the dual-ship window. Returns ONE combined prompt string
# matching the old function's exact output BYTE FOR BYTE (including
# 8 lines with intentional trailing whitespace).
#
# DO NOT REFACTOR THIS FUNCTION'S OUTPUT WITHOUT REGRESSION-TESTING
# THE EXISTING lambda_brd_chat HANDLERS -- the prompt has been tuned
# over many production calls and small wording changes have caused
# subtle behaviour drift before.
# ---------------------------------------------------------------------------

def get_brd_update_prompt(user_instruction: str, conversation_history: List[Dict], section_list: str, section_number: int, section_title: str, current_section_content_numbered: str, section_json: Optional[Dict] = None) -> str:
    """Construct the prompt for updating a BRD section. section_json is the section dict to edit (included so Claude sees the actual table/content)."""
    
    # Clean title
    section_title_clean = re.sub(r'^\d+\.\s*', '', section_title).strip()
    
    # Embed actual section JSON so Claude has the table/content to edit (fixes "I don't see the table")
    full_section_json_block = ""
    if section_json is not None:
        full_section_json_block = json.dumps(section_json, indent=2)
    else:
        full_section_json_block = "(section data not provided)"
    
    prompt = f"""You are a documentation assistant. You MUST update BRD section #{section_number} based on the user's instruction.

<critical_instruction>
You MUST interpret the user's instruction LITERALLY and PRECISELY. 
Your goal is to modify ONLY the specific items requested and PRESERVE everything else exactly as is.

CRITICAL RULE - UNIQUE ITEM NUMBERING:
The <current_section_content_numbered> section uses GLOBAL unique identifiers:
- [ITEM N] for bullet points (unique across ALL lists in this section)
- [ROW N] for table rows

ALWAYS use these unique identifiers to locate items.
Example: If you see [ITEM 1]...[ITEM 5] in List A, and [ITEM 6]...[ITEM 8] in List B.
"Delete 1st item of List B" means DELETE [ITEM 6].

CRITICAL RULE - CONTENT IDENTIFICATION PRIORITY:
When the instruction identifies items by their content (e.g., "remove Enterprise Clients", "change Role of Sarah"), 
you MUST match that specific content.

STALE REFERENCE HANDLING (Double Deletion Prevention):
If the instruction mentions specific item names or content (e.g., "remove Enterprise Clients"), 
but those specific items do NOT appear in the numbered view below:
1. STOP. Do NOT apply the operation to different items even if they are at the same position.
2. Assume the action has already been taken (stale instruction).
3. Return the section UNCHANGED or with only valid updates applied.

QUANTITY SAFEGUARD:
If the instruction specifies a quantity (e.g. "remove 2 rows"), you MUST NOT remove more than that quantity.
If removing "last 2 rows" would result in removing 4 rows (e.g. because of ambiguity or previous deletions), STOP and remove only the last 2 VISIBLE rows.

ONLY use positional references (e.g., "remove last 2 rows") if the instruction is purely positional 
AND DOES NOT mention specific content that is missing.
</critical_instruction>

<examples>
CORRECT literal interpretation:
- "remove 4th point" = remove [ITEM 4].
- "delete the last 2 rows" = delete the last 2 data rows ([ROW N]).
- "Move 3rd item to second list" = Take [ITEM 3], remove from List A, add to List B.

CORRECT handling of stale references (PREVENT DOUBLE DELETION):
- Instruction: "Remove last 2 rows (Enterprise Clients and Legal Team)"
- Numbered view shows: [ROW 1] Sarah, [ROW 2] Michael, ... [ROW 6] Robert
- "Enterprise Clients" is MISSING.
- ACTION: DO NOTHING. The target items are already gone. Do NOT remove Emma and Robert.

WRONG handling (DATA LOSS):
- Instruction: "Remove last 2 rows (Enterprise Clients and Legal Team)"
- Numbered view shows: [ROW 1] Sarah ... [ROW 6] Robert
- WRONG Action: Removing Emma and Robert because they are now the "last 2". 
- CONSEQUENCE: Accidental data loss of valid rows.
</examples>

<section_context>
You are updating Section #{section_number} titled "{section_title_clean}".
Make edits ONLY in this section.
</section_context>

<current_section_content_numbered>
{current_section_content_numbered}
</current_section_content_numbered>

<full_section_json>
THIS IS THE STARTING POINT. EDIT THIS JSON:
{full_section_json_block}
</full_section_json>

<user_instruction>
{user_instruction}
</user_instruction>

<task>
1. READ the numbered content view above - Use [ITEM N] and [ROW N] to identify targets.
2. CHECK if the instruction names specific items. VERIFY if they exist.
3. IF missing and specific -> STOP (Stale/Idempotent).
4. IF present or purely positional -> APPLY change.
5. Apply valid changes and COPY all other items EXACTLY as they are.
6. Return the fully reconstructed section JSON.
</task>

<response_format>
Respond ONLY with JSON in this exact structure:
{{
    "title": "{section_title_clean}",
    "content": [
        {{ "type": "paragraph", "text": "..." }},
        {{ "type": "bullet", "items": ["item1","item2"] }},
        {{ "type": "table", "rows": [["col1","col2"],["v1","v2"]] }}
    ]
}}
</response_format>

<verification_checklist>
- [ ] I used [ITEM N] / [ROW N] to uniquely identify targets.
- [ ] I matched specific content names if provided in the instruction.
- [ ] I avoided deleting wrong items if the named targets were missing.
- [ ] I preserved all other items exactly.
- [ ] The title is exactly "{section_title_clean}".
</verification_checklist>
"""
    return prompt


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean_section_title(title: str) -> str:
    """Strip a leading <digits>. prefix from a section title (e.g.
    "4. Stakeholders" -> "Stakeholders"). Mirrors the inline cleanup
    the old function did at lambda_brd_chat.py:919."""
    return re.sub(r"^\d+\.\s*", "", title or "").strip()
