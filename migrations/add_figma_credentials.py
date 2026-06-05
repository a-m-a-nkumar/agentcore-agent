"""
Migration: Add Figma PAT + Team ID columns to users table.
Run once: python migrations/add_figma_credentials.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from db_helper import get_db_connection, release_db_connection
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            ALTER TABLE users
                ADD COLUMN IF NOT EXISTS figma_pat        TEXT,
                ADD COLUMN IF NOT EXISTS figma_team_id    VARCHAR(255),
                ADD COLUMN IF NOT EXISTS figma_linked_at  TIMESTAMP WITH TIME ZONE;
        """)
        conn.commit()
        cursor.close()
        logger.info("Migration complete: figma_pat, figma_team_id, figma_linked_at added to users table.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    run()
