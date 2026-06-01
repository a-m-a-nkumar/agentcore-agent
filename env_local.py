"""
LOCAL ENVIRONMENT CONFIGURATION
================================
Use this file when running the project on your LOCAL machine.

In environment.py, make sure this line is UNcommented:
    from env_local import *

Local settings:
  - S3 uploads use plain boto3 (NO KMS encryption)
  - Database uses a direct password from DATABASE_PASSWORD env var
  - LLM calls go directly to AWS Bedrock (no API Gateway proxy)
  - Agent ARNs point to the local AWS account (448049797912)
  - Strands agents use BedrockModel directly (no Deluxe gateway)
  - Lambda names use local account (448049797912)
"""

import os
import json
import logging
import boto3
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# S3 Configuration  (no KMS)
# ---------------------------------------------------------------------------
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_s3_client():
    """Return a plain boto3 S3 client (no KMS)."""
    return boto3.client("s3", region_name=AWS_REGION)


def s3_put_object(
    key: str,
    body,
    content_type: str = "application/octet-stream",
    bucket: str = None,
) -> str:
    """Upload an object to S3 without KMS encryption (local dev)."""
    if bucket is None:
        bucket = S3_BUCKET_NAME
    if isinstance(body, str):
        body = body.encode("utf-8")
    client = get_s3_client()
    client.put_object(Key=key, Body=body, Bucket=bucket, ContentType=content_type)
    logger.info(f"[LOCAL S3] Uploaded s3://{bucket}/{key}")
    return f"s3://{bucket}/{key}"


# ---------------------------------------------------------------------------
# Database Configuration  (direct password, no Secrets Manager)
# ---------------------------------------------------------------------------
def get_db_params() -> dict:
    """Return DB connection params using a plain password from env vars."""
    return {
        "host": os.getenv("DATABASE_HOST", os.getenv("POSTGRES_HOST", "localhost")),
        "port": int(os.getenv("DATABASE_PORT", os.getenv("POSTGRES_PORT", "5432"))),
        "database": os.getenv("DATABASE_NAME", os.getenv("POSTGRES_DB", "postgres")),
        "user": os.getenv("DATABASE_USER", os.getenv("POSTGRES_USER", "postgres")),
        "password": os.getenv("DATABASE_PASSWORD", ""),
    }


# ---------------------------------------------------------------------------
# Agent ARNs  (local AWS account: 448049797912)
# ---------------------------------------------------------------------------
DEFAULT_AGENT_ARN = os.getenv(
    "AGENT_ARN",
    "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK",
)
DEFAULT_ANALYST_AGENT_ARN = os.getenv(
    "ANALYST_AGENT_ARN",
    "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/Analyst_agent-kCoE8v38c0",
)


# ---------------------------------------------------------------------------
# LLM  (direct AWS Bedrock, no API Gateway proxy)
# ---------------------------------------------------------------------------
DEFAULT_BEDROCK_MODEL = os.getenv(
    "BEDROCK_MODEL_ID",
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
)


# ---------------------------------------------------------------------------
# Agent Model — Strands agents use Bedrock directly (Local)
# ---------------------------------------------------------------------------
AGENT_MODEL_PROVIDER = "bedrock"
DEFAULT_DLXAI_GATEWAY_URL = ""   # Not used locally
DEFAULT_DLXAI_GATEWAY_KEY = ""   # Not used locally
DEFAULT_GATEWAY_MODEL = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# ---------------------------------------------------------------------------
# Embedding config  (Local: Titan v1 at 1536 dimensions)
# ---------------------------------------------------------------------------
EMBEDDING_DIMENSIONS = 1536
BEDROCK_EMBEDDING_MODEL = "amazon.titan-embed-text-v1"

# ---------------------------------------------------------------------------
# Lambda name defaults  (Local AWS account: 448049797912)
# ---------------------------------------------------------------------------
DEFAULT_LAMBDA_BRD_GENERATOR           = "brd_generator_lambda"
DEFAULT_LAMBDA_BRD_RETRIEVER           = "brd_retriever_lambda"
DEFAULT_LAMBDA_BRD_CHAT                = "brd_chat_lambda"
DEFAULT_LAMBDA_REQUIREMENTS_GATHERING  = "requirements_gathering_lambda"
DEFAULT_LAMBDA_BRD_FROM_HISTORY        = "brd_from_history_lambda"

