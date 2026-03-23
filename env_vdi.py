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
# 6. Lambda ARN defaults  (VDI AWS account: 590184044598)
# ---------------------------------------------------------------------------
DEFAULT_LAMBDA_BRD_GENERATOR           = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-generator"
DEFAULT_LAMBDA_BRD_RETRIEVER           = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-retriever"
DEFAULT_LAMBDA_BRD_CHAT                = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-chat"
DEFAULT_LAMBDA_REQUIREMENTS_GATHERING  = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-requirements-gathering"
DEFAULT_LAMBDA_BRD_FROM_HISTORY        = "arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-from-history"
