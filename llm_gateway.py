import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = "Claude-4.5-Sonnet"
DEFAULT_BASE_URL = "https://dlxai-dev.deluxe.com/proxy"
DEFAULT_API_KEY = "sk-2cdb551cf35f418ea88b36"

# ── Cost-normalized token accounting ──────────────────────────────────────
# Anthropic list prices ($ / 1M tokens). We re-value every call's usage into
# "Sonnet-4.5-equivalent" tokens so a cached read counts ~0.1x and a cheaper
# model counts proportionally less. Cache read = 0.1x input, 5-min cache write
# = 1.25x input (applied in _effective_tokens). Sonnet is the reference unit.
SONNET_IN = 3.0
SONNET_OUT = 15.0
MODEL_PRICES = {
    # lowercase substring -> (input_$/M, output_$/M)
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
    "opus": (15.0, 75.0),
}


def _model_rates(model: Optional[str]) -> Tuple[float, float]:
    """(input_$/M, output_$/M) for `model`. Raises ValueError on an unknown
    model so mispricing fails LOUD — the caller (_record_tokens_async's daemon
    thread) catches it, logs, and records raw-only, rather than silently
    pricing an unknown model as Sonnet (a 3x over- / 5x under-count)."""
    m = (model or "").lower()
    for key, rates in MODEL_PRICES.items():
        if key in m:
            return rates
    raise ValueError(f"unknown_model_for_pricing model={model!r} (add it to MODEL_PRICES)")


def _effective_tokens(usage_dict: Dict[str, Any], model: Optional[str]) -> Tuple[int, int]:
    """Re-value usage into Sonnet-4.5-equivalent (input, output) tokens.

    input side: (uncached + 0.1*cache_read + 1.25*cache_write) * (IN/3)
    output side: output * (OUT/15)
    """
    cache_read = int(usage_dict.get("cache_read_input_tokens") or 0)
    cache_write = int(usage_dict.get("cache_creation_input_tokens") or 0)
    prompt = int(usage_dict.get("prompt_tokens") or 0)
    uncached_in = max(prompt - cache_read - cache_write, 0)
    output = int(usage_dict.get("completion_tokens") or 0)
    in_rate, out_rate = _model_rates(model)
    eff_in = round((uncached_in + 0.1 * cache_read + 1.25 * cache_write) * (in_rate / SONNET_IN))
    eff_out = round(output * (out_rate / SONNET_OUT))
    return int(eff_in), int(eff_out)

# Singleton client — reused across calls to avoid connection overhead
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        base_url = os.getenv("DLXAI_GATEWAY_URL", DEFAULT_BASE_URL)
        api_key = os.getenv("DLXAI_GATEWAY_KEY", DEFAULT_API_KEY)
        _client = OpenAI(base_url=base_url, api_key=api_key, timeout=300.0)
    return _client


