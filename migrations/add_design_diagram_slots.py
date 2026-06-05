"""
Migration: add per-type diagram_slots column to design_sessions.

The SAD-redesign turns Plate 00 into three independent slots — Logical,
Infrastructure, Security. The previous schema had ONE diagram per
session (`diagram_s3_key`, `diagram_svg_s3_key`); we keep those for
backward-compat (legacy callers + the `logical` slot's S3 keys still
write here), and add a JSONB column tracking the full per-type state.

Default shape on a new row:
    {
      "logical":        {"status": "pending"},
      "infrastructure": {"status": "pending"},
      "security":       {"status": "pending"}
    }

Per-slot shape (populated by save / skip / unskip handlers):
    {
      "status":       "pending|in_progress|done|skipped|skipped_saved|failed",
      "tool":         "drawio" | "lucid",      // optional, informational
      "artifact_key": "sessions/{id}/diagram/security.svg",  // when Done
      "saved_at":     1714603200,              // epoch seconds (NULL until first save)
      "error":        "string"                 // only when status = failed
    }

Run once:
    python migrations/add_design_diagram_slots.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv()


DEFAULT_SLOTS = (
    '{'
    '"logical": {"status": "pending"}, '
    '"infrastructure": {"status": "pending"}, '
    '"security": {"status": "pending"}'
    '}'
)


ADD_COLUMN_SQL = f"""
ALTER TABLE design_sessions
ADD COLUMN IF NOT EXISTS diagram_slots JSONB
    NOT NULL
    DEFAULT '{DEFAULT_SLOTS}'::JSONB;
"""

ADD_TOOL_COLUMN_SQL = """
ALTER TABLE design_sessions
ADD COLUMN IF NOT EXISTS authoring_tool TEXT;
"""

# Backfill: existing sessions with a diagram_s3_key get their `logical`
# slot marked Done with the legacy artifact, so the redesign UI shows the
# correct status on first load. Sessions that never saved a diagram stay
# at the default all-Pending shape.
BACKFILL_SQL = """
UPDATE design_sessions
SET diagram_slots = jsonb_set(
    diagram_slots,
    '{logical}',
    jsonb_build_object(
        'status', 'done',
        'artifact_key', diagram_s3_key,
        'saved_at', extract(epoch FROM last_activity_ts)::bigint
    ),
    true
)
WHERE diagram_s3_key IS NOT NULL
  AND diagram_slots->'logical'->>'status' = 'pending';
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

        cur.execute(ADD_COLUMN_SQL)
        cur.execute(ADD_TOOL_COLUMN_SQL)
        cur.execute(BACKFILL_SQL)
        rows_backfilled = cur.rowcount

        conn.commit()
        cur.close()
        print(
            f"[OK] design_sessions.diagram_slots + authoring_tool added. "
            f"Backfilled {rows_backfilled} session(s) with prior diagrams."
        )
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
