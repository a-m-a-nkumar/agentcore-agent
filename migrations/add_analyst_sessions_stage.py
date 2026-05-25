"""
Migration: Add `stage` and `use_long_term_context` columns to analyst_sessions.

Part of the Unified BRD Agent rollout (features/aman). Two new columns:

  stage TEXT NOT NULL DEFAULT 'NEW'
    The new BRD session stage. Valid values come from db_helper.py's
    BRD_SESSION_STAGES enum:
        NEW | GATHERING | GENERATING | DRAFTED | REFINING
    Mirrors the design_sessions.stage column SAD uses.

  use_long_term_context BOOLEAN NOT NULL DEFAULT TRUE
    Per-session toggle controlling whether the orchestrator retrieves
    long-term semantic facts from AgentCore Memory for this session.
    TRUE  (default)  → Mary remembers across sessions for this project.
    FALSE            → Fresh session, no context from prior sessions.
    Writes to long-term memory still happen regardless of this flag; it
    only gates retrieval. See "Per-session context mode" in the plan.

Backfill rules for existing rows:
  - stage = 'REFINING' if brd_id IS NOT NULL else 'NEW'
    (any session that has already produced a BRD is treated as REFINING;
    everything else is treated as never-started.)
  - use_long_term_context = TRUE for everyone
    (matches the default; users who want fresh can create a new session.)

Run once:
    python migrations/add_analyst_sessions_stage.py

Idempotent: ADD COLUMN IF NOT EXISTS makes re-runs safe.
"""

import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv()


ADD_STAGE_COLUMN_SQL = """
ALTER TABLE analyst_sessions
    ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'NEW';
"""

ADD_USE_LTC_COLUMN_SQL = """
ALTER TABLE analyst_sessions
    ADD COLUMN IF NOT EXISTS use_long_term_context BOOLEAN NOT NULL DEFAULT TRUE;
"""

# Backfill stage for existing rows. Default is 'NEW' but any session with
# a brd_id has already produced a BRD, so REFINING is the accurate stage.
# This update is idempotent — re-running just no-ops on already-correct rows.
BACKFILL_STAGE_SQL = """
UPDATE analyst_sessions
   SET stage = 'REFINING'
 WHERE brd_id IS NOT NULL
   AND stage = 'NEW';
"""

# An index on stage helps the "list sessions by stage" queries the
# frontend will use for the session sidebar (e.g. "show only sessions
# that have generated a BRD"). Cheap to add; cheap to drop if unused.
CREATE_STAGE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_analyst_sessions_stage
    ON analyst_sessions (stage)
    WHERE is_deleted = FALSE;
"""


def run() -> None:
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

        cur.execute(ADD_STAGE_COLUMN_SQL)
        cur.execute(ADD_USE_LTC_COLUMN_SQL)

        # Backfill returns the number of rows updated so we can log it.
        cur.execute(BACKFILL_STAGE_SQL)
        backfilled = cur.rowcount

        cur.execute(CREATE_STAGE_INDEX_SQL)

        conn.commit()
        cur.close()

        print(f"[OK] analyst_sessions: stage + use_long_term_context columns added.")
        print(f"[OK] Backfilled stage='REFINING' for {backfilled} existing session(s) with brd_id.")
        print(f"[OK] Index idx_analyst_sessions_stage created (or already existed).")
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    run()
