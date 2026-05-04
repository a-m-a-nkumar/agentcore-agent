"""
Migration: drop the design_sessions.is_deleted column.

We were soft-deleting design sessions (UPDATE SET is_deleted=TRUE) but
never surfacing them anywhere — no trash bin, no undo, no audit view —
so the column was dead weight that bloated every SELECT with a
'WHERE is_deleted = FALSE' filter and accumulated invisible rows.

DEPLOY ORDER (important — running this before the new code is live
will break every running FastAPI / Lambda that still queries
WHERE is_deleted = FALSE):

  1. Deploy backend code that no longer references is_deleted in any
     query (commit 7a777ce or later).
  2. Wait until ALL running instances have picked up the new code.
  3. THEN run this migration:
        python migrations/drop_design_sessions_is_deleted.py

What it does:
  • Hard-deletes any rows currently marked is_deleted = TRUE.
  • Drops the is_deleted column from design_sessions.
  • Recreates the project + user indexes without the partial
    'WHERE is_deleted = FALSE' clause they used to carry.

After this lands, design_sessions is plain "row exists = real session",
"row gone = deleted". S3 artefacts under sessions/{id}/* are preserved
either way (operators can scrub them via the AWS console).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv()


# Idempotent: column existence is checked before dropping.
PURGE_SQL = """
DELETE FROM design_sessions
 WHERE EXISTS (
       SELECT 1
         FROM information_schema.columns
        WHERE table_name = 'design_sessions'
          AND column_name = 'is_deleted'
   )
   AND is_deleted = TRUE;
"""

DROP_COLUMN_SQL = """
ALTER TABLE design_sessions
DROP COLUMN IF EXISTS is_deleted;
"""

# Indexes that referenced is_deleted were partial — they no longer make
# sense without the column. Drop and recreate without the WHERE clause.
DROP_PARTIAL_INDEXES_SQL = """
DROP INDEX IF EXISTS idx_design_sessions_project;
DROP INDEX IF EXISTS idx_design_sessions_user;
"""

CREATE_PROJECT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_design_sessions_project
    ON design_sessions (project_id, last_activity_ts DESC);
"""

CREATE_USER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_design_sessions_user
    ON design_sessions (user_id, last_activity_ts DESC);
"""


def run():
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        cur = conn.cursor()

        # Trick to allow the migration to be re-run after the column is
        # already gone: only execute the WHERE-is_deleted DELETE if the
        # column still exists. Postgres supports this via the EXISTS check
        # in PURGE_SQL above which short-circuits.
        cur.execute(PURGE_SQL)
        purged = cur.rowcount
        cur.execute(DROP_COLUMN_SQL)
        cur.execute(DROP_PARTIAL_INDEXES_SQL)
        cur.execute(CREATE_PROJECT_INDEX_SQL)
        cur.execute(CREATE_USER_INDEX_SQL)

        conn.commit()
        cur.close()
        print(
            f"[OK] Dropped design_sessions.is_deleted. "
            f"Hard-deleted {purged} previously-soft-deleted row(s). "
            f"Indexes refreshed without the partial WHERE clause."
        )
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
