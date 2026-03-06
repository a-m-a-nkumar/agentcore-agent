"""
Centralized Database Configuration with Secrets Manager + IAM Auth Support
==========================================================================
This module provides database connection parameters for all DB-accessing files.

Authentication priority:
  1. Static password from DATABASE_PASSWORD env var (local dev)
  2. Password from AWS Secrets Manager (production - as recommended by DevOps)
  3. IAM Auth token via boto3 generate_db_auth_token (fallback)

Usage:
  from db_config import get_db_params, get_direct_connection
  params = get_db_params()  # Returns dict with host, port, database, user, password
  conn = get_direct_connection()  # Returns a psycopg2 connection
"""

import os
import logging
import time
import json
import threading

logger = logging.getLogger(__name__)

# Cache for Secrets Manager password (refresh every 30 minutes)
_secret_cache = {
    "password": None,
    "expires_at": 0,
}

# Cache for IAM auth token (refresh every 10 minutes)
_token_cache = {
    "token": None,
    "expires_at": 0,
}
_cache_lock = threading.Lock()

# Secrets Manager config
SECRETS_MANAGER_SECRET_NAME = os.getenv(
    "DB_SECRET_NAME", "sdlc-orch/rds/rds-credentials/sdlc-orchestration-agent"
)
SECRET_REFRESH_SECONDS = 1800  # 30 minutes
TOKEN_REFRESH_SECONDS = 600    # 10 minutes


def _fetch_password_from_secrets_manager(region: str) -> dict:
    """
    Fetch database credentials from AWS Secrets Manager.
    Returns dict with POSTGRES_USER, POSTGRES_PASSWORD, etc.
    """
    import boto3
    from base64 import b64decode

    try:
        session = boto3.session.Session()
        client = session.client(service_name='secretsmanager', region_name=region)
        logger.info(f"[DB_CONFIG] Fetching secrets from: {SECRETS_MANAGER_SECRET_NAME}")

        response = client.get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)

        if 'SecretString' in response:
            secret = json.loads(response['SecretString'])
        else:
            secret = json.loads(b64decode(response['SecretBinary']))

        logger.info("[DB_CONFIG] Successfully fetched credentials from Secrets Manager")
        return secret

    except Exception as e:
        logger.warning(f"[DB_CONFIG] Failed to fetch from Secrets Manager: {e}")
        return {}


def _get_cached_secret_password(region: str) -> str:
    """Get cached password from Secrets Manager, refreshing if expired."""
    with _cache_lock:
        now = time.time()
        if _secret_cache["password"] and now < _secret_cache["expires_at"]:
            return _secret_cache["password"]

        secret = _fetch_password_from_secrets_manager(region)
        password = secret.get("POSTGRES_PASSWORD", "")
        if password:
            _secret_cache["password"] = password
            _secret_cache["expires_at"] = now + SECRET_REFRESH_SECONDS
            return password
        return ""


def _generate_iam_auth_token(host: str, port: int, user: str, region: str) -> str:
    """Generate a temporary IAM auth token via boto3."""
    import boto3

    rds_client = boto3.client("rds", region_name=region)
    token = rds_client.generate_db_auth_token(
        DBHostname=host,
        Port=port,
        DBUsername=user,
        Region=region,
    )
    logger.info("[DB_CONFIG] Generated fresh IAM auth token")
    return token


def _get_cached_iam_token(host: str, port: int, user: str, region: str) -> str:
    """Get cached IAM auth token, refreshing if expired."""
    with _cache_lock:
        now = time.time()
        if _token_cache["token"] and now < _token_cache["expires_at"]:
            return _token_cache["token"]

        token = _generate_iam_auth_token(host, port, user, region)
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + TOKEN_REFRESH_SECONDS
        return token


def get_db_params() -> dict:
    """
    Get database connection parameters.

    Supports both ECS env var names and legacy names:
      ECS:    RDS_HOST, RDS_PORT, RDS_DATABASE
      Legacy: DATABASE_HOST, DATABASE_PORT, DATABASE_NAME

    Authentication priority:
      1. DATABASE_PASSWORD env var (local dev)
      2. Secrets Manager (production - fetches POSTGRES_PASSWORD)
      3. IAM auth token (fallback)

    Returns:
        dict with keys: host, port, database, user, password, and optionally sslmode
    """
    # Support both ECS and legacy env var names
    host = os.getenv("DATABASE_HOST") or os.getenv("RDS_HOST")
    port = int(os.getenv("DATABASE_PORT") or os.getenv("RDS_PORT") or "5432")
    database = os.getenv("DATABASE_NAME") or os.getenv("RDS_DATABASE", "sdlcdev")
    user = os.getenv("DATABASE_USER") or os.getenv("RDS_USER", "postgres")
    region = os.getenv("AWS_REGION", "us-east-1")

    if not host:
        raise ValueError(
            "No database host configured. "
            "Set DATABASE_HOST or RDS_HOST environment variable."
        )

    # Priority 1: Static password from env var
    static_password = os.getenv("DATABASE_PASSWORD", "").strip()
    if static_password:
        logger.info("[DB_CONFIG] Using static password from DATABASE_PASSWORD env var")
        return {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": static_password,
        }

    # Priority 2: Fetch from Secrets Manager (recommended by DevOps)
    sm_password = _get_cached_secret_password(region)
    if sm_password:
        logger.info("[DB_CONFIG] Using password from Secrets Manager")
        # Also get the user from Secrets Manager if available
        secret = _fetch_password_from_secrets_manager(region)
        sm_user = secret.get("POSTGRES_USER", user)
        return {
            "host": host,
            "port": port,
            "database": database,
            "user": sm_user,
            "password": sm_password,
        }

    # Priority 3: IAM auth token
    logger.info("[DB_CONFIG] Using IAM auth token (no static or SM password)")
    token = _get_cached_iam_token(host, port, user, region)
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": token,
        "sslmode": "require",
    }


def get_direct_connection():
    """
    Get a direct psycopg2 connection (not pooled).
    Useful for one-off scripts like setup_client_db.py.
    """
    import psycopg2

    params = get_db_params()
    logger.info(f"[DB_CONFIG] Connecting to {params['host']}:{params['port']}/{params['database']}")
    return psycopg2.connect(**params)
