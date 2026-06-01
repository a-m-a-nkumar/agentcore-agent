"""
Env-configurable prompt-cache helper.

`cached_system_blocks(text)` wraps a system prompt into the SINGLE cached
content block the DLX gateway forwards reliably. (Multi-block system
payloads are collapsed by the gateway — a two-block payload arrives at
Bedrock with the first block dropped, verified in the Phase 6 cache smoke
test. One-block payloads pass through and cache cleanly.)

The cache_control TTL is read from the BRD_CACHE_TTL env var:

  - unset / "5m" : {"type": "ephemeral"} — Anthropic's 5-minute cache,
                   verified through the DLX gateway
                   (.scratch/gateway_cache_passthrough_test.py).
  - "1h"         : {"type": "ephemeral", "ttl": "1h"} — the extended-cache
                   beta. Only set this once the gateway is confirmed to
                   forward the extended-cache-ttl beta header, or Bedrock
                   may reject the request.

Caching only ACTIVATES for cached prefixes ≥1024 tokens (Sonnet). Shorter
system prompts carry the marker harmlessly — Anthropic ignores it and
returns the call uncached, no error.
"""

import os
from typing import Any, Dict, List


def cache_control_value() -> Dict[str, Any]:
    """The cache_control dict for system blocks, per BRD_CACHE_TTL."""
    ttl = (os.getenv("BRD_CACHE_TTL", "5m") or "5m").strip().lower()
    if ttl in ("1h", "60m", "3600s"):
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


def cached_system_blocks(text: str) -> List[Dict[str, Any]]:
    """Wrap a system prompt string into the one-block cached payload."""
    return [{"type": "text", "text": text, "cache_control": cache_control_value()}]
