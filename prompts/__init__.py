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

__all__ = [
    "BRD_GENERATION_PROMPT_BASE",
    "BRD_GENERATION_INSTRUCTIONS",
    "BRD_REQUIRED_SECTIONS",
    "get_full_brd_generation_prompt",
]
