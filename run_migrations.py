"""One-shot script: triggers db_helper._run_migrations and verifies the
user_module_activity table exists. Safe to re-run — every statement is
idempotent (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, etc.).

Usage (from sdlc_python_fastapi_backend dir, with venv activated):
    python run_migrations.py
"""

import logging
import os
from dotenv import load_dotenv

# Load .env before importing db_helper (db params come from environment)
load_dotenv(override=True)
print(f"DEBUG: DATABASE_HOST={os.getenv('DATABASE_HOST')}")

from db_helper import get_db_connection, release_db_connection, get_db_pool  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    print("=" * 60)
    print("Running migrations…")
    print("=" * 60)
    # First call to get_db_pool() runs _run_migrations as a side effect.
    pool = get_db_pool()
    if pool is None:
        print("[FAIL] DB pool failed to initialize")
        return 1

    # Verify the new table + indexes exist.
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'user_module_activity';
            """)
            row = cursor.fetchone()
            if not row:
                print("[FAIL] user_module_activity table not found after migrations")
                return 2
            print(f"[OK] user_module_activity table exists.")

            cursor.execute("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'user_module_activity'
                ORDER BY indexname;
            """)
            indexes = [r[0] for r in cursor.fetchall()]
            print(f"[OK] {len(indexes)} indexes found:")
            for ix in indexes:
                print(f"     - {ix}")

            cursor.execute("SELECT COUNT(*) FROM user_module_activity;")
            count = cursor.fetchone()[0]
            print(f"[OK] Current row count: {count}")
    finally:
        release_db_connection(conn)

    print("=" * 60)
    print("Migrations completed successfully.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
