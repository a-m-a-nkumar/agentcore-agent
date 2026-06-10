"""Inspect existing pending_changes duplicates before cleaning."""
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
    cur.execute("""
        SELECT workspace_key,
               source_page_id,
               requirement_id,
               count(*)         AS rows,
               count(*) FILTER (WHERE status = 'pending')    AS pending_rows,
               count(*) FILTER (WHERE status = 'superseded') AS superseded_rows,
               count(*) FILTER (WHERE status = 'applied')    AS applied_rows,
               count(*) FILTER (WHERE status = 'dismissed')  AS dismissed_rows
          FROM pending_changes
         GROUP BY workspace_key, source_page_id, requirement_id
         HAVING count(*) FILTER (WHERE status = 'pending') > 1
         ORDER BY pending_rows DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("No duplicate 'pending' rows found.")
    else:
        print(f"Found {len(rows)} (workspace, page, requirement) groups with >1 pending row:")
        for r in rows:
            print(
                f"  ws={r['workspace_key'][:12]}… page={r['source_page_id']} "
                f"req={r['requirement_id']} : pending={r['pending_rows']} "
                f"superseded={r['superseded_rows']} applied={r['applied_rows']} "
                f"dismissed={r['dismissed_rows']}"
            )

    cur.execute("""
        SELECT count(*) FILTER (WHERE status = 'pending')    AS pending,
               count(*) FILTER (WHERE status = 'superseded') AS superseded,
               count(*) FILTER (WHERE status = 'applied')    AS applied,
               count(*) FILTER (WHERE status = 'dismissed')  AS dismissed,
               count(*) AS total
          FROM pending_changes
    """)
    totals = cur.fetchone()
    print("\nOverall pending_changes counts:")
    print(f"  pending   : {totals['pending']}")
    print(f"  superseded: {totals['superseded']}")
    print(f"  applied   : {totals['applied']}")
    print(f"  dismissed : {totals['dismissed']}")
    print(f"  total     : {totals['total']}")

conn.close()
