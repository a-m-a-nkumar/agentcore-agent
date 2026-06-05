"""
Migration: Add BRD session columns to projects table.
Adds brd_id and agentcore_session_id for BRD transcript agent session maintenance.
Run once: python migrations/add_brd_session_columns.py
"""

import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv()


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

        cur.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS brd_id TEXT")
        cur.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS agentcore_session_id TEXT")

        conn.commit()
        cur.close()
        print("✅ BRD session columns added to projects table (or already exist).")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
