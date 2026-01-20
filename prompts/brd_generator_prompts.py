"""
BRD Generator Prompt Templates

This module contains all prompt templates used by the lambda_brd_generator.py Lambda function.
Separating prompts from code logic makes them easier to maintain, version, and test.
"""

# Base prompt that introduces the task
BRD_GENERATION_PROMPT_BASE = """You are a Product Manager in a software solutions company for payments. A discussion has happened within a product team, and the meeting transcript is available. You are tasked with creating a Business Requirements Document (BRD).

Below is the template structure that must be followed exactly.

### Utmost IMPORTANT
"Keep the BRD concise to fit within available tokens"
"Use bullet points and tables where possible instead of long paragraphs"
"Prioritize covering ALL 16 sections concisely rather than detailed elaboration"
"Be brief but comprehensive - quality over quantity"


### CRITICAL: The BRD MUST contain ALL 16 sections listed below:"""

# Required BRD sections
BRD_REQUIRED_SECTIONS = [
    "1. Document Overview",
    "2. Purpose",
    "3. Background / Context",
    "4. Stakeholders",
    "5. Scope",
    "6. Business Objectives & ROI",
    "7. Functional Requirements",
    "8. Non-Functional Requirements",
    "9. User Stories / Use Cases",
    "10. Assumptions",
    "11. Constraints",
    "12. Acceptance Criteria / KPIs",
    "13. Timeline / Milestones",
    "14. Risks and Dependencies",
    "15. Approval & Review",
    "16. Glossary & Appendix",
]

# Detailed instructions for BRD generation
BRD_GENERATION_INSTRUCTIONS = """
### Instructions:
1. Preserve Structure
   - Follow the exact structural integrity of the template.
   - You MUST generate ALL 16 sections listed above, even if some sections have limited information from the transcript.
   - For sections with limited transcript information, use reasonable professional assumptions and standard practices.
   - Maintain all sections, headings, and tables exactly as they appear.
   - Place the transcript-derived information into the corresponding sections without adding or removing sections.
   - DO NOT skip any of the 16 sections - all must be present in the final BRD.
   - CRITICAL: DO NOT create numbered subsections beyond section 16. If you need to show use case flows, steps, or sub-items within a section, use bullet points, tables, or unnumbered paragraphs instead of creating new numbered sections like "17.", "18.", etc.
   - For example, in section 9 (User Stories / Use Cases), if you need to show a use case flow, use bullet points or a table, NOT numbered items like "11. Step 1", "12. Step 2" that could be mistaken for new sections."""


def get_full_brd_generation_prompt(template_text: str, transcript_text: str) -> str:
    """
    Construct the full BRD generation prompt.
    
    Args:
        template_text: The BRD template structure to follow
        transcript_text: The meeting transcript to extract information from
        
    Returns:
        Complete prompt string ready for LLM invocation
    """
    # Build sections list
    sections_text = "\n".join(BRD_REQUIRED_SECTIONS)
    
    # Combine all parts
    prompt = f"""{BRD_GENERATION_PROMPT_BASE}
{sections_text}

{BRD_GENERATION_INSTRUCTIONS}

   
--- TEMPLATE ---
{template_text}

--- TRANSCRIPT ---
{transcript_text}

Return only the completed BRD as plain text.
""".strip()
    
    return prompt


def get_prompt_base_length() -> int:
    """
    Get the approximate character length of the base prompt (without template/transcript).
    Useful for token estimation.
    
    Returns:
        Character count of base prompt components
    """
    sections_text = "\n".join(BRD_REQUIRED_SECTIONS)
    base_prompt = f"{BRD_GENERATION_PROMPT_BASE}\n{sections_text}\n{BRD_GENERATION_INSTRUCTIONS}"
    return len(base_prompt)


# Optional: Configuration constants
class PromptConfig:
    """Configuration constants for prompt generation"""
    
    # Token estimation (rough: 1 token ≈ 4 characters)
    CHARS_PER_TOKEN = 4
    
    # Maximum input tokens (instructions + template + transcript)
    MAX_INPUT_TOKENS = 2000
    
    # Safety margin for token calculations
    SAFETY_MARGIN_TOKENS = 200
    
    # Reserved tokens for template
    RESERVED_TEMPLATE_TOKENS = 600
    
    # Minimum tokens for transcript
    MIN_TRANSCRIPT_TOKENS = 500
    
    # Total context window for Llama 3.1 8B
    TOTAL_CONTEXT_TOKENS = 8192
    
    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Estimate token count from character count"""
        return len(text) // cls.CHARS_PER_TOKEN
    
    @classmethod
    def calculate_available_output_tokens(cls, prompt_tokens: int) -> int:
        """Calculate available tokens for output given input prompt size"""
        return cls.TOTAL_CONTEXT_TOKENS - prompt_tokens - cls.SAFETY_MARGIN_TOKENS
