import logging
import os
import threading
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


def _record_tokens_async(user_id: Optional[str], total_tokens: int) -> None:
    """Fire-and-forget write to users.token_usage — never blocks the caller."""
    if not user_id or not total_tokens or total_tokens <= 0:
        return

    def _write():
        try:
            from db_helper import increment_user_token_usage
            increment_user_token_usage(user_id, total_tokens)
        except Exception as e:
            logger.warning(f"[LLM Gateway] token_usage write failed for {user_id}: {e}")

    threading.Thread(target=_write, daemon=True).start()


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
    system_prompt: Optional[str] = None,
    return_metadata: bool = False,
    user_id: Optional[str] = None,
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

    usage = getattr(response, "usage", None)
    if usage:
        total = getattr(usage, "total_tokens", 0) or 0
        logger.info(
            f"[LLM Gateway] {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} "
            f"tokens prompt={getattr(usage, 'prompt_tokens', '?')} "
            f"completion={getattr(usage, 'completion_tokens', '?')} total={total}"
        )
        _record_tokens_async(user_id, total)
    else:
        logger.info(f"[LLM Gateway] {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} (no usage)")

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


def chat_completion_with_tools(
    messages: List[Dict],
    tools: List[Dict],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    user_id: Optional[str] = None,
) -> Dict:
    """
    Gateway call with OpenAI-style function calling (tools).

    Returns dict with:
      - "message": the assistant message object (may contain tool_calls)
      - "finish_reason": "stop" | "tool_calls"
    """
    client = _get_client()
    resolved = model or os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    if any(tok in resolved for tok in ("anthropic", "bedrock", "amazon")):
        resolved = os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)

    params = {
        "model": resolved,
        "messages": messages,
        "temperature": temperature,
        "tools": tools,
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens

    logger.info(f"[LLM Gateway] Tool call → model='{resolved}' tools={[t['function']['name'] for t in tools]}")
    start = time.time()
    response = client.chat.completions.create(**params)
    elapsed = time.time() - start

    usage = getattr(response, "usage", None)
    if usage:
        total = getattr(usage, "total_tokens", 0) or 0
        logger.info(
            f"[LLM Gateway] Tool {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} "
            f"finish={response.choices[0].finish_reason} "
            f"tokens prompt={getattr(usage, 'prompt_tokens', '?')} "
            f"completion={getattr(usage, 'completion_tokens', '?')} total={total}"
        )
        _record_tokens_async(user_id, total)
    else:
        logger.info(f"[LLM Gateway] Tool {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} finish={response.choices[0].finish_reason} (no usage)")

    if not response or not response.choices:
        raise ValueError(f"Gateway returned empty response for model={resolved}")

    msg = response.choices[0].message
    return {
        "message": msg,
        "finish_reason": response.choices[0].finish_reason,
    }