def _record_tokens_async(
    user_id: Optional[str],
    usage_dict: Dict[str, Any],
    model: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    """Fire-and-forget token accounting — never blocks the caller.

    Computes Sonnet-equivalent input/output, logs one structured `[TOKENS]`
    line per call (CloudWatch debuggability), then records to the DB:
      1. ECS / local backend (db_helper importable + DB reachable): direct
         UPDATE (raw + sonnet-equivalent cols + daily upsert).
      2. Lambda / agent container (no RDS): POST to the backend's
         /api/internal/record-tokens endpoint (BACKEND_URL + INTERNAL_API_KEY).
    """
    usage_dict = usage_dict or {}
    raw_total = int(usage_dict.get("total_tokens") or 0)

    def _write():
        # Re-value to Sonnet-equivalent units. Unknown model -> LOUD log,
        # eff=0 (never silently Sonnet-priced); raw is still recorded.
        try:
            eff_in, eff_out = _effective_tokens(usage_dict, model)
        except ValueError as e:
            logger.error(f"[TOKENS] {e} source={source} — recording raw only, eff=0")
            eff_in, eff_out = 0, 0

        cache_read = int(usage_dict.get("cache_read_input_tokens") or 0)
        cache_write = int(usage_dict.get("cache_creation_input_tokens") or 0)
        prompt = int(usage_dict.get("prompt_tokens") or 0)
        # Structured per-call breakdown — every call with usage, even no user_id.
        try:
            logger.info("[TOKENS] " + json.dumps({
                "ts": int(time.time()),
                "user_id": user_id,
                "source": source,
                "model": model,
                "raw_total": raw_total,
                "uncached_in": max(prompt - cache_read - cache_write, 0),
                "cache_read": cache_read,
                "cache_write": cache_write,
                "output": int(usage_dict.get("completion_tokens") or 0),
                "eff_in": eff_in,
                "eff_out": eff_out,
            }))
        except Exception:
            pass

        if not user_id or raw_total <= 0:
            return

        # Try direct DB write first (ECS path)
        try:
            from db_helper import increment_user_token_usage
            increment_user_token_usage(user_id, raw_total, eff_in, eff_out)
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
                f"(would have recorded {raw_total} tokens for user {user_id})"
            )
            return
        try:
            import ssl as _ssl
            from urllib import request as _urlreq
            from urllib.error import HTTPError as _HTTPError, URLError as _URLError

            body = json.dumps({
                "user_id": user_id, "tokens": raw_total,
                "effective_input": eff_in, "effective_output": eff_out,
                "source": source, "model": model,
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
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
    system_prompt: Optional[Union[str, List[Dict[str, Any]]]] = None,
    return_metadata: bool = False,
    user_id: Optional[str] = None,
    token_source: Optional[str] = None,
) -> Union[str, Dict]:
    """Call the Deluxe gateway.

    `system_prompt` accepts two shapes:
      - A string (legacy): wrapped as `{"role": "system", "content": <str>}`.
      - A list of content blocks (new — for Anthropic prompt caching):
        e.g. `[{"type": "text", "text": "...", "cache_control": {"type":
        "ephemeral"}}]`. Each block is passed through to the model
        unchanged. The DLX gateway's pass-through preserves cache_control
        (verified Step 0 of Phase 6 — see .scratch/gateway_cache_passthrough_test.py).

    When `return_metadata=True`, the returned dict includes `usage` with
    `cache_creation_input_tokens` / `cache_read_input_tokens` when present
    so callers can verify caching is working.
    """
    client = _get_client()
    resolved = model or os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    # Bedrock model IDs (e.g. "global.anthropic.claude-…") aren't valid on the
    # DLX AI gateway — fall back to the default gateway model.
    if any(tok in resolved for tok in ("anthropic", "bedrock", "amazon")):
        resolved = os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)

    if system_prompt:
        # List form: pass content blocks as-is so `cache_control` survives.
        # String form: legacy wrapper.
        if isinstance(system_prompt, list):
            sys_msg: Dict[str, Any] = {"role": "system", "content": system_prompt}
        else:
            sys_msg = {"role": "system", "content": system_prompt}
        messages = [sys_msg] + list(messages)

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

    usage_dict: Dict[str, Any] = {}
    usage = getattr(response, "usage", None)
    if usage:
        # openai-python v1 usage is a pydantic model; dump to dict so we
        # can capture cache_creation_input_tokens / cache_read_input_tokens
        # (returned by Anthropic-compat gateways) alongside the standard
        # prompt/completion counts.
        try:
            usage_dict = usage.model_dump()
        except AttributeError:
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        total = usage_dict.get("total_tokens") or 0
        cache_w = usage_dict.get("cache_creation_input_tokens") or 0
        cache_r = usage_dict.get("cache_read_input_tokens") or 0
        cache_suffix = f" cache_write={cache_w} cache_read={cache_r}" if (cache_w or cache_r) else ""
        logger.info(
            f"[LLM Gateway] {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} "
            f"source={token_source or '?'} "
            f"tokens prompt={usage_dict.get('prompt_tokens', '?')} "
            f"completion={usage_dict.get('completion_tokens', '?')} total={total}"
            f"{cache_suffix}"
        )
        _record_tokens_async(user_id, usage_dict, model=resolved, source=token_source)
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
            "usage": usage_dict,
        }
    return content


