"""
Centralized S3 Service - All S3 write operations go through here.
"""

import os
import logging
import boto3

logger = logging.getLogger(__name__)

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

_s3_client = None


def get_s3_client():
    """Get or create a cached S3 client."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def s3_put_object(key: str, body, content_type: str = "application/octet-stream", bucket: str = None):
    """
    Upload an object to S3 with KMS encryption.

    Args:
        key: S3 object key (path)
        body: File content (bytes or str — str will be encoded to UTF-8)
        content_type: MIME type of the content
        bucket: S3 bucket name (defaults to S3_BUCKET_NAME env var)
    """
    if bucket is None:
        bucket = S3_BUCKET_NAME

    if isinstance(body, str):
        body = body.encode("utf-8")

    client = get_s3_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    logger.info(f"[S3] Uploaded s3://{bucket}/{key} ({len(body)} bytes)")


def s3_get_object(key: str, bucket: str = None):
    """
    Download an object from S3.

    Args:
        key: S3 object key (path)
        bucket: S3 bucket name (defaults to S3_BUCKET_NAME env var)

    Returns:
        The S3 GetObject response dict
    """
    if bucket is None:
        bucket = S3_BUCKET_NAME

    client = get_s3_client()
    return client.get_object(Bucket=bucket, Key=key)
