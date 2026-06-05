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

__all__ = [
    "BRD_GENERATION_PROMPT_BASE",
    "BRD_GENERATION_INSTRUCTIONS",
    "BRD_REQUIRED_SECTIONS",
    "get_full_brd_generation_prompt",
    "BRD_FROM_CHAT_PROMPT",
    "get_brd_from_history_prompt",
    "MARY_REQUIREMENTS_PROMPT",
    "get_requirements_gathering_prompt",
]