def chat_completion_stream(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
    system_prompt: Optional[str] = None,
    user_id: Optional[str] = None,
    token_source: Optional[str] = None,
):
    """
    Real SSE streaming via the OpenAI SDK against the DLX AI gateway.

    Yields strings already formatted as Server-Sent Events ready to relay
    straight to the HTTP client:

        data: {"type": "chunk", "text": "..."}\\n\\n
        ...
        data: {"type": "done"}\\n\\n

    Token usage is recorded once at the end of the stream via the standard
    _record_tokens_async path (same DB row, same source label, same auth).
    Requires the gateway to honour `stream_options={"include_usage": true}`,
    which the DLX AI proxy passes through to the upstream OpenAI-compatible
    backend so a final chunk with usage data arrives.
    """
    client = _get_client()
    resolved = model or os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    if any(tok in resolved for tok in ("anthropic", "bedrock", "amazon")):
        resolved = os.getenv("DLXAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)

    if system_prompt:
        messages = [{"role": "system", "content": system_prompt}] + list(messages)

    params: Dict = {
        "model": resolved,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        # Ask the proxy to forward a final chunk containing usage stats
        # so we can record token totals once the stream completes.
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens

    logger.info(
        f"[LLM Gateway] STREAM Calling model='{resolved}' max_tokens={max_tokens} "
        f"user={user_id or 'unknown'} source={token_source or '?'}"
    )
    import json as _json  # local — keeps top-level imports minimal
    start = time.time()
    total_chars = 0
    final_usage = None

    try:
        response = client.chat.completions.create(**params)
        for chunk in response:
            # OpenAI-compatible streams end with a chunk that has `usage`
            # populated and an empty `choices` list.
            usage_obj = getattr(chunk, "usage", None)
            if usage_obj:
                final_usage = usage_obj
                # Don't continue — some gateways still send choices on the
                # usage chunk, so fall through to delta extraction below.
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta else None
            if text:
                total_chars += len(text)
                yield f"data: {_json.dumps({'type': 'chunk', 'text': text})}\n\n"
    except Exception as e:
        logger.error(f"[LLM Gateway] STREAM error: {e}")
        yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    elapsed = time.time() - start

    if final_usage:
        try:
            usage_dict = final_usage.model_dump()
        except AttributeError:
            usage_dict = {
                "prompt_tokens": getattr(final_usage, "prompt_tokens", None),
                "completion_tokens": getattr(final_usage, "completion_tokens", None),
                "total_tokens": getattr(final_usage, "total_tokens", None),
            }
        total = usage_dict.get("total_tokens") or 0
        # Detect whether the gateway instruments cache fields on STREAMED usage.
        # If absent, effective-input over-counts cached reads as fresh input —
        # surface it (don't silently assume 0). If this fires for cache-heavy
        # streamed calls (edits / Q&A), file a gateway-instrumentation ticket.
        if (
            "cache_read_input_tokens" not in usage_dict
            and "cache_creation_input_tokens" not in usage_dict
        ):
            logger.warning(
                f"[TOKENS] stream_missing_cache_fields source={token_source} model={resolved} "
                f"— effective_input may over-count cached reads"
            )
        logger.info(
            f"[LLM Gateway] STREAM {elapsed:.1f}s model={resolved} "
            f"user={user_id or 'unknown'} source={token_source or '?'} "
            f"tokens prompt={usage_dict.get('prompt_tokens', '?')} "
            f"completion={usage_dict.get('completion_tokens', '?')} total={total} "
            f"chars_streamed={total_chars}"
        )
        _record_tokens_async(user_id, usage_dict, model=resolved, source=token_source)
    else:
        logger.info(
            f"[LLM Gateway] STREAM {elapsed:.1f}s model={resolved} "
            f"user={user_id or 'unknown'} chars={total_chars} (no usage in final chunk)"
        )

    yield f"data: {_json.dumps({'type': 'done'})}\n\n"


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
        try:
            usage_dict = usage.model_dump()
        except AttributeError:
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        total = usage_dict.get("total_tokens") or 0
        logger.info(
            f"[LLM Gateway] Tool {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} "
            f"source={token_source or '?'} "
            f"finish={response.choices[0].finish_reason} "
            f"tokens prompt={usage_dict.get('prompt_tokens', '?')} "
            f"completion={usage_dict.get('completion_tokens', '?')} total={total}"
        )
        _record_tokens_async(user_id, usage_dict, model=resolved, source=token_source)
    else:
        logger.info(f"[LLM Gateway] Tool {elapsed:.1f}s model={resolved} user={user_id or 'unknown'} finish={response.choices[0].finish_reason} (no usage)")

    if not response or not response.choices:
        raise ValueError(f"Gateway returned empty response for model={resolved}")

    msg = response.choices[0].message
    return {
        "message": msg,
        "finish_reason": response.choices[0].finish_reason,
    }
