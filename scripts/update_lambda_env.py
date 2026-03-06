#!/usr/bin/env python3
"""
Update Lambda environment variables from .env file.
Run from project root: python scripts/update_lambda_env.py
"""
import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import dotenv_values
except ImportError:
    print("Error: python-dotenv required. Run: pip install python-dotenv")
    sys.exit(1)

try:
    import boto3
except ImportError:
    print("Error: boto3 required. Run: pip install boto3")
    sys.exit(1)

# Lambda functions and their required env vars
# NOTE: AWS_REGION is reserved by Lambda - do not include it (Lambda provides it automatically)
LAMBDA_CONFIG = {
    "brd_chat_lambda": [
        "BEDROCK_MODEL_ID", "BEDROCK_REGION", "BEDROCK_MAX_TOKENS", "BEDROCK_TEMPERATURE",
        "BEDROCK_GUARDRAIL_ARN", "BEDROCK_GUARDRAIL_VERSION",
        "S3_BUCKET_NAME", "AGENTCORE_GATEWAY_ID", "AGENTCORE_MEMORY_ID", "AGENTCORE_ACTOR_ID",
        "BRD_STRUCTURE_MAX_CHARS",
    ],
    "brd_generator_lambda": [
        "BEDROCK_MODEL_ID", "BEDROCK_REGION", "BEDROCK_MAX_TOKENS", "BEDROCK_TEMPERATURE",
        "BEDROCK_GUARDRAIL_ARN", "BEDROCK_GUARDRAIL_VERSION",
        "S3_BUCKET_NAME",
    ],
    "brd_from_history_lambda": [
        "BEDROCK_MODEL_ID", "BEDROCK_GUARDRAIL_ARN", "BEDROCK_GUARDRAIL_VERSION",
        "S3_BUCKET_NAME", "AGENTCORE_MEMORY_ID", "AGENTCORE_ACTOR_ID",
    ],
    "requirements_gathering_lambda": [
        "BEDROCK_MODEL_ID", "BEDROCK_GUARDRAIL_ARN", "BEDROCK_GUARDRAIL_VERSION",
        "AGENTCORE_MEMORY_ID", "AGENTCORE_ACTOR_ID",
    ],
    "brd_retriever_lambda": [
        "S3_BUCKET_NAME",
    ],
}

REGION = os.getenv("AWS_REGION", "us-east-1")


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env")

    if not os.path.exists(env_path):
        print(f"Error: .env not found at {env_path}")
        sys.exit(1)

    env_vars = dotenv_values(env_path)
    env_vars = {k: v for k, v in env_vars.items() if v is not None and str(v).strip() != ""}

    # Note: AWS_REGION is reserved by Lambda - Lambda provides it automatically. Do not set it.

    client = boto3.client("lambda", region_name=REGION)

    for func_name, required_keys in LAMBDA_CONFIG.items():
        try:
            # Get current config
            resp = client.get_function_configuration(FunctionName=func_name)
            current_vars = resp.get("Environment", {}).get("Variables", {}) or {}

            # Merge: keep existing, add/override from .env
            new_vars = dict(current_vars)
            for key in required_keys:
                if key in env_vars:
                    new_vars[key] = str(env_vars[key])

            client.update_function_configuration(
                FunctionName=func_name,
                Environment={"Variables": new_vars},
            )
            print(f"  [OK] {func_name} - updated {len([k for k in required_keys if k in env_vars])} env vars")
        except client.exceptions.ResourceNotFoundException:
            print(f"  [SKIP] {func_name} - function not found")
        except Exception as e:
            print(f"  [ERROR] {func_name} - {e}")

    print("\nDone. Lambda environment variables updated from .env")


if __name__ == "__main__":
    main()
