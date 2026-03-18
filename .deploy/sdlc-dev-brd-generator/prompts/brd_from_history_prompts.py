"""
BRD from History Prompt Templates

This module contains the prompt template for generating BRDs from analyst chat history.
"""

# BRD Generation from Chat History Prompt
# This prompt instructs Bedrock to generate BRD from conversation history
BRD_FROM_CHAT_PROMPT = """
════════════════════════════════════════════════════════════════
SPECIAL INSTRUCTIONS FOR BRD GENERATION FROM ANALYST CHAT HISTORY
════════════════════════════════════════════════════════════════

CONTEXT:
You are generating a Business Requirements Document (BRD) from a requirements discovery conversation.
The conversation below is NOT a formal meeting transcript - it's a natural dialogue between a Business Analyst and a User.

CRITICAL: You MUST follow the TEMPLATE structure provided below exactly.
All 16 sections from the template MUST be present in the final BRD.

SOURCE TRUTH & ATTRIBUTION RULES (CRITICAL):
- The conversation transcript below labels speakers as "User:" and "Analyst:".
- CONTENT FROM "User:" -> MUST be labeled [USER-PROVIDED].
- CONTENT FROM "Analyst:" -> MUST be labeled [AI ASSUMPTION] (if unconfirmed) or [AGREED - AI SUGGESTION] (if confirmed).
- DO NOT attribute "Analyst:" statements as [USER-PROVIDED] unless the User explicitly says "Yes" or "Agreed".

════════════════════════════════════════════════════════════════
LABELING REQUIREMENTS (MANDATORY):
════════════════════════════════════════════════════════════════

You MUST clearly label ALL content in the BRD as one of:

[USER-PROVIDED]
- Information explicitly stated by the USER in the conversation
- Example: "[USER-PROVIDED] System must support 10,000 concurrent users"

[AI ASSUMPTION]
- Information you are inferring based on professional judgment (defaults) OR unconfirmed Analyst suggestions
- Example: "[AI ASSUMPTION] 24/7 availability required (standard for customer-facing chatbots)"

[PARTIALLY SPECIFIED - USER + AI]
- User provided partial information, you are expanding it
- Example: "[PARTIALLY SPECIFIED] User mentioned 'mobile app'; assuming iOS and Android support"

[AGREED - AI SUGGESTION]
- The Analyst suggested specific research/features, and the User EXPLICITLY agreed (e.g. "Yes, let's do that").
- Example: "[AGREED - AI SUGGESTION] Integration with Stripe (Analyst suggested, User confirmed)"

════════════════════════════════════════════════════════════════
CONFIDENCE LEVELS FOR AI ASSUMPTIONS:
════════════════════════════════════════════════════════════════

- HIGH CONFIDENCE: Strongly implied by user
  Example: "[AI ASSUMPTION - HIGH CONFIDENCE] 24/7 availability (user said 'always available')"

- MEDIUM CONFIDENCE: Reasonable inference
  Example: "[AI ASSUMPTION - MEDIUM CONFIDENCE] Mobile-first design (user discussed mobile app)"

- LOW CONFIDENCE: Standard industry practice
  Example: "[AI ASSUMPTION - LOW CONFIDENCE] GDPR compliance (standard for customer data)"

- TO BE CONFIRMED: Needs validation
  Example: "[AI ASSUMPTION - TO BE CONFIRMED] Integration with Salesforce CRM"

════════════════════════════════════════════════════════════════
CREATING COMPLETE BRD SKELETON:
════════════════════════════════════════════════════════════════

RULE: NEVER leave any section empty. Every section MUST have content.

For each section:
1. If user discussed it → Use [USER-PROVIDED]
2. If user partially discussed it → Use [PARTIALLY SPECIFIED - USER + AI]
3. If user didn't discuss it → Use [AI ASSUMPTION] with professional defaults

NEVER write: "Not discussed", "Information not available", "TBD"
ALWAYS provide content with appropriate labels.

════════════════════════════════════════════════════════════════
INSTRUCTIONS:
════════════════════════════════════════════════════════════════

1. READ the entire conversation below
2. IDENTIFY what the USER explicitly said (mark as [USER-PROVIDED])
3. IDENTIFY what the USER partially mentioned (mark as [PARTIALLY SPECIFIED])
4. FILL ALL GAPS with professional assumptions (mark as [AI ASSUMPTION])
5. FOLLOW the template structure exactly (all 16 sections)
6. USE bullet points, tables, and clear formatting
7. BE SPECIFIC and actionable in every section

════════════════════════════════════════════════════════════════
TEMPLATE STRUCTURE TO FOLLOW:
════════════════════════════════════════════════════════════════

{template}

════════════════════════════════════════════════════════════════
CONVERSATION HISTORY:
════════════════════════════════════════════════════════════════

{conversation}

════════════════════════════════════════════════════════════════
NOW GENERATE THE COMPLETE BRD:
════════════════════════════════════════════════════════════════

Return only the completed BRD as plain text following the template structure exactly.
"""


def get_brd_from_history_prompt(template: str, conversation: str) -> str:
    """
    Generate the full BRD from history prompt with template and conversation.
    
    Args:
        template: The BRD template text extracted from the DOCX file
        conversation: The formatted conversation history
        
    Returns:
        The complete prompt ready to send to Bedrock
    """
    return BRD_FROM_CHAT_PROMPT.format(
        template=template,
        conversation=conversation
    )


__all__ = [
    "BRD_FROM_CHAT_PROMPT",
    "get_brd_from_history_prompt",
]
