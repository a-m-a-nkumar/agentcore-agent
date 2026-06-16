"""
Recovery: restore the latest completed scan's pending_changes from
'superseded' back to 'pending', so the UI surfaces them again.

Run after the concurrent-supersede bug fix has been deployed. Idempotent.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(
    host=os.getenv("DATABASE_HOST"),
    port=os.getenv("DATABASE_PORT", "5432"),
    database=os.getenv("DATABASE_NAME"),
    user=os.getenv("DATABASE_USER"),
    password=os.getenv("DATABASE_PASSWORD"),
)

try:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # For each workspace, find the latest complete scan_run.
        cur.execute(
            """
            SELECT DISTINCT ON (workspace_key)
                   workspace_key, id AS scan_run_id, completed_at
              FROM jira_sync_runs
             WHERE status = 'complete'
             ORDER BY workspace_key, completed_at DESC
            """
        )
        latest_per_ws = cur.fetchall()

        if not latest_per_ws:
            print("No complete scans found — nothing to recover.")
        else:
            for ws_row in latest_per_ws:
                ws = ws_row["workspace_key"]
                run_id = ws_row["scan_run_id"]
                print(
                    f"Workspace {ws[:12]}…  latest scan {run_id} "
                    f"completed={ws_row['completed_at']}"
                )

                # Restore pending_changes from this scan that got
                # incorrectly flipped to superseded.
                cur.execute(
                    """
                    UPDATE pending_changes
                       SET status = 'pending'
                     WHERE workspace_key = %s
                       AND scan_run_id = %s
                       AND status = 'superseded'
                    """,
                    (ws, run_id),
                )
                restored = cur.rowcount
                print(f"   restored {restored} pending_change row(s)")

            conn.commit()
            print("\nDone.")

        # Final counts
        cur.execute(
            """
            SELECT status, count(*) FROM pending_changes
             GROUP BY status ORDER BY status
            """
        )
        print("\nFinal status counts:")
        for r in cur.fetchall():
            print(f"  {r['status']:12s}: {r['count']}")
except Exception as e:
    conn.rollback()
    print(f"Recovery failed: {e}")
    raise
finally:
    conn.close()
