"""
VDI ENVIRONMENT CONFIGURATION
================================
Use this file when running the project on the VDI / client environment.

In environment.py, make sure this line is UNcommented:
    from env_vdi import *

VDI-specific features enabled here:
  1. KMS             — all S3 uploads are SSE-KMS encrypted (services/s3_service.py)
  2. Security Manager — DB credentials fetched from AWS Secrets Manager (db_config.py)
  3. API Gateway      — LLM calls routed through the Deluxe DLX AI proxy (llm_gateway.py)
  4. AWS Bedrock ARNs — points to the VDI AWS account (590184044598)
  5. Agent Model      — Strands agents use the Deluxe gateway (OpenAIModel)
  6. Lambda ARNs      — VDI account Lambda function ARNs (590184044598)
"""

import os

# ---------------------------------------------------------------------------
# 1. KMS — S3 with SSE-KMS encryption
# ---------------------------------------------------------------------------
# services/s3_service.py enforces SSE-KMS on every upload.
from services.s3_service import s3_put_object, get_s3_client  # noqa: F401

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")

# ---------------------------------------------------------------------------
# 2. Security Manager — DB credentials via AWS Secrets Manager
# ---------------------------------------------------------------------------
# db_config.py caches the secret and refreshes every 30 minutes.
from db_config import get_db_params  # noqa: F401

# ---------------------------------------------------------------------------
# 3. API Gateway — LLM calls via Deluxe DLX AI proxy
# ---------------------------------------------------------------------------
# llm_gateway.py wraps the OpenAI-compatible Deluxe endpoint.
from llm_gateway import chat_completion  # noqa: F401

import json
import logging as _logging

_logger = _logging.getLogger(__name__)


def chat_completion_stream(messages, model=None, temperature=0.5, max_tokens=None, system_prompt=None):
    """
    VDI streaming shim — llm_gateway does not support SSE streaming yet.
    Falls back to a single blocking call and yields the response as one chunk + done.
    """
    if system_prompt:
        messages = [{"role": "system", "content": system_prompt}] + list(messages)
    # Callers pass Bedrock model IDs (e.g. "global.anthropic.claude-…") which the
    # DLX AI gateway doesn't recognise.  Remap to the gateway model name.
    if model and ("anthropic" in model or "bedrock" in model or "amazon" in model):
        _logger.info(f"[VDI LLM STREAM] Remapping Bedrock model '{model}' → '{DEFAULT_GATEWAY_MODEL}'")
        model = DEFAULT_GATEWAY_MODEL
    _logger.info("[VDI LLM STREAM] Falling back to non-streaming chat_completion")
    result = chat_completion(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens)
    yield f"data: {json.dumps({'type': 'chunk', 'text': result})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"

# ---------------------------------------------------------------------------
# 4. AWS Bedrock ARNs  (VDI AWS account: 590184044598)
# ---------------------------------------------------------------------------
DEFAULT_AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:590184044598:runtime/pm_agent-uDlkiNFagv"
DEFAULT_ANALYST_AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:590184044598:runtime/analyst_agent-JAa3wMFKOK"

# ---------------------------------------------------------------------------
# 5. Agent Model — Strands agents use Deluxe gateway (VDI)
# ---------------------------------------------------------------------------
AGENT_MODEL_PROVIDER = "gateway"
DEFAULT_DLXAI_GATEWAY_URL = "https://dlxai-dev.deluxe.com/proxy"
DEFAULT_DLXAI_GATEWAY_KEY = "sk-2cdb551cf35f418ea88b36"
DEFAULT_GATEWAY_MODEL = "Claude-4.5-Sonnet"

# ---------------------------------------------------------------------------
# Embedding config  (VDI: gateway Titan-v2 at 1024 dimensions)
# ---------------------------------------------------------------------------
EMBEDDING_DIMENSIONS = 1024
BEDROCK_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

# ---------------------------------------------------------------------------
# 6. Lambda ARN defaults  (VDI AWS account: 590184044598)
# ---------------------------------------------------------------------------
DEFAULT_LAMBDA_BRD_GENERATOR           = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-generator"
DEFAULT_LAMBDA_BRD_RETRIEVER           = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-retriever"
DEFAULT_LAMBDA_BRD_CHAT                = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-chat"
DEFAULT_LAMBDA_REQUIREMENTS_GATHERING  = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-requirements-gathering"
DEFAULT_LAMBDA_BRD_FROM_HISTORY        = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-from-history"

# ---------------------------------------------------------------------------
# 7. AgentCore defaults  (VDI)
# ---------------------------------------------------------------------------
DEFAULT_AGENTCORE_MEMORY_ID  = os.getenv("AGENTCORE_MEMORY_ID", "sdlc_dev_agentcore_memory-VF74Yf64ZB")
DEFAULT_AGENTCORE_ACTOR_ID   = os.getenv("AGENTCORE_ACTOR_ID", "analyst-session")
DEFAULT_AGENTCORE_GATEWAY_ID = os.getenv("AGENTCORE_GATEWAY_ID", "")

# ---------------------------------------------------------------------------
# 8. Lambda ARN defaults  (VDI — full ARN form for direct invocation)
# ---------------------------------------------------------------------------
DEFAULT_LAMBDA_REQUIREMENTS_GATHERING_ARN = os.getenv(
    "LAMBDA_REQUIREMENTS_GATHERING_ARN",
    "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-requirements-gathering",
)
DEFAULT_LAMBDA_BRD_FROM_HISTORY_ARN = os.getenv(
    "LAMBDA_BRD_FROM_HISTORY_ARN",
    "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-from-history",
)