# ---------------------------------------------------------------------------
# AgentCore defaults  (Local)
# ---------------------------------------------------------------------------
DEFAULT_AGENTCORE_MEMORY_ID  = os.getenv("AGENTCORE_MEMORY_ID", "Test-DGwqpP7Rvj")
DEFAULT_AGENTCORE_ACTOR_ID   = os.getenv("AGENTCORE_ACTOR_ID", "brd-session")
DEFAULT_AGENTCORE_GATEWAY_ID = os.getenv("AGENTCORE_GATEWAY_ID", "testgatewayfbdd062d-e2eo4q0y09")

# ---------------------------------------------------------------------------
# Lambda ARN defaults  (Local — full ARN form for direct invocation)
# ---------------------------------------------------------------------------
DEFAULT_LAMBDA_REQUIREMENTS_GATHERING_ARN = os.getenv(
    "LAMBDA_REQUIREMENTS_GATHERING_ARN",
    "arn:aws:lambda:us-east-1:448049797912:function:requirements_gathering_lambda",
)
DEFAULT_LAMBDA_BRD_FROM_HISTORY_ARN = os.getenv(
    "LAMBDA_BRD_FROM_HISTORY_ARN",
    "arn:aws:lambda:us-east-1:448049797912:function:brd_from_history_lambda",
)

# ---------------------------------------------------------------------------
# Unified BRD Agent (features/aman) — Phase 2 plumbing
# Mirrors the section in env_vdi.py. Keep the two files in sync.
# ---------------------------------------------------------------------------
BRD_USE_UNIFIED_AGENT = os.getenv("BRD_USE_UNIFIED_AGENT", "false").lower() == "true"

# In local/dev we hit Bedrock directly, so the router/handler models are
# Bedrock model IDs rather than gateway model names. The llm_gateway shim
# remaps anyway, but having distinct defaults documents intent.
BRD_ROUTER_MODEL  = os.getenv("BRD_ROUTER_MODEL",  "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BRD_HANDLER_MODEL = os.getenv("BRD_HANDLER_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

BRD_ROUTER_MAX_TOKENS    = int(os.getenv("BRD_ROUTER_MAX_TOKENS",    "400"))
BRD_ROUTER_TEMPERATURE   = float(os.getenv("BRD_ROUTER_TEMPERATURE", "0.0"))
BRD_EDIT_MAX_TOKENS      = int(os.getenv("BRD_EDIT_MAX_TOKENS",      "3000"))
BRD_SECTION_MAX_TOKENS   = int(os.getenv("BRD_SECTION_MAX_TOKENS",   "4000"))
BRD_AUDIT_MAX_TOKENS     = int(os.getenv("BRD_AUDIT_MAX_TOKENS",     "1500"))
BRD_QA_MAX_TOKENS        = int(os.getenv("BRD_QA_MAX_TOKENS",        "900"))
BRD_SUGGEST_MAX_TOKENS   = int(os.getenv("BRD_SUGGEST_MAX_TOKENS",   "900"))
BRD_GATHER_MAX_TOKENS    = int(os.getenv("BRD_GATHER_MAX_TOKENS",    "600"))

BRD_PREVIOUS_VERSIONS_CAP = int(os.getenv("BRD_PREVIOUS_VERSIONS_CAP", "5"))
BRD_SECTION_PARALLELISM   = int(os.getenv("BRD_SECTION_PARALLELISM",   "5"))

BRD_RATE_LIMIT_TURNS_PER_HOUR        = int(os.getenv("BRD_RATE_LIMIT_TURNS_PER_HOUR",        "60"))
BRD_RATE_LIMIT_GENERATIONS_PER_DAY   = int(os.getenv("BRD_RATE_LIMIT_GENERATIONS_PER_DAY",   "5"))

BRD_SSE_MAX_CONCURRENT_STREAMS = int(os.getenv("BRD_SSE_MAX_CONCURRENT_STREAMS", "3"))
BRD_SSE_HARD_TIMEOUT_SECONDS   = int(os.getenv("BRD_SSE_HARD_TIMEOUT_SECONDS",   "120"))
BRD_SSE_IDLE_TIMEOUT_SECONDS   = int(os.getenv("BRD_SSE_IDLE_TIMEOUT_SECONDS",   "30"))

BRD_AGENTCORE_ACTOR_PREFIX = os.getenv("BRD_AGENTCORE_ACTOR_PREFIX", "user-")
BRD_AGENTCORE_LEGACY_ACTOR = os.getenv("BRD_AGENTCORE_LEGACY_ACTOR", "analyst-session")

BRD_FACTS_NAMESPACE_TEMPLATE = os.getenv(
    "BRD_FACTS_NAMESPACE_TEMPLATE",
    "user-{user_id}:project-{project_id}",
)
BRD_FACTS_TOP_K = int(os.getenv("BRD_FACTS_TOP_K", "10"))

BRD_ORCHESTRATOR_LAMBDA = os.getenv("BRD_ORCHESTRATOR_LAMBDA", "brd_orchestrator_lambda")
BRD_GENERATOR_LAMBDA    = os.getenv("BRD_GENERATOR_LAMBDA",    "brd_generator_lambda")
BRD_FROM_HISTORY_LAMBDA = os.getenv("BRD_FROM_HISTORY_LAMBDA", "brd_from_history_lambda")

OTEL_EXPORTER     = os.getenv("OTEL_EXPORTER",     "console")  # local: spans to stdout
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "brd-orchestrator")


