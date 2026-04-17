"""
Migration: Create Artifact Lineage Table
=========================================
Creates the artifact_lineage table for tracking the relationship between
source BRD requirements and generated artifacts (Jira stories, test scenarios).

Safe to run on a fresh database. All statements use IF NOT EXISTS.
Run AFTER: 02_setup_core_tables.py (needs users, projects tables)
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
        cursor = conn.cursor()

        # ── 1. ARTIFACT LINEAGE TABLE ─────────────────────────────────────────
        print("📊 [1/3] Creating artifact_lineage table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS artifact_lineage (
                id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                project_id                  VARCHAR(255) NOT NULL,
                user_id                     VARCHAR(255) NOT NULL,

                source_type                 VARCHAR(50)  NOT NULL,
                source_id                   VARCHAR(255) NOT NULL,
                source_section_id           VARCHAR(50)  NOT NULL,
                source_version              INTEGER      NOT NULL,
                source_content_hash         VARCHAR(64)  NOT NULL,

                target_type                 VARCHAR(50)  NOT NULL,
                target_id                   VARCHAR(255) NOT NULL,
                target_content_hash         VARCHAR(64)  NOT NULL,
                target_metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,

                original_generated_content  JSONB        NOT NULL,
                status                      VARCHAR(30)  NOT NULL DEFAULT 'current',

                created_at                  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at                  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

                CONSTRAINT fk_lineage_project FOREIGN KEY (project_id)
                    REFERENCES projects(id) ON DELETE CASCADE,
                CONSTRAINT fk_lineage_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
        print("   ✅ artifact_lineage table created")

        # ── 2. INDEXES ────────────────────────────────────────────────────────
        print("📊 [2/3] Creating indexes...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_source_lookup
            ON artifact_lineage (project_id, source_id, source_section_id, status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_target_lookup
            ON artifact_lineage (project_id, target_type, target_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lineage_project_status
            ON artifact_lineage (project_id, status)
        """)
        conn.commit()
        print("   ✅ Indexes created")

        # ── 3. AUTO-UPDATE TRIGGER ────────────────────────────────────────────
        print("📊 [3/3] Creating auto-update trigger...")
        # Reuse the update_updated_at_column() function created by setup_core_tables
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        cursor.execute("DROP TRIGGER IF EXISTS trigger_lineage_updated ON artifact_lineage")
        cursor.execute("""
            CREATE TRIGGER trigger_lineage_updated
            BEFORE UPDATE ON artifact_lineage
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """)
        conn.commit()
        print("   ✅ Trigger created")

        print("\n🎉 artifact_lineage migration complete!")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run()
