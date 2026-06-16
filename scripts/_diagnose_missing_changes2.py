"""Deeper diagnostic: find the latest scan, find what flipped pending → superseded."""
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

with conn.cursor(cursor_factory=RealDictCursor) as cur:
    print("=== ALL scan runs ever (no limit) ===")
    cur.execute("""
        SELECT id, status, pages_scanned, pages_changed, changes_detected,
               started_at, completed_at
          FROM jira_sync_runs
         ORDER BY started_at DESC
    """)
    runs = cur.fetchall()
    for r in runs:
        print(f"  {r['started_at']}  status={r['status']:9s}  "
              f"scanned={r['pages_scanned']} changed={r['pages_changed']} "
              f"detected={r['changes_detected']}  run={r['id']}")
    print(f"\nTotal runs: {len(runs)}\n")

    print("=== ALL pending_changes — when did they enter status=superseded? ===")
    # We can't trace exactly when status flipped (no separate event log),
    # but we can show the latest scan_run_id per row + when it was created.
    cur.execute("""
        SELECT pc.scan_run_id,
               pc.status,
               count(*) as n,
               min(pc.detected_at) as first_seen,
               max(pc.detected_at) as last_seen
          FROM pending_changes pc
         GROUP BY pc.scan_run_id, pc.status
         ORDER BY max(pc.detected_at) DESC
    """)
    for r in cur.fetchall():
        print(f"  born_in_scan={r['scan_run_id']}  status={r['status']:10s}  "
              f"n={r['n']}  detected={r['first_seen']}…{r['last_seen']}")

    print("\n=== Most recent scan: was it 'complete' AND does it have rows? ===")
    cur.execute("""
        SELECT * FROM jira_sync_runs
         ORDER BY started_at DESC LIMIT 1
    """)
    latest = cur.fetchone()
    if latest:
        print(f"  Latest run: {latest['id']}")
        print(f"  status={latest['status']}  completed_at={latest['completed_at']}")
        print(f"  pages_scanned={latest['pages_scanned']} pages_changed={latest['pages_changed']}")
        print(f"  changes_detected={latest['changes_detected']}")

        cur.execute("""
            SELECT requirement_id, status, detected_at
              FROM pending_changes
             WHERE scan_run_id = %s
             ORDER BY detected_at
        """, (latest['id'],))
        rows = cur.fetchall()
        print(f"\n  Pending_changes rows for this scan: {len(rows)}")
        for r in rows:
            print(f"    {r['requirement_id']}  status={r['status']}")

    print("\n=== HYPOTHESIS CHECK — does updated_at on superseded rows reveal recent flip? ===")
    # pending_changes doesn't have updated_at, so we'll check if there's any
    # status=superseded row WHOSE scan_run_id IS the latest scan id (which
    # would mean the latest scan's own rows got self-superseded — the bug).
    cur.execute("""
        WITH latest AS (
            SELECT id FROM jira_sync_runs ORDER BY started_at DESC LIMIT 1
        )
        SELECT count(*) AS self_superseded
          FROM pending_changes
         WHERE scan_run_id = (SELECT id FROM latest)
           AND status = 'superseded'
    """)
    n = cur.fetchone()['self_superseded']
    if n > 0:
        print(f"  ⚠ BUG: {n} rows belong to the LATEST scan AND are 'superseded' — "
              f"something flipped them AFTER the latest scan owned them.")
    else:
        print(f"  Latest scan's rows are NOT self-superseded.")

conn.close()