def _get_bedrock_config():
    from botocore.config import Config
    return Config(
        connect_timeout=60,
        read_timeout=300,
        retries={"max_attempts": 3, "mode": "standard"},
    )


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
    """
    Send a chat request directly to AWS Bedrock (local dev — no gateway).

    Accepts the same interface as llm_gateway.chat_completion so that the
    lambda files work unchanged when switching environments.
    """
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=_get_bedrock_config(),
    )

    model_id = model or DEFAULT_BEDROCK_MODEL
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": messages,
        "max_tokens": max_tokens or 4000,
        "temperature": temperature,
    }
    if system_prompt:
        body["system"] = system_prompt

    logger.info(f"[LOCAL LLM] Invoking Bedrock model: {model_id}")
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
    )
    result = json.loads(response["body"].read())
    content = (result.get("content", [{}])[0].get("text", "") or "").strip()

    # Record per-user token usage (fire-and-forget, same pattern as gateway).
    # Bedrock-native usage reports cache tokens SEPARATELY from input_tokens
    # (unlike the OpenAI-compat gateway where prompt_tokens already includes
    # them), so build a gateway-shaped usage_dict for _effective_tokens.
    if user_id:
        usage = result.get("usage") or {}
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        total = in_tok + cache_read + cache_write + out_tok
        if total > 0:
            try:
                import threading
                from db_helper import increment_user_token_usage
                from llm_gateway import _effective_tokens
                usage_dict = {
                    "prompt_tokens": in_tok + cache_read + cache_write,
                    "completion_tokens": out_tok,
                    "total_tokens": total,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                }
                try:
                    eff_in, eff_out = _effective_tokens(usage_dict, model_id)
                except ValueError:
                    eff_in, eff_out = 0, 0
                threading.Thread(
                    target=lambda: increment_user_token_usage(user_id, total, eff_in, eff_out),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.warning(f"[LOCAL LLM] token_usage write failed: {e}")

    if return_metadata:
        stop_reason = result.get("stop_reason")
        finish_reason = "length" if stop_reason == "max_tokens" else stop_reason
        return {"content": content, "finish_reason": finish_reason}
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
    Bedrock call with Anthropic-native tool use (local dev).
    Accepts OpenAI tool format, converts to Anthropic format for Bedrock.
    Returns dict with "message" and "finish_reason" matching the gateway interface.

    Records per-user token usage (raw + Sonnet-equivalent) same as the gateway
    path. Signature mirrors llm_gateway.chat_completion_with_tools so callers
    can swap between gateway / LOCAL via environment.py without arg juggling.
    """
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=_get_bedrock_config(),
    )
    model_id = model or DEFAULT_BEDROCK_MODEL

    # Convert OpenAI tool format → Anthropic tool format
    anthropic_tools = []
    for t in tools:
        fn = t.get("function", t)
        anthropic_tools.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })

    # Separate system messages from user/assistant messages
    system_parts = []
    chat_messages = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            chat_messages.append(m)

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": chat_messages,
        "tools": anthropic_tools,
        "max_tokens": max_tokens or 4000,
        "temperature": temperature,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)

    logger.info(f"[LOCAL LLM] Tool call → Bedrock model: {model_id}")
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
    )
    result = json.loads(response["body"].read())

    # Record per-user token usage (fire-and-forget). Bedrock-native usage
    # reports cache fields separately from input_tokens — re-shape to the
    # gateway convention for _effective_tokens.
    if user_id:
        usage = result.get("usage") or {}
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        total = in_tok + cache_read + cache_write + out_tok
        if total > 0:
            try:
                import threading
                from db_helper import increment_user_token_usage
                from llm_gateway import _effective_tokens
                usage_dict = {
                    "prompt_tokens": in_tok + cache_read + cache_write,
                    "completion_tokens": out_tok,
                    "total_tokens": total,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                }
                try:
                    eff_in, eff_out = _effective_tokens(usage_dict, model_id)
                except ValueError:
                    eff_in, eff_out = 0, 0
                threading.Thread(
                    target=lambda: increment_user_token_usage(user_id, total, eff_in, eff_out),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.warning(f"[LOCAL LLM Tool] token_usage write failed: {e}")

    # Convert Anthropic response to OpenAI-compatible structure
    stop_reason = result.get("stop_reason", "end_turn")
    content_blocks = result.get("content", [])

    text_parts = []
    tool_calls = []
    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append(type("ToolCall", (), {
                "id": block["id"],
                "function": type("Function", (), {
                    "name": block["name"],
                    "arguments": json.dumps(block["input"]),
                })(),
                "type": "function",
            })())

    # Build a message-like object matching OpenAI SDK structure
    msg = type("Message", (), {
        "content": "\n".join(text_parts) if text_parts else None,
        "tool_calls": tool_calls if tool_calls else None,
        "role": "assistant",
    })()

    finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"
    return {"message": msg, "finish_reason": finish_reason}


def chat_completion_stream(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.5,
    max_tokens: Optional[int] = None,
    system_prompt: Optional[str] = None,
    user_id: Optional[str] = None,
    token_source: Optional[str] = None,
):
    """
    Stream a chat request directly to AWS Bedrock (local dev — no gateway).
    Yields SSE-formatted data strings: { type: 'chunk', text: '...' } and a final { type: 'done' }.

    Captures token usage from Bedrock's streaming event protocol:
      - `message_start.message.usage` → input + cache fields
      - `message_delta.usage`         → output_tokens (final)
    Records raw + Sonnet-equivalent same as the non-stream path.
    """
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=_get_bedrock_config(),
    )

    model_id = model or DEFAULT_BEDROCK_MODEL
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": messages,
        "max_tokens": max_tokens or 8192,
        "temperature": temperature,
    }
    if system_prompt:
        body["system"] = system_prompt

    logger.info(f"[LOCAL LLM STREAM] Invoking Bedrock model: {model_id}")
    response = client.invoke_model_with_response_stream(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    # Accumulate usage across the stream's two emit points.
    input_tokens = 0
    cache_read = 0
    cache_write = 0
    output_tokens = 0

    for event in response["body"]:
        chunk = json.loads(event["chunk"]["bytes"])
        etype = chunk.get("type")
        if etype == "message_start":
            u = (chunk.get("message") or {}).get("usage") or {}
            input_tokens = u.get("input_tokens", 0) or 0
            cache_read = u.get("cache_read_input_tokens", 0) or 0
            cache_write = u.get("cache_creation_input_tokens", 0) or 0
        elif etype == "content_block_delta":
            text = chunk.get("delta", {}).get("text", "")
            if text:
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
        elif etype == "message_delta":
            u = chunk.get("usage") or {}
            if u:
                output_tokens = u.get("output_tokens", output_tokens) or output_tokens

    # Record after the stream ends.
    if user_id:
        total = input_tokens + cache_read + cache_write + output_tokens
        if total > 0:
            try:
                import threading
                from db_helper import increment_user_token_usage
                from llm_gateway import _effective_tokens
                usage_dict = {
                    "prompt_tokens": input_tokens + cache_read + cache_write,
                    "completion_tokens": output_tokens,
                    "total_tokens": total,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                }
                try:
                    eff_in, eff_out = _effective_tokens(usage_dict, model_id)
                except ValueError:
                    eff_in, eff_out = 0, 0
                threading.Thread(
                    target=lambda: increment_user_token_usage(user_id, total, eff_in, eff_out),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.warning(f"[LOCAL LLM STREAM] token_usage write failed: {e}")

    yield f"data: {json.dumps({'type': 'done'})}\n\n"
