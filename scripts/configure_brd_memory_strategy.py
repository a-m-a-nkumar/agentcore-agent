"""
One-time setup: register the BRD `SEMANTIC` long-term memory strategy
on the existing AgentCore Memory store.

Runs the bedrock-agentcore PutMemoryStrategy (or equivalent) API call
that tells AgentCore Memory:
  • The new strategy's name is `brd_facts_extraction`.
  • Its kind is SEMANTIC (extracts JSON facts).
  • Its extraction prompt is the BRD-tailored override defined in
    `prompts/brd_facts_extraction_prompt.py` — keeps default extraction
    from polluting long-term memory with chitchat.
  • Strategy applies to events under per-user actors (`user-*`).

After this runs ONCE per memory store:
  • Every event the orchestrator writes via create_event under
    `actor_id = f"user-{user_id}"` will be queued for extraction.
  • Extraction completes in ~20–40 seconds asynchronously.
  • Long-term records are stored under namespace
    `BRD_FACTS_NAMESPACE_TEMPLATE` (default `user-{user_id}:project-{project_id}`)
    and retrievable via `retrieve_memory_records`.

Idempotent: if the strategy already exists with the same name and
kind, this script no-ops with an informational log line. To FORCE an
update (e.g. after editing the override prompt), pass `--update`.

Usage:
    python scripts/configure_brd_memory_strategy.py            # idempotent register
    python scripts/configure_brd_memory_strategy.py --update   # update existing
    python scripts/configure_brd_memory_strategy.py --dry-run  # print payload only

Env vars consumed:
    AGENTCORE_MEMORY_ID  — memory store ID (must exist already).
    AWS_REGION           — defaults to us-east-1.

Sharp edges:
  • The PutMemoryStrategy / UpdateMemoryStrategy APIs are documented at
    https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-memory.html
    BUT the exact parameter names have changed between SDK versions.
    This script uses the canonical shape; adjust per your boto3 version
    if it returns "unknown parameter" errors.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from prompts.brd_facts_extraction_prompt import (  # noqa: E402
    BRD_FACTS_EXTRACTION_PROMPT,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("configure_brd_memory_strategy")

STRATEGY_NAME = "brd_facts_extraction"
STRATEGY_KIND = "SEMANTIC"


def _client():
    return boto3.client(
        "bedrock-agentcore",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _build_strategy_payload(memory_id: str) -> dict:
    """Build the request payload for put / update strategy."""
    return {
        "memoryId": memory_id,
        "strategyName": STRATEGY_NAME,
        "strategyType": STRATEGY_KIND,
        # Restrict extraction to per-user actors so the legacy
        # "analyst-session" shared-actor events don't end up extracted
        # into the per-user namespaces (those reads happen via the
        # dual-actor merge path, not via long-term retrieval).
        "actorPattern": "user-*",
        "extractionConfig": {
            "promptOverride": BRD_FACTS_EXTRACTION_PROMPT,
        },
    }


def _try_describe(client, memory_id: str) -> dict | None:
    """Return the existing strategy by name, or None."""
    try:
        resp = client.get_memory_strategy(
            memoryId=memory_id,
            strategyName=STRATEGY_NAME,
        )
        return resp
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("ResourceNotFoundException", "NotFoundException"):
            return None
        raise
    except Exception:
        # Some SDK versions expose this as list+filter instead.
        try:
            resp = client.list_memory_strategies(memoryId=memory_id)
            for s in resp.get("strategies", []) or []:
                if s.get("name") == STRATEGY_NAME:
                    return s
            return None
        except Exception:
            return None


def run(*, dry_run: bool = False, force_update: bool = False) -> None:
    memory_id = os.getenv("AGENTCORE_MEMORY_ID")
    if not memory_id:
        raise SystemExit("AGENTCORE_MEMORY_ID env var must be set.")

    payload = _build_strategy_payload(memory_id)

    if dry_run:
        logger.info("DRY RUN — would send payload:")
        # Truncate the long extraction prompt for log readability.
        log_payload = dict(payload)
        log_payload["extractionConfig"] = {
            "promptOverride": (BRD_FACTS_EXTRACTION_PROMPT[:200] + " ... (truncated)"),
        }
        print(json.dumps(log_payload, indent=2))
        return

    client = _client()
    existing = _try_describe(client, memory_id)

    if existing and not force_update:
        logger.info(
            f"Strategy '{STRATEGY_NAME}' already exists on memory '{memory_id}'. "
            f"Pass --update to force overwrite."
        )
        return

    try:
        if existing:
            logger.info(f"Updating existing strategy '{STRATEGY_NAME}' ...")
            # boto3 method shape varies; try the most likely names.
            for method_name in ("update_memory_strategy", "modify_memory_strategy"):
                if hasattr(client, method_name):
                    getattr(client, method_name)(**payload)
                    break
            else:
                raise RuntimeError(
                    "No update_memory_strategy method on bedrock-agentcore client. "
                    "Upgrade boto3 or fall back to put_memory_strategy."
                )
        else:
            logger.info(f"Creating strategy '{STRATEGY_NAME}' on memory '{memory_id}' ...")
            for method_name in ("put_memory_strategy", "create_memory_strategy"):
                if hasattr(client, method_name):
                    getattr(client, method_name)(**payload)
                    break
            else:
                raise RuntimeError(
                    "No put_memory_strategy / create_memory_strategy method available. "
                    "Upgrade boto3 — this API was added in recent versions."
                )
        logger.info(f"Strategy '{STRATEGY_NAME}' registered. Extraction begins on next "
                    "create_event write under a `user-*` actor. Lag: ~20–40s.")
    except ClientError as e:
        logger.error(f"Strategy registration failed: {e}")
        raise


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Register the BRD SEMANTIC long-term memory strategy "
            "(one-time setup per memory store)."
        )
    )
    p.add_argument("--dry-run", action="store_true", help="Print payload only.")
    p.add_argument(
        "--update",
        action="store_true",
        help="If the strategy already exists, overwrite it.",
    )
    args = p.parse_args()
    run(dry_run=args.dry_run, force_update=args.update)


if __name__ == "__main__":
    main()
