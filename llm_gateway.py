import logging
import os
import time
from typing import Dict, List, Optional, Union

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = "Claude-4.5-Sonnet"
DEFAULT_BASE_URL = "https://dlxai-dev.deluxe.com/proxy"
DEFAULT_API_KEY = "sk-2cdb551cf35f418ea88b36"

# Singleton client — reused across calls to avoid connection overhead
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        base_url = os.getenv("DLXAI_GATEWAY_URL", DEFAULT_BASE_URL)
        api_key = os.getenv("DLXAI_GATEWAY_KEY", DEFAULT_API_KEY)
        _client = OpenAI(base_url=base_url, api_key=api_key, timeout=300.0)
    return _client


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
    system_prompt: Optional[str] = None,
    return_metadata: bool = False,
) -> Union[str, Dict]:
    client = _get_client()
    resolved = model or os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    # Bedrock model IDs (e.g. "global.anthropic.claude-…") aren't valid on the
    # DLX AI gateway — fall back to the default gateway model.
    if any(tok in resolved for tok in ("anthropic", "bedrock", "amazon")):
        resolved = os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)

    if system_prompt:
        messages = [{"role": "system", "content": system_prompt}] + list(messages)

    params = {
        "model": resolved,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens

    logger.info(f"[LLM Gateway] Calling model='{resolved}' max_tokens={max_tokens}")
    start = time.time()
    response = client.chat.completions.create(**params)
    elapsed = time.time() - start
    logger.info(f"[LLM Gateway] Response received in {elapsed:.1f}s")

    if not response or not response.choices:
        logger.error(f"[LLM Gateway] Empty response from gateway: {response}")
        raise ValueError(f"Gateway returned empty response for model={resolved}")

    content = (response.choices[0].message.content or "").strip()
    if return_metadata:
        return {
            "content": content,
            "finish_reason": getattr(response.choices[0], "finish_reason", None),
        }
    return content
