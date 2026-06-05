"""
Migration: Create user_module_activity table
=============================================
Per-event log feeding the Organization Usage dashboard's events column
+ recent-activity rows. Every meaningful module action (BRD generated,
Jira stories pushed, MCP prompt enhanced, test scenarios pushed, etc.)
inserts one row here via `track_event()` (db_helper.py:1514).

This migration was historically applied by db_helper._run_migrations()
at app boot. Made explicit here so a fresh siriusai stand-up can apply
it before app code runs.

Safe to run on any database — uses CREATE TABLE / INDEX IF NOT EXISTS.
Run AFTER: setup_core_tables.py (needs users + projects tables for FKs).
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
    """Create user_module_activity table + 4 indexes."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            logger.info("Creating user_module_activity table...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_module_activity (
                    id            BIGSERIAL PRIMARY KEY,
                    user_id       VARCHAR(255) NOT NULL,
                    project_id    VARCHAR(255),
                    module        VARCHAR(64)  NOT NULL,
                    event_type    VARCHAR(128) NOT NULL,
                    source        VARCHAR(32)  NOT NULL DEFAULT 'web',
                    occurred_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata      JSONB        NOT NULL DEFAULT '{}'::jsonb,
                    CONSTRAINT fk_uma_user
                        FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
                    CONSTRAINT fk_uma_project
                        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                )
            """)
            logger.info("+ Created (or confirmed) user_module_activity table")

            logger.info("Creating indexes on user_module_activity...")
            indexes = [
                ("idx_uma_user_time",   "user_module_activity(user_id, occurred_at DESC)"),
                ("idx_uma_module_time", "user_module_activity(module, occurred_at DESC)"),
                ("idx_uma_event_time",  "user_module_activity(event_type, occurred_at DESC)"),
                ("idx_uma_user_module", "user_module_activity(user_id, module)"),
            ]
            for name, defn in indexes:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {defn}")
                logger.info(f"+ {name}")
            conn.commit()

            # Verify
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'user_module_activity'
            """)
            if not cursor.fetchone():
                raise RuntimeError("Migration ran but user_module_activity table not present afterwards")

            cursor.execute("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'user_module_activity'
                ORDER BY indexname
            """)
            found = [r[0] for r in cursor.fetchall()]
            logger.info(f"Verified: table exists with {len(found)} indexes: {found}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating user_module_activity table: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Migration: user_module_activity table")
    logger.info("=" * 60)
    run()
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
