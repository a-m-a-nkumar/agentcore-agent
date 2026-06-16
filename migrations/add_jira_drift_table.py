"""
Migration: Jira Sync drift table
=================================
Creates the table that powers the "Drift to resolve" half of the Jira Sync
Pulse — the REVERSE direction, where a Jira story was edited by hand and now
diverges from the BRD requirement it was generated from.

  jira_drift_items — one row per (Jira story × source requirement) that has
                     drifted. Persists across scans and carries its own
                     resolution lifecycle (open -> resolved/accepted), so an
                     "accept / stop flagging" decision survives future scans.

Keys on workspace_key (like the rest of Jira Sync) so users mapped to the same
Confluence space + Jira project share drift results.

Run AFTER:
  - add_artifact_lineage.py
  - add_workspace_key_to_lineage.py
  - add_jira_sync_tables.py   (provides update_updated_at_column())
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

        # ── 1. jira_drift_items
        print("[1/4] Creating jira_drift_items table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jira_drift_items (
                id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_key           VARCHAR(64)  NOT NULL,
                lineage_id              UUID,
                source                  VARCHAR(20)  NOT NULL DEFAULT 'JIRA',
                target_type             VARCHAR(50)  NOT NULL,
                target_id               VARCHAR(255) NOT NULL,
                source_page_id          VARCHAR(255),
                requirement_id          VARCHAR(50)  NOT NULL,

                -- display: BRD requirement text vs current artifact text
                source_text             TEXT,
                current_text            TEXT,
                title                   TEXT,
                summary                 TEXT,

                -- detection: the snapshot we last knew in-sync vs live, + hash
                baseline_content        JSONB        NOT NULL DEFAULT '{}'::jsonb,
                current_snapshot        JSONB        NOT NULL DEFAULT '{}'::jsonb,
                current_hash            VARCHAR(64),

                -- who/when edited (from the Jira changelog)
                edited_by               VARCHAR(255),
                edited_at               TIMESTAMP WITH TIME ZONE,

                -- lifecycle
                status                  VARCHAR(20)  NOT NULL DEFAULT 'open',
                resolution              VARCHAR(20),
                resolution_note         TEXT,
                proposed_brd_amendment  JSONB,
                resolved_by_user_id     VARCHAR(255),
                resolved_at             TIMESTAMP WITH TIME ZONE,

                detected_at             TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_scan_run_id        UUID,
                updated_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

                CONSTRAINT fk_drift_lineage FOREIGN KEY (lineage_id)
                    REFERENCES artifact_lineage(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
        print("   ok")

        # ── 1b. Comment-driven drift columns (idempotent add-ons).
        #   drift_kind: 'field' (snapshot edit) | 'comment' (LLM judged a comment)
        print("[1b/4] Adding comment-drift columns...")
        for col_ddl in (
            "drift_kind VARCHAR(20) NOT NULL DEFAULT 'field'",
            "comment_excerpt TEXT",
            "last_comment_at TIMESTAMP WITH TIME ZONE",
            "proposed_story_update JSONB",
        ):
            cursor.execute(f"ALTER TABLE jira_drift_items ADD COLUMN IF NOT EXISTS {col_ddl}")
        conn.commit()
        print("   ok")

        # ── 2. Dedup key: one open row per (workspace, artifact, requirement, kind)
        #   edge — so a story can carry a field-edit drift AND a comment drift.
        print("[2/4] Creating dedup unique index...")
        cursor.execute("DROP INDEX IF EXISTS uq_drift_edge")
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_drift_edge
            ON jira_drift_items (workspace_key, target_type, target_id, requirement_id, drift_kind)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_drift_workspace_status
            ON jira_drift_items (workspace_key, status)
        """)
        conn.commit()
        print("   ok")

        # ── 2b. Concurrency guard: at most one 'running' scan per workspace, so
        #   two near-simultaneous /scan requests can't both run the pipeline.
        print("[2b/4] Enforcing one running scan per workspace...")
        # Retire duplicate 'running' rows (keep newest per workspace) so the
        # partial unique index can be built.
        cursor.execute("""
            UPDATE jira_sync_runs
               SET status = 'failed',
                   message = COALESCE(message, 'retired by concurrency guard'),
                   completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
             WHERE status = 'running'
               AND id NOT IN (
                   SELECT DISTINCT ON (workspace_key) id
                     FROM jira_sync_runs
                    WHERE status = 'running'
                    ORDER BY workspace_key, started_at DESC
               )
        """)
        conn.commit()
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_one_running_scan_per_ws
            ON jira_sync_runs (workspace_key) WHERE status = 'running'
        """)
        conn.commit()
        print("   ok")

        # ── 2c. Per-page scan watermark: skip re-diffing a Confluence page whose
        #   version hasn't advanced since the last scan (saves the expensive LLM
        #   diff call — a comment-only change shouldn't re-diff the whole BRD).
        print("[2c/4] Creating jira_sync_page_scan watermark table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jira_sync_page_scan (
                workspace_key   VARCHAR(64)  NOT NULL,
                page_id         VARCHAR(255) NOT NULL,
                scanned_version INTEGER      NOT NULL,
                updated_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (workspace_key, page_id)
            )
        """)
        conn.commit()
        print("   ok")

        # ── 3. updated_at trigger (reuses the function from add_jira_sync_tables.py)
        print("[3/4] Creating updated_at trigger...")
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        cursor.execute("DROP TRIGGER IF EXISTS trg_drift_updated_at ON jira_drift_items")
        cursor.execute("""
            CREATE TRIGGER trg_drift_updated_at
            BEFORE UPDATE ON jira_drift_items
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """)
        conn.commit()
        print("   ok")

        # ── 4. Sanity print
        print("[4/4] Verifying table exists...")
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = %s",
            ("jira_drift_items",),
        )
        count = cursor.fetchone()[0]
        print(f"   jira_drift_items: {'OK' if count == 1 else 'MISSING'}")

        print("\nMigration complete.")

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
