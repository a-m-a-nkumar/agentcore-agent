"""
Langfuse Client - Singleton wrapper for Langfuse observability
"""

import os
import logging
from langfuse import Langfuse

logger = logging.getLogger(__name__)

_langfuse_instance: Langfuse = None


def get_langfuse() -> Langfuse:
    """Return the shared Langfuse client instance, initializing it on first call."""
    global _langfuse_instance
    if _langfuse_instance is None:
        _langfuse_instance = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        )
        logger.info("Langfuse client initialized")
    return _langfuse_instance
