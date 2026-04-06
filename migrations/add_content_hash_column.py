"""
Live migration: Add content_hash column and cross-project lookup index
to the existing document_embeddings table.

Safe to run multiple times — both statements use IF NOT EXISTS / DO NOTHING guards.
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def run():
    print("🔄 Connecting to live database...")
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        print("✅ Connected!")
        cursor = conn.cursor()

        # 1. Add content_hash column (idempotent — does nothing if column already exists)
        print("\n📝 Adding content_hash column to document_embeddings...")
        cursor.execute("""
            ALTER TABLE document_embeddings
            ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);
        """)
        print("✅ content_hash column added (or already existed — no-op).")

        # 2. Add cross-project lookup index (idempotent)
        print("\n📊 Creating idx_embeddings_content_lookup index...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_content_lookup
            ON document_embeddings (source_type, source_id, chunk_index);
        """)
        print("✅ idx_embeddings_content_lookup index created (or already existed — no-op).")

        conn.commit()
        cursor.close()

        print("\n" + "="*60)
        print("✅ Live migration completed successfully!")
        print("="*60)
        print("\n📋 Summary:")
        print("  - document_embeddings.content_hash  VARCHAR(64) NULL  ← added")
        print("  - idx_embeddings_content_lookup on (source_type, source_id, chunk_index)  ← added")
        print("\n🎯 Embedding dedup (hybrid reuse) is now active.")

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
