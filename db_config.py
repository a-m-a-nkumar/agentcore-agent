"""
Centralized Database Configuration
===================================
Provides database connection parameters from environment variables.

Usage:
  from db_config import get_db_params, get_direct_connection
  params = get_db_params()  # Returns dict with host, port, database, user, password
  conn = get_direct_connection()  # Returns a psycopg2 connection
"""

import os
import logging

logger = logging.getLogger(__name__)


def get_db_params() -> dict:
    """
    Get database connection parameters from environment variables.

    Supports both ECS env var names and legacy names:
      ECS:    RDS_HOST, RDS_PORT, RDS_DATABASE
      Legacy: DATABASE_HOST, DATABASE_PORT, DATABASE_NAME

    Returns:
        dict with keys: host, port, database, user, password
    """
    host = os.getenv("DATABASE_HOST") or os.getenv("RDS_HOST")
    port = int(os.getenv("DATABASE_PORT") or os.getenv("RDS_PORT") or "5432")
    database = os.getenv("DATABASE_NAME") or os.getenv("RDS_DATABASE", "postgres")
    user = os.getenv("DATABASE_USER") or os.getenv("RDS_USER", "postgres")
    password = os.getenv("DATABASE_PASSWORD", "").strip()

    if not host:
        raise ValueError(
            "No database host configured. "
            "Set DATABASE_HOST or RDS_HOST environment variable."
        )

    if not password:
        raise ValueError(
            "No database password configured. "
            "Set DATABASE_PASSWORD environment variable."
        )

    logger.info(f"[DB_CONFIG] Using {host}:{port}/{database} as user={user}")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
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
