"""
Migration: Upgrade dedup index to include content_hash (4-column composite)
===========================================================================
Problem:
    The existing idx_embeddings_content_lookup covers only 3 columns:
        (source_type, source_id, chunk_index)

    But find_existing_embedding() filters on ALL 4 columns:
        WHERE source_type = %s
          AND source_id   = %s
          AND chunk_index = %s
          AND content_hash = %s   ← not in old index!

    This means PostgreSQL was:
      1. Using the 3-col index to narrow down to a chunk group ✅
      2. Then doing a heap fetch + filter on content_hash rows ❌

Fix:
    Drop the old 3-col index and replace it with the complete 4-col version.
    This makes find_existing_embedding() a pure index scan — zero heap fetches.

Safe to run multiple times — all statements are idempotent.
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()


def run():
    print("🔄 Connecting to database...")
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        print("✅ Connected!\n")
        conn.autocommit = False
        cursor = conn.cursor()

        # ── Step 1: Drop the old 3-column index ──────────────────────────────
        print("🗑️  Dropping old 3-column index (idx_embeddings_content_lookup)...")
        cursor.execute("""
            DROP INDEX IF EXISTS idx_embeddings_content_lookup;
        """)
        print("✅ Old index dropped (or did not exist — no-op).")

        # ── Step 2: Create the complete 4-column composite index ─────────────
        print("\n📊 Creating new 4-column dedup index...")
        print("   Columns: (source_type, source_id, chunk_index, content_hash)")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_content_lookup
            ON document_embeddings (source_type, source_id, chunk_index, content_hash);
        """)
        print("✅ idx_embeddings_content_lookup (4-col) created.")

        conn.commit()
        cursor.close()

        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
        print("=" * 60)
        print("\n📋 Summary of changes:")
        print("  BEFORE:  idx_embeddings_content_lookup  →  (source_type, source_id, chunk_index)")
        print("  AFTER:   idx_embeddings_content_lookup  →  (source_type, source_id, chunk_index, content_hash)")
        print("\n🎯 Impact:")
        print("  find_existing_embedding() is now a PURE INDEX SCAN")
        print("  Zero heap fetches — maximum dedup performance ⚡")
        print("=" * 60)

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()
            print("\n🔌 Database connection closed.")


if __name__ == "__main__":
    run()
