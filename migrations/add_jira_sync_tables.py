"""
Migration: Jira Sync staging tables
====================================
Creates three new tables that power the "Changes to apply" half of the
Jira Sync Pulse:

  jira_sync_runs            — one row per scan; lets the UI poll progress
  pending_changes           — one row per changed BRD requirement
  proposed_artifact_updates — one row per downstream Jira/test artifact proposal

All three key on workspace_key (not project_id) so users mapped to the same
Confluence space + Jira project share scan results.

Run AFTER:
  - add_artifact_lineage.py
  - add_workspace_key_to_lineage.py
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

        # ── 1. jira_sync_runs
        print("[1/6] Creating jira_sync_runs table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jira_sync_runs (
                id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_key               VARCHAR(64)  NOT NULL,
                triggered_by_user_id        VARCHAR(255) NOT NULL,
                triggered_by_project_id     VARCHAR(255) NOT NULL,
                status                      VARCHAR(20)  NOT NULL DEFAULT 'running',
                message                     TEXT,
                pages_scanned               INTEGER      NOT NULL DEFAULT 0,
                pages_changed               INTEGER      NOT NULL DEFAULT 0,
                changes_detected            INTEGER      NOT NULL DEFAULT 0,
                started_at                  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at                TIMESTAMP WITH TIME ZONE,

                CONSTRAINT fk_run_user FOREIGN KEY (triggered_by_user_id)
                    REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_run_project FOREIGN KEY (triggered_by_project_id)
                    REFERENCES projects(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sync_runs_workspace_latest
            ON jira_sync_runs (workspace_key, started_at DESC)
        """)
        conn.commit()
        print("   ok")

        # ── 2. pending_changes
        print("[2/6] Creating pending_changes table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_changes (
                id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_key               VARCHAR(64)  NOT NULL,
                scan_run_id                 UUID         NOT NULL,
                source_page_id              VARCHAR(255) NOT NULL,
                requirement_id              VARCHAR(50)  NOT NULL,
                severity                    VARCHAR(20)  NOT NULL,
                summary                     TEXT         NOT NULL,
                old_text                    TEXT,
                new_text                    TEXT,
                source_version_from         INTEGER,
                source_version_to           INTEGER,
                artifacts_affected          INTEGER      NOT NULL DEFAULT 0,
                status                      VARCHAR(20)  NOT NULL DEFAULT 'pending',
                detected_at                 TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

                CONSTRAINT fk_pending_run FOREIGN KEY (scan_run_id)
                    REFERENCES jira_sync_runs(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_workspace_status
            ON pending_changes (workspace_key, status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_scan
            ON pending_changes (scan_run_id)
        """)
        conn.commit()
        print("   ok")

        # ── 3. proposed_artifact_updates
        print("[3/6] Creating proposed_artifact_updates table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS proposed_artifact_updates (
                id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                pending_change_id           UUID         NOT NULL,
                workspace_key               VARCHAR(64)  NOT NULL,
                lineage_id                  UUID,
                target_type                 VARCHAR(50)  NOT NULL,
                target_id                   VARCHAR(255) NOT NULL,
                action                      VARCHAR(40)  NOT NULL,
                current_snapshot            JSONB        NOT NULL DEFAULT '{}'::jsonb,
                proposed_snapshot           JSONB,
                confidence                  NUMERIC(3,2),
                rationale                   TEXT,
                decision                    VARCHAR(20)  NOT NULL DEFAULT 'pending',
                decided_by_user_id          VARCHAR(255),
                decided_at                  TIMESTAMP WITH TIME ZONE,
                applied_at                  TIMESTAMP WITH TIME ZONE,
                applied_by_user_id          VARCHAR(255),
                apply_error                 TEXT,

                CONSTRAINT fk_proposal_change FOREIGN KEY (pending_change_id)
                    REFERENCES pending_changes(id) ON DELETE CASCADE,
                CONSTRAINT fk_proposal_lineage FOREIGN KEY (lineage_id)
                    REFERENCES artifact_lineage(id) ON DELETE SET NULL
            )
        """)
        conn.commit()
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_proposal_change
            ON proposed_artifact_updates (pending_change_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_proposal_workspace_decision
            ON proposed_artifact_updates (workspace_key, decision)
        """)
        conn.commit()
        print("   ok")

        # ── 4. Concurrency guard: a proposal can only be applied once.
        # Partial unique index on rows that have applied_at set.
        print("[4/6] Creating concurrency guard index...")
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_proposal_applied_once
            ON proposed_artifact_updates (id)
            WHERE applied_at IS NOT NULL
        """)
        conn.commit()
        print("   ok")

        # ── 5. updated_at triggers (reuse function from setup_core_tables)
        print("[5/6] Creating updated_at triggers...")
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        conn.commit()
        # Only jira_sync_runs has a logically updated_at usage (status/completed_at),
        # but to keep the schema small we don't add updated_at columns to the
        # other two tables — they're append-mostly with explicit state columns
        # (decision / applied_at) tracked directly.
        print("   ok")

        # ── 6. Sanity print
        print("[6/6] Verifying tables exist...")
        for tbl in ("jira_sync_runs", "pending_changes", "proposed_artifact_updates"):
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = %s",
                (tbl,),
            )
            count = cursor.fetchone()[0]
            print(f"   {tbl}: {'OK' if count == 1 else 'MISSING'}")

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
