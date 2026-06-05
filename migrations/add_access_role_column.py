"""
Migration: Add access_role column to users table
==================================================
Per-user access tier derived from Azure AD group membership. Acceptable
values: 'BOTH', 'TECH', 'BUSINESS', 'NONE'. Refreshed on every
authenticated request by `update_user_access_role()` (UPSERT-based as
of commit 0e4bcd6 — handles brand-new users whose row doesn't exist yet).

Feeds the access-role chip in the Organization Usage dashboard.

This migration was historically applied by db_helper._run_migrations()
at app boot. Made explicit here so a fresh siriusai stand-up can apply
it before app code runs.

Safe to run on any database — uses ADD COLUMN IF NOT EXISTS.
Run AFTER: setup_core_tables.py (needs users table to exist).
"""

import os
import sys
import logging

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
    """Add users.access_role (VARCHAR(16) NOT NULL DEFAULT 'NONE')."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            logger.info("Adding access_role column to users table...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS access_role VARCHAR(16) NOT NULL DEFAULT 'NONE'
            """)
            conn.commit()
            logger.info("+ Added (or confirmed) users.access_role")

            cursor.execute("""
                SELECT column_name, data_type, character_maximum_length, column_default, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'users'
                  AND column_name = 'access_role'
            """)
            row = cursor.fetchone()
            if row:
                logger.info(
                    f"Verified: access_role ({row[1]}({row[2]}), default={row[3]}, nullable={row[4]})"
                )
            else:
                raise RuntimeError("Migration ran but users.access_role not present afterwards")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding access_role column: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Migration: users.access_role column")
    logger.info("=" * 60)
    run()
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
