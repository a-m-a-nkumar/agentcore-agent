"""
Migration: Create design_sessions table for the multi-session Design Assistant.

Each row tracks one user session that can span the Diagram phase (mxGraph
XML + rendered SVG saved to S3) and the SAD phase (sad_structure.json,
facts.json, audit results in S3 + chat in AgentCore Memory). One project
can have many sessions; everything resumes from this row.

Run once:
    python migrations/add_design_sessions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv()


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS design_sessions (
    id                  UUID PRIMARY KEY,
    project_id          UUID NOT NULL,
    user_id             TEXT NOT NULL,
    name                TEXT NOT NULL,
    stage               TEXT NOT NULL DEFAULT 'NEW',
    diagram_s3_key      TEXT,
    diagram_svg_s3_key  TEXT,
    sad_id              UUID,
    confluence_page_id  TEXT,
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_ts    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_design_sessions_project
    ON design_sessions (project_id, last_activity_ts DESC)
    WHERE is_deleted = FALSE;
"""

CREATE_USER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_design_sessions_user
    ON design_sessions (user_id, last_activity_ts DESC)
    WHERE is_deleted = FALSE;
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

        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_INDEX_SQL)
        cur.execute(CREATE_USER_INDEX_SQL)

        conn.commit()
        cur.close()
        print("[OK] design_sessions table + indexes created (or already existed).")
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
