"""
Migration: One-time S3 backfill of `previous_versions: []` arrays into
every section of every existing `brds/*/brd_structure.json`.

Why: the unified BRD orchestrator pushes the old content of a section
onto `section["previous_versions"]` before overwriting it (edit /
regenerate / save handlers). For brand-new BRDs the array starts empty
and grows from there. For BRDs created BEFORE this migration the array
key is absent, so the first edit would `.append()` to a missing key
and crash.

This migration walks every object under `s3://{S3_BUCKET_NAME}/brds/`,
loads each `brd_structure.json`, ensures each section has a
`previous_versions: []` array (if absent), and writes it back via the
project's SSE-KMS-enforcing s3_put_object helper.

Idempotent: re-running is safe. Sections that already have a
`previous_versions` list (even non-empty) are left untouched.

Usage:
    python migrations/add_brd_structure_previous_versions.py [--dry-run]

The `--dry-run` flag prints the planned mutations without writing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Tuple

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate.previous_versions")


def _get_s3_client():
    """Return a boto3 S3 client. We don't import services.s3_service.get_s3_client
    here to keep this migration runnable from a bare CI environment where
    the full backend package may not be importable."""
    return boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _list_structure_keys(s3, bucket: str) -> List[str]:
    """Yield all keys matching brds/*/brd_structure.json under the bucket."""
    paginator = s3.get_paginator("list_objects_v2")
    keys: List[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix="brds/"):
        for obj in page.get("Contents", []) or []:
            key = obj.get("Key", "")
            # Match brds/<brd_id>/brd_structure.json (no nested paths
            # beyond the single brd_id segment).
            parts = key.split("/")
            if len(parts) == 3 and parts[0] == "brds" and parts[2] == "brd_structure.json":
                keys.append(key)
    return keys


def _patch_structure(structure: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Add `previous_versions: []` to any section missing it.

    Returns (patched, count_added). `patched` may be the same object
    as `structure` (in-place); callers should not rely on identity.
    """
    sections = structure.get("sections")
    if not isinstance(sections, list):
        return structure, 0

    added = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        if "previous_versions" not in section:
            section["previous_versions"] = []
            added += 1
    return structure, added


def _write_structure(s3, bucket: str, key: str, structure: Dict[str, Any]) -> None:
    """Write the patched structure back with SSE-KMS encryption.

    We require BRD_KMS_KEY_ID to be set (matches what the production
    bucket policy requires; non-KMS writes are denied by the policy).
    """
    kms_key_id = os.getenv("BRD_KMS_KEY_ID") or os.getenv("KMS_KEY_ID")
    if not kms_key_id:
        raise RuntimeError(
            "BRD_KMS_KEY_ID (or KMS_KEY_ID) env var must be set — "
            "the BRD bucket policy denies non-KMS writes."
        )

    body = json.dumps(structure, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key_id,
    )


def run(*, dry_run: bool = False) -> None:
    bucket = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
    s3 = _get_s3_client()

    logger.info(f"Scanning s3://{bucket}/brds/ for brd_structure.json keys ...")
    keys = _list_structure_keys(s3, bucket)
    logger.info(f"Found {len(keys)} BRD structure files.")

    total_objects_patched = 0
    total_sections_patched = 0
    total_objects_already_ok = 0
    total_errors = 0

    for key in keys:
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read().decode("utf-8")
            structure = json.loads(body)
        except (ClientError, json.JSONDecodeError) as e:
            logger.warning(f"[SKIP] {key}: could not read/parse ({e})")
            total_errors += 1
            continue

        _, added = _patch_structure(structure)
        if added == 0:
            total_objects_already_ok += 1
            continue

        total_objects_patched += 1
        total_sections_patched += added
        logger.info(
            f"[{'DRY' if dry_run else 'WRITE'}] {key}: "
            f"added previous_versions=[] to {added} section(s)"
        )

        if not dry_run:
            try:
                _write_structure(s3, bucket, key, structure)
            except Exception as e:
                logger.error(f"[FAIL] {key}: write failed ({e})")
                total_errors += 1

    logger.info(
        f"Migration summary: "
        f"objects_patched={total_objects_patched} "
        f"sections_patched={total_sections_patched} "
        f"already_ok={total_objects_already_ok} "
        f"errors={total_errors} "
        f"dry_run={dry_run}"
    )


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "One-time S3 backfill: add previous_versions:[] to every "
            "section of every existing BRD structure file."
        )
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned mutations without writing.",
    )
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
