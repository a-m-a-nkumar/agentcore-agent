"""
Centralized S3 Service - All S3 write operations go through here.
Enforces SSE-KMS encryption on every upload as required by the bucket policy.
"""

import os
import logging
import boto3

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
KMS_KEY_ARN = os.getenv("KMS_KEY_ARN", "")

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
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=KMS_KEY_ARN,
    )
    logger.info(f"[S3] Uploaded s3://{bucket}/{key} ({len(body)} bytes, KMS encrypted)")


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
