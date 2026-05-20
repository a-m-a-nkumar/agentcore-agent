"""
Live migration: Add source_updated_at column to document_embeddings for
time-aware re-ranking in the RAG pipeline.

What this does:
  1. ALTER TABLE document_embeddings ADD COLUMN source_updated_at TIMESTAMPTZ
     (idempotent via IF NOT EXISTS)
  2. CREATE INDEX on source_updated_at DESC (idempotent)
  3. Backfill source_updated_at from:
       - confluence_pages.last_modified_at for source_type='confluence'
       - jira_issues.updated_date         for source_type='jira'

After this migration:
  - All existing embeddings get a populated source_updated_at value where the
    parent metadata row exists.
  - Embeddings whose parent row has been deleted (orphans) stay NULL — the
    recency function treats NULL as DECAY_FLOOR, never as a freshness boost.
  - New inserts must populate source_updated_at via insert_document_embedding.

Safe to run multiple times — every step is idempotent.
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()


def run():
    print("Connecting to live database...")
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        print("Connected.")
        cursor = conn.cursor()

        # 1. Add source_updated_at column
        print("\n[1/4] Adding source_updated_at column to document_embeddings...")
        cursor.execute("""
            ALTER TABLE document_embeddings
            ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ;
        """)
        print("    column ready.")

        # 2. Index for future time-range filtering (QDF hard filters, eval queries)
        print("\n[2/4] Creating idx_embeddings_source_updated_at index...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_source_updated_at
            ON document_embeddings (source_updated_at DESC NULLS LAST);
        """)
        print("    index ready.")

        # 3. Backfill from confluence_pages
        print("\n[3/4] Backfilling source_updated_at from confluence_pages...")
        cursor.execute("""
            UPDATE document_embeddings de
            SET source_updated_at = cp.last_modified_at
            FROM confluence_pages cp
            WHERE de.source_type = 'confluence'
              AND de.source_id   = cp.page_id
              AND de.project_id  = cp.project_id
              AND de.source_updated_at IS DISTINCT FROM cp.last_modified_at;
        """)
        confluence_updated = cursor.rowcount
        print(f"    backfilled {confluence_updated} confluence chunk rows.")

        # 4. Backfill from jira_issues
        print("\n[4/4] Backfilling source_updated_at from jira_issues...")
        cursor.execute("""
            UPDATE document_embeddings de
            SET source_updated_at = ji.updated_date
            FROM jira_issues ji
            WHERE de.source_type = 'jira'
              AND de.source_id   = ji.issue_key
              AND de.project_id  = ji.project_id
              AND de.source_updated_at IS DISTINCT FROM ji.updated_date;
        """)
        jira_updated = cursor.rowcount
        print(f"    backfilled {jira_updated} jira chunk rows.")

        # Stats: how many embeddings have a populated timestamp now?
        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE source_updated_at IS NOT NULL) AS populated,
                COUNT(*) FILTER (WHERE source_updated_at IS NULL)     AS null_count,
                COUNT(*)                                              AS total
            FROM document_embeddings;
        """)
        populated, null_count, total = cursor.fetchone()

        conn.commit()
        cursor.close()

        print("\n" + "=" * 60)
        print("Migration completed.")
        print("=" * 60)
        print(f"  document_embeddings.source_updated_at populated: {populated}/{total} ({null_count} NULL)")
        print(f"  Confluence rows backfilled: {confluence_updated}")
        print(f"  Jira rows backfilled:       {jira_updated}")
        print()
        print("  NULLs are expected for: orphaned embeddings whose parent row was deleted,")
        print("  or source_types other than 'confluence' / 'jira'.")
        print("  The recency function treats NULL as DECAY_FLOOR (conservative, never boosted).")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\nMigration failed: {e}")
        raise
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    run()
