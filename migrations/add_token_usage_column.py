"""
Migration: Add token_usage column to users table
==================================================
Per-user cumulative LLM token counter — atomically incremented on every
LLM call via `increment_user_token_usage()` (called from llm_gateway).
Feeds the Organization Usage dashboard token totals.

This migration was historically applied by db_helper._run_migrations()
at app boot. Made explicit here so a fresh siriusai stand-up can apply
it before app code runs.

Safe to run on any database — uses ADD COLUMN IF NOT EXISTS.
Run AFTER: setup_core_tables.py (needs users table to exist).
"""

import os
import sys
import logging

# Add parent directory to path so we can import db_helper
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from dotenv import load_dotenv
env_path = os.path.join(parent_dir, '.env')
load_dotenv(env_path, override=True)

from db_helper import get_db_connection, release_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(f"DATABASE_HOST: {os.getenv('DATABASE_HOST', 'NOT SET')}")
logger.info(f"DATABASE_NAME: {os.getenv('DATABASE_NAME', 'NOT SET')}")


def run():
    """Add users.token_usage (BIGINT NOT NULL DEFAULT 0)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            logger.info("Adding token_usage column to users table...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS token_usage BIGINT NOT NULL DEFAULT 0
            """)
            conn.commit()
            logger.info("+ Added (or confirmed) users.token_usage")

            # Verify
            cursor.execute("""
                SELECT column_name, data_type, column_default, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'users'
                  AND column_name = 'token_usage'
            """)
            row = cursor.fetchone()
            if row:
                logger.info(f"Verified: token_usage ({row[1]}, default={row[2]}, nullable={row[3]})")
            else:
                raise RuntimeError("Migration ran but users.token_usage not present afterwards")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding token_usage column: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Migration: users.token_usage column")
    logger.info("=" * 60)
    run()
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
