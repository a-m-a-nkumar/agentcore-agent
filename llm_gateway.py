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


def _record_tokens_async(user_id: Optional[str], total_tokens: int, source: Optional[str] = None) -> None:
    """Fire-and-forget write to users.token_usage — never blocks the caller.

    Dual-mode:
      1. ECS / local backend (db_helper importable AND DB reachable):
         direct UPDATE on users.token_usage.
      2. Lambda / agent container (no RDS access): POST to the backend's
         /api/internal/record-tokens endpoint using BACKEND_URL +
         INTERNAL_API_KEY env vars.
    """
    if not user_id or not total_tokens or total_tokens <= 0:
        return

    def _write():
        # Try direct DB write first (ECS path)
        try:
            from db_helper import increment_user_token_usage
            increment_user_token_usage(user_id, total_tokens)
            return
        except Exception as e:
            logger.debug(f"[LLM Gateway] direct DB write skipped ({e}); falling back to HTTP callback")

        # Fallback: HTTP callback to backend (Lambda / agent path).
        # Use stdlib urllib so this works inside Lambda zips that don't ship
        # python-requests.
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        api_key = os.getenv("INTERNAL_API_KEY", "")
        if not backend_url or not api_key:
            logger.warning(
                f"[LLM Gateway] cannot record tokens: BACKEND_URL/INTERNAL_API_KEY env vars not set "
                f"(would have recorded {total_tokens} tokens for user {user_id})"
            )
            return
        try:
            import json as _json
            import ssl as _ssl
            from urllib import request as _urlreq
            from urllib.error import HTTPError as _HTTPError, URLError as _URLError

            body = _json.dumps({
                "user_id": user_id, "tokens": total_tokens, "source": source,
            }).encode("utf-8")
            req = _urlreq.Request(
                f"{backend_url}/api/internal/record-tokens",
                data=body,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                method="POST",
            )
            # The deployed dev backend (sdlc-dev.deluxe.com) sits behind a
            # Deluxe-internal CA whose chain isn't in the Lambda runtime's
            # default trust store. Allow callers (Lambdas, agent containers)
            # to opt out of cert verification for this internal callback by
            # setting INTERNAL_TLS_VERIFY=0. Payload is just tokens + UUID.
            ctx = None
            if os.getenv("INTERNAL_TLS_VERIFY", "1") == "0":
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
            with _urlreq.urlopen(req, timeout=5, context=ctx) as resp:
                if resp.status >= 400:
                    logger.warning(f"[LLM Gateway] record-tokens callback {resp.status}: {resp.read()[:200]!r}")
        except _HTTPError as e:
            logger.warning(f"[LLM Gateway] record-tokens callback HTTP {e.code}: {e.read()[:200]!r}")
        except (_URLError, Exception) as e:
            logger.warning(f"[LLM Gateway] record-tokens callback failed for {user_id}: {e}")

    threading.Thread(target=_write, daemon=True).start()


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
    system_prompt: Optional[str] = None,
    return_metadata: bool = False,
    user_id: Optional[str] = None,
    token_source: Optional[str] = None,
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
            f"source={token_source or '?'} "
            f"tokens prompt={getattr(usage, 'prompt_tokens', '?')} "
            f"completion={getattr(usage, 'completion_tokens', '?')} total={total}"
        )
        _record_tokens_async(user_id, total, source=token_source)
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
    token_source: Optional[str] = None,
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
            f"source={token_source or '?'} "
            f"finish={response.choices[0].finish_reason} "
            f"tokens prompt={getattr(usage, 'prompt_tokens', '?')} "
            f"completion={getattr(usage, 'completion_tokens', '?')} total={total}"
        )
        _record_tokens_async(user_id, total, source=token_source)
    else:
        logger.info(f"[LLM Gateway] Tool {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} finish={response.choices[0].finish_reason} (no usage)")

    if not response or not response.choices:
        raise ValueError(f"Gateway returned empty response for model={resolved}")

    msg = response.choices[0].message
    return {
        "message": msg,
        "finish_reason": response.choices[0].finish_reason,
    }
