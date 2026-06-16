"""Diagnose 'pending changes vanished' — show recent scan runs, status counts,
and what got superseded most recently."""
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
    print("=== Recent scan runs ===")
    cur.execute("""
        SELECT id, workspace_key, status, pages_scanned, pages_changed,
               changes_detected, started_at, completed_at, message
          FROM jira_sync_runs
         ORDER BY started_at DESC
         LIMIT 8
    """)
    for r in cur.fetchall():
        print(f"  {r['started_at']}  status={r['status']:9s}  "
              f"pages_scanned={r['pages_scanned']} pages_changed={r['pages_changed']} "
              f"changes_detected={r['changes_detected']}")
        print(f"     run_id={r['id']}  ws={r['workspace_key'][:12]}…")
        if r['message']:
            print(f"     message={r['message']}")

    print("\n=== Status counts (current state) ===")
    cur.execute("""
        SELECT status, count(*) FROM pending_changes GROUP BY status ORDER BY status
    """)
    for r in cur.fetchall():
        print(f"  {r['status']:12s}: {r['count']}")

    print("\n=== Pending changes by scan_run, most recent first ===")
    cur.execute("""
        SELECT pc.scan_run_id,
               pc.status,
               count(*)              AS n,
               max(pc.detected_at)   AS latest,
               array_agg(pc.requirement_id ORDER BY pc.requirement_id) AS reqs
          FROM pending_changes pc
         GROUP BY pc.scan_run_id, pc.status
         ORDER BY max(pc.detected_at) DESC
         LIMIT 10
    """)
    for r in cur.fetchall():
        reqs = ", ".join(r["reqs"]) if r["reqs"] else "—"
        print(f"  scan_run={r['scan_run_id']}  status={r['status']:10s}  n={r['n']}  reqs=[{reqs}]")

    print("\n=== Most recent 5 rows that got flipped to 'superseded' ===")
    cur.execute("""
        SELECT id, workspace_key, source_page_id, requirement_id, status,
               scan_run_id, detected_at
          FROM pending_changes
         WHERE status = 'superseded'
         ORDER BY detected_at DESC
         LIMIT 5
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (none)")
    for r in rows:
        print(f"  req={r['requirement_id']:6s} page={r['source_page_id']} "
              f"superseded_from_scan={r['scan_run_id']}")

conn.close()
