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
from typing import Dict, List, Optional

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
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)


# ---------------------------------------------------------------------------
# Agent Model — Strands agents use Bedrock directly (Local)
# ---------------------------------------------------------------------------
AGENT_MODEL_PROVIDER = "bedrock"
DEFAULT_DLXAI_GATEWAY_URL = ""   # Not used locally
DEFAULT_DLXAI_GATEWAY_KEY = ""   # Not used locally
DEFAULT_GATEWAY_MODEL = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# ---------------------------------------------------------------------------
# Lambda name defaults  (Local AWS account: 448049797912)
# ---------------------------------------------------------------------------
DEFAULT_LAMBDA_BRD_GENERATOR           = "brd_generator_lambda"
DEFAULT_LAMBDA_BRD_RETRIEVER           = "brd_retriever_lambda"
DEFAULT_LAMBDA_BRD_CHAT                = "brd_chat_lambda"
DEFAULT_LAMBDA_REQUIREMENTS_GATHERING  = "requirements_gathering_lambda"
DEFAULT_LAMBDA_BRD_FROM_HISTORY        = "brd_from_history_lambda"


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.9,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Send a chat request directly to AWS Bedrock (local dev — no gateway).

    Accepts the same interface as llm_gateway.chat_completion so that the
    lambda files work unchanged when switching environments.
    """
    from botocore.config import Config

    bedrock_config = Config(
        connect_timeout=60,
        read_timeout=300,
        retries={"max_attempts": 3, "mode": "standard"},
    )
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=bedrock_config,
    )

    model_id = model or DEFAULT_BEDROCK_MODEL
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": messages,
        "max_tokens": max_tokens or 4000,
        "temperature": temperature,
    }

    logger.info(f"[LOCAL LLM] Invoking Bedrock model: {model_id}")
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
    )
    result = json.loads(response["body"].read())
    return (result.get("content", [{}])[0].get("text", "") or "").strip()
