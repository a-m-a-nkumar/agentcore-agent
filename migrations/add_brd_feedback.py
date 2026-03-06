"""
Migration: Add brd_feedback table for storing user ratings (1-10) per BRD generation.
Run once: python migrations/add_brd_feedback.py
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brd_feedback (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                brd_id VARCHAR(255) NOT NULL,
                session_id VARCHAR(255),
                score SMALLINT NOT NULL CHECK (score >= 1 AND score <= 10),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_brd_feedback_user_id ON brd_feedback(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_brd_feedback_brd_id ON brd_feedback(brd_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_brd_feedback_created_at ON brd_feedback(created_at DESC)")
        conn.commit()
        cur.close()
        print("✅ brd_feedback table created (or already exists).")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
