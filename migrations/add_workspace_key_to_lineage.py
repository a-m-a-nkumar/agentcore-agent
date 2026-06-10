"""
Migration: Add workspace_key to artifact_lineage
================================================
Adds a `workspace_key` column to artifact_lineage so lineage rows can be
shared across multiple projects that point at the same Confluence space +
Jira project. Backfills existing rows from projects.confluence_space_key
and projects.jira_project_key.

Run AFTER: add_artifact_lineage.py
Safe to re-run (uses IF NOT EXISTS / WHERE workspace_key IS NULL).
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def run():
    print("Connecting to database...")
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        print("Connected.\n")
        cursor = conn.cursor()

        # ── 1. Ensure pgcrypto for digest() — already loaded on most installs
        print("[1/4] Ensuring pgcrypto extension...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        conn.commit()
        print("   ok")

        # ── 2. Add column
        print("[2/4] Adding workspace_key column to artifact_lineage...")
        cursor.execute("""
            ALTER TABLE artifact_lineage
            ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(64)
        """)
        conn.commit()
        print("   ok")

        # ── 3. Backfill from projects.
        # workspace_key = sha1(confluence_space_key || '|' || jira_project_key)
        # truncated to 32 hex chars to match the helper in services/workspace.py.
        # Rows for projects missing either field get sha1('|') as a fallback —
        # they'll still group together (and can be re-keyed once the project is
        # fully configured).
        print("[3/4] Backfilling workspace_key from projects...")
        cursor.execute("""
            UPDATE artifact_lineage al
               SET workspace_key = substr(
                     encode(
                       digest(
                         coalesce(p.confluence_space_key, '') || '|' ||
                         coalesce(p.jira_project_key, ''),
                         'sha1'
                       ),
                       'hex'
                     ),
                     1, 32
                   )
              FROM projects p
             WHERE al.project_id = p.id
               AND al.workspace_key IS NULL
        """)
        backfilled = cursor.rowcount
        conn.commit()
        print(f"   backfilled {backfilled} rows")

        # ── 4. Index for the forward lookup keyed on workspace.
        print("[4/4] Creating index idx_lineage_workspace_lookup...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_workspace_lookup
            ON artifact_lineage (workspace_key, source_id, source_section_id, status)
        """)
        conn.commit()
        print("   ok")

        # Sanity check — how many rows still missing the key
        cursor.execute(
            "SELECT count(*) FROM artifact_lineage WHERE workspace_key IS NULL"
        )
        remaining = cursor.fetchone()[0]
        print(f"\nLineage rows still missing workspace_key: {remaining}")
        print("Migration complete.")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
