"""
Shared utilities for internal (MCP) endpoints.
- API key validation
- In-memory session and event stores for test workflows
"""

import asyncio
import json
import logging
import os

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ============================================
# API KEY VALIDATION
# ============================================

def validate_api_key(x_api_key: str) -> str:
    """Validate API key and return associated project_id."""
    internal_keys_str = os.environ.get("INTERNAL_API_KEYS", "{}")
    try:
        valid_keys = json.loads(internal_keys_str)
    except json.JSONDecodeError:
        logger.error("Failed to parse INTERNAL_API_KEYS from env")
        valid_keys = {}

    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return valid_keys[x_api_key]  # returns project_id


# ============================================
# IN-MEMORY SESSION / EVENT STORES (Test Workflow)
# ============================================

# session_id → { project_id, scenarios, prompt, gherkin, ... }
test_sessions: dict = {}

# project_id → asyncio.Event
project_events: dict = {}
