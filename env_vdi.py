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
from llm_gateway import chat_completion, chat_completion_with_tools  # noqa: F401

import json
import logging as _logging

_logger = _logging.getLogger(__name__)


from llm_gateway import chat_completion_stream as _llm_chat_completion_stream  # noqa: F401


def chat_completion_stream(messages, model=None, temperature=0.5, max_tokens=None,
                           system_prompt=None, user_id=None, token_source=None):
    """
    VDI streaming — delegates to the real SSE-streaming implementation in
    llm_gateway.chat_completion_stream. Yields SSE-formatted strings ready
    for StreamingResponse to relay to the browser, plus records token usage
    once the stream completes (via the standard _record_tokens_async path).

    Callers pass Bedrock model IDs (e.g. "global.anthropic.claude-…") which
    the DLX AI gateway doesn't recognise — remap to the gateway model first.
    """
    if model and ("anthropic" in model or "bedrock" in model or "amazon" in model):
        _logger.info(
            f"[VDI LLM STREAM] Remapping Bedrock model '{model}' → '{DEFAULT_GATEWAY_MODEL}'"
        )
        model = DEFAULT_GATEWAY_MODEL
    yield from _llm_chat_completion_stream(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        user_id=user_id,
        token_source=token_source,
    )

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

# ---------------------------------------------------------------------------
# 9. Unified BRD Agent (features/aman) — Phase 2 plumbing
# ---------------------------------------------------------------------------
# Feature flag: when false, the existing PM-agent / analyst-agent paths
# stay live and the new orchestrator is dark-launched. Flip per-environment
# once the new path is verified against the golden-set.
BRD_USE_UNIFIED_AGENT = os.getenv("BRD_USE_UNIFIED_AGENT", "false").lower() == "true"

# Model selection. Default to Sonnet for safety; if the gateway proves it
# supports Haiku via the golden-set (>= 95% intent accuracy) we flip the
# router env var to "Claude-Haiku-4.5" without touching code.
BRD_ROUTER_MODEL  = os.getenv("BRD_ROUTER_MODEL",  "Claude-4.5-Sonnet")
BRD_HANDLER_MODEL = os.getenv("BRD_HANDLER_MODEL", "Claude-4.5-Sonnet")

# Router & handler tuning knobs (mirror SAD_*_MAX_TOKENS pattern).
BRD_ROUTER_MAX_TOKENS    = int(os.getenv("BRD_ROUTER_MAX_TOKENS",    "400"))
BRD_ROUTER_TEMPERATURE   = float(os.getenv("BRD_ROUTER_TEMPERATURE", "0.0"))
BRD_EDIT_MAX_TOKENS      = int(os.getenv("BRD_EDIT_MAX_TOKENS",      "3000"))
BRD_SECTION_MAX_TOKENS   = int(os.getenv("BRD_SECTION_MAX_TOKENS",   "4000"))
BRD_AUDIT_MAX_TOKENS     = int(os.getenv("BRD_AUDIT_MAX_TOKENS",     "1500"))
BRD_QA_MAX_TOKENS        = int(os.getenv("BRD_QA_MAX_TOKENS",        "900"))
BRD_SUGGEST_MAX_TOKENS   = int(os.getenv("BRD_SUGGEST_MAX_TOKENS",   "900"))
BRD_GATHER_MAX_TOKENS    = int(os.getenv("BRD_GATHER_MAX_TOKENS",    "600"))

# Per-section revert stack depth (mirrors SAD's previous_versions cap=5).
BRD_PREVIOUS_VERSIONS_CAP = int(os.getenv("BRD_PREVIOUS_VERSIONS_CAP", "5"))

# Parallel section generation -- port of SAD's ThreadPoolExecutor pattern
# (lambda_sad_orchestrator.py:1526) into lambda_brd_generator. 5 workers
# is the same default SAD uses.
BRD_SECTION_PARALLELISM = int(os.getenv("BRD_SECTION_PARALLELISM", "5"))

# Per-user rate limits (FastAPI middleware enforces).
BRD_RATE_LIMIT_TURNS_PER_HOUR        = int(os.getenv("BRD_RATE_LIMIT_TURNS_PER_HOUR",        "60"))
BRD_RATE_LIMIT_GENERATIONS_PER_DAY   = int(os.getenv("BRD_RATE_LIMIT_GENERATIONS_PER_DAY",   "5"))

# SSE limits for the /turn-stream and /generate-stream endpoints.
BRD_SSE_MAX_CONCURRENT_STREAMS = int(os.getenv("BRD_SSE_MAX_CONCURRENT_STREAMS", "3"))
BRD_SSE_HARD_TIMEOUT_SECONDS   = int(os.getenv("BRD_SSE_HARD_TIMEOUT_SECONDS",   "120"))
BRD_SSE_IDLE_TIMEOUT_SECONDS   = int(os.getenv("BRD_SSE_IDLE_TIMEOUT_SECONDS",   "30"))

# AgentCore Memory -- dual-actor pattern. Writes use the per-user actor;
# reads merge results from BOTH the per-user actor AND the legacy shared
# one so historical chats remain accessible without a migration.
BRD_AGENTCORE_ACTOR_PREFIX = os.getenv("BRD_AGENTCORE_ACTOR_PREFIX", "user-")
BRD_AGENTCORE_LEGACY_ACTOR = os.getenv("BRD_AGENTCORE_LEGACY_ACTOR", "analyst-session")

# Long-term memory namespace template. Per-(user, project) so a user's
# facts about project A don't leak into their work on project B.
BRD_FACTS_NAMESPACE_TEMPLATE = os.getenv(
    "BRD_FACTS_NAMESPACE_TEMPLATE",
    "user-{user_id}:project-{project_id}",
)
BRD_FACTS_TOP_K = int(os.getenv("BRD_FACTS_TOP_K", "10"))

# Lambda function names for the new orchestrator + (kept) workers.
BRD_ORCHESTRATOR_LAMBDA = os.getenv("BRD_ORCHESTRATOR_LAMBDA", "sdlc-dev-brd-orchestrator")
BRD_GENERATOR_LAMBDA    = os.getenv("BRD_GENERATOR_LAMBDA",    "sdlc-dev-brd-generator")
BRD_FROM_HISTORY_LAMBDA = os.getenv("BRD_FROM_HISTORY_LAMBDA", "sdlc-dev-brd-from-history")

# Observability -- ADOT layer exporter. CloudWatch is the default.
OTEL_EXPORTER     = os.getenv("OTEL_EXPORTER",     "cloudwatch")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "brd-orchestrator")
