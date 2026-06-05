"""
Prompts package for BRD generation and chat functionality.

This package contains all prompt templates used across the Lambda functions.
"""

from .brd_generator_prompts import (
    BRD_GENERATION_PROMPT_BASE,
    BRD_GENERATION_INSTRUCTIONS,
    BRD_REQUIRED_SECTIONS,
    get_full_brd_generation_prompt,
)

from .brd_from_history_prompts import (
    BRD_FROM_CHAT_PROMPT,
    get_brd_from_history_prompt,
)

from .requirements_gathering_prompts import (
    MARY_REQUIREMENTS_PROMPT,
    get_requirements_gathering_prompt,
)

# ---- SAD prompts (multi-session Design Assistant, SAD phase) ----
from .sad_intent_router import (
    SAD_INTENTS,
    SAD_INTENT_ROUTER_SYSTEM_PROMPT,
    build_router_prompt,
    get_router_system_prompt,
)
from .sad_section_prompts import (
    SECTION_SYSTEM_PROMPT,
    SECTION_PROMPT_BUILDERS,
    ARSR_IN_SCOPE_CATEGORIES,
    ARSR_OUT_OF_SCOPE_CATEGORIES,
)
from .sad_audit_prompts import (
    AUDIT_SYSTEM_PROMPT,
    build_audit_prompt,
)
from .sad_edit_prompts import (
    EDIT_SYSTEM_PROMPT,
    SUGGEST_SYSTEM_PROMPT,
    build_edit_prompt,
    build_suggest_prompt,
)
from .sad_qa_prompts import (
    QA_SYSTEM_PROMPT,
    build_qa_prompt,
)
from .sad_gather_prompts import (
    SAD_GATHER_SYSTEM_PROMPT,
    build_gather_prompt,
)

__all__ = [
    # BRD
    "BRD_GENERATION_PROMPT_BASE",
    "BRD_GENERATION_INSTRUCTIONS",
    "BRD_REQUIRED_SECTIONS",
    "get_full_brd_generation_prompt",
    "BRD_FROM_CHAT_PROMPT",
    "get_brd_from_history_prompt",
    "MARY_REQUIREMENTS_PROMPT",
    "get_requirements_gathering_prompt",
    # SAD
    "SAD_INTENTS",
    "SAD_INTENT_ROUTER_SYSTEM_PROMPT",
    "build_router_prompt",
    "get_router_system_prompt",
    "SECTION_SYSTEM_PROMPT",
    "SECTION_PROMPT_BUILDERS",
    "ARSR_IN_SCOPE_CATEGORIES",
    "ARSR_OUT_OF_SCOPE_CATEGORIES",
    "AUDIT_SYSTEM_PROMPT",
    "build_audit_prompt",
    "EDIT_SYSTEM_PROMPT",
    "SUGGEST_SYSTEM_PROMPT",
    "build_edit_prompt",
    "build_suggest_prompt",
    "QA_SYSTEM_PROMPT",
    "build_qa_prompt",
    "SAD_GATHER_SYSTEM_PROMPT",
    "build_gather_prompt",
]

