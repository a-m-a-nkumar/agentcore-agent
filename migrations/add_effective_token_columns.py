"""
Migration: Sonnet-equivalent (cost-normalized) token columns + daily table
===========================================================================
Adds two cumulative per-user counters that re-value raw token usage into
"Sonnet-4.5-equivalent" units (cache reads discounted ~0.1x, cheaper models
scaled down) — computed in llm_gateway._effective_tokens and written via
increment_user_token_usage(). Also adds a per-day aggregate table so usage
can later be enforced/queried over time windows (daily rolls up to monthly).

Raw `users.token_usage` is left unchanged.

Safe to run on any database — uses ADD COLUMN / CREATE TABLE IF NOT EXISTS.
Run AFTER: add_token_usage_column.py (needs users.token_usage to exist).
Run BEFORE: restarting the backend / deploying the Lambdas (the increment
references these objects, so they must exist first).
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
    """Add the two sonnet-equivalent columns + the daily aggregate table."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            logger.info("Adding sonnet-equivalent token columns to users...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS sonnet_equivalent_input_tokens BIGINT NOT NULL DEFAULT 0
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS sonnet_equivalent_output_tokens BIGINT NOT NULL DEFAULT 0
            """)
            cursor.execute("""
                COMMENT ON COLUMN users.sonnet_equivalent_input_tokens IS
                'Cumulative INPUT usage re-valued to equivalent uncached Sonnet-4.5 input tokens of equal cost (cache read 0.1x, cache write 1.25x, model-scaled). Not a raw token count.'
            """)
            cursor.execute("""
                COMMENT ON COLUMN users.sonnet_equivalent_output_tokens IS
                'Cumulative OUTPUT usage re-valued to equivalent Sonnet-4.5 output tokens of equal cost (model-scaled). Not a raw token count.'
            """)

            logger.info("Creating user_token_usage_daily table...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_token_usage_daily (
                    user_id                          TEXT   NOT NULL,
                    usage_date                       DATE   NOT NULL,
                    raw_tokens                       BIGINT NOT NULL DEFAULT 0,
                    sonnet_equivalent_input_tokens   BIGINT NOT NULL DEFAULT 0,
                    sonnet_equivalent_output_tokens  BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, usage_date)
                )
            """)
            conn.commit()
            logger.info("+ Added (or confirmed) effective-token columns + daily table")

            # Verify
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'users'
                  AND column_name IN ('sonnet_equivalent_input_tokens',
                                      'sonnet_equivalent_output_tokens')
            """)
            cols = sorted(r[0] for r in cursor.fetchall())
            cursor.execute("SELECT to_regclass('public.user_token_usage_daily')")
            tbl = cursor.fetchone()[0]
            if len(cols) == 2 and tbl:
                logger.info(f"Verified: users columns {cols}; table {tbl}")
            else:
                raise RuntimeError(f"Migration ran but objects missing (cols={cols}, table={tbl})")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding effective-token objects: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Migration: sonnet-equivalent token columns + daily table")
    logger.info("=" * 60)
    run()
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
