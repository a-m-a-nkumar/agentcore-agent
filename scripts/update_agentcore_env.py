#!/usr/bin/env python3
"""
Update AgentCore runtime environment variables from .env file.
Sets BEDROCK_GUARDRAIL_ARN and BEDROCK_GUARDRAIL_VERSION so agents use guardrails when calling Bedrock.
Run from project root: python scripts/update_agentcore_env.py
"""
import os
import re
import sys

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

# Agent runtime IDs (fallback)
AGENT_RUNTIME_IDS = [
    "my_agent-0BLwDgF9uK",
    "Analyst_agent-kCoE8v38c0",
]

# Env vars to set for guardrail support when agent calls Bedrock
AGENT_ENV_KEYS = [
    "BEDROCK_GUARDRAIL_ARN",
    "BEDROCK_GUARDRAIL_VERSION",
]

REGION = os.getenv("AWS_REGION", "us-east-1")


def get_agent_runtime_ids(project_root: str) -> list:
    """Get agent runtime IDs from .bedrock_agentcore.yaml or .env."""
    yaml_path = os.path.join(project_root, ".bedrock_agentcore.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path) as f:
            content = f.read()
        # Match agent_id: value
        ids = re.findall(r"agent_id:\s*([A-Za-z0-9_-]+)", content)
        if ids:
            return ids
    # Fallback: parse from .env ARNs
    env_path = os.path.join(project_root, ".env")
    if os.path.exists(env_path):
        env = dotenv_values(env_path)
        ids = []
        for key in ["AGENT_ARN", "ANALYST_AGENT_ARN"]:
            arn = env.get(key)
            if arn and "runtime/" in arn:
                ids.append(arn.split("runtime/")[-1].strip())
        if ids:
            return ids
    return AGENT_RUNTIME_IDS


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env")

    if not os.path.exists(env_path):
        print(f"Error: .env not found at {env_path}")
        sys.exit(1)

    env_vars = dotenv_values(env_path)
    env_vars = {k: v for k, v in env_vars.items() if v is not None and str(v).strip() != ""}

    # Build env vars to set for agents
    agent_env = {}
    for key in AGENT_ENV_KEYS:
        if key in env_vars:
            agent_env[key] = str(env_vars[key])

    if not agent_env:
        print("No BEDROCK_GUARDRAIL_ARN or BEDROCK_GUARDRAIL_VERSION in .env - skipping agent env update")
        return

    runtime_ids = get_agent_runtime_ids(project_root)
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)

    for runtime_id in runtime_ids:
        try:
            # Get current config
            resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
            role_arn = resp["roleArn"]
            network_config = resp["networkConfiguration"]
            artifact = resp["agentRuntimeArtifact"]

            # Merge env vars: keep existing, add/override from .env
            current_env = resp.get("environmentVariables") or {}
            new_env = dict(current_env)
            new_env.update(agent_env)

            client.update_agent_runtime(
                agentRuntimeId=runtime_id,
                agentRuntimeArtifact=artifact,
                roleArn=role_arn,
                networkConfiguration=network_config,
                environmentVariables=new_env,
            )
            print(f"  [OK] {runtime_id} - updated env vars: {list(agent_env.keys())}")
        except client.exceptions.ResourceNotFoundException:
            print(f"  [SKIP] {runtime_id} - runtime not found")
        except Exception as e:
            print(f"  [ERROR] {runtime_id} - {e}")

    print("\nDone. AgentCore runtime environment variables updated from .env")


if __name__ == "__main__":
    main()
