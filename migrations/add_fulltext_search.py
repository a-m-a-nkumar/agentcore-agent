"""
Migration: Add full-text search (tsvector + GIN index) to document_embeddings
=============================================================================
Enables BM25 keyword search alongside existing vector similarity search.
Uses a GENERATED ALWAYS AS column so existing and future rows are auto-indexed.

Safe to run multiple times — all statements use IF NOT EXISTS / DO NOTHING guards.
"""

import psycopg2
import os
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
        print("Connected!")
        cursor = conn.cursor()

        # 1. Add generated tsvector column (idempotent)
        print("\nAdding content_tsvector generated column to document_embeddings...")
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_embeddings'
                      AND column_name = 'content_tsvector'
                ) THEN
                    ALTER TABLE document_embeddings
                    ADD COLUMN content_tsvector tsvector
                    GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content_chunk, ''))
                    ) STORED;
                END IF;
            END $$;
        """)
        print("content_tsvector column ready.")

        # 2. Create GIN index for fast full-text search (idempotent)
        print("\nCreating GIN index for full-text search...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_embeddings_fulltext
            ON document_embeddings USING GIN (content_tsvector);
        """)
        print("GIN index created.")

        conn.commit()
        cursor.close()

        print("\n" + "=" * 60)
        print("Migration completed successfully!")
        print("=" * 60)
        print("\nSummary:")
        print("  - document_embeddings.content_tsvector  GENERATED ALWAYS AS ... STORED")
        print("  - idx_embeddings_fulltext USING GIN (content_tsvector)")
        print("\nBM25 keyword search is now available.")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\nMigration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()
            print("\nDatabase connection closed.")


if __name__ == "__main__":
    run()
