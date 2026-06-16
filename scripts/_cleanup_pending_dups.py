"""
One-off cleanup: collapse duplicate 'pending' pending_changes rows that were
created before the supersede-on-scan-completion fix landed.

Keeps the most recent row per (workspace_key, source_page_id, requirement_id)
and marks every older duplicate as 'superseded'. No deletes — every row is
preserved for audit.
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
        # Preview which rows would be flipped.
        cur.execute(
            """
            SELECT id, workspace_key, source_page_id, requirement_id, detected_at
              FROM (
                  SELECT id, workspace_key, source_page_id, requirement_id, detected_at,
                         row_number() OVER (
                             PARTITION BY workspace_key, source_page_id, requirement_id
                             ORDER BY detected_at DESC
                         ) AS rn
                    FROM pending_changes
                   WHERE status = 'pending'
              ) ranked
             WHERE rn > 1
             ORDER BY workspace_key, source_page_id, requirement_id, detected_at
            """
        )
        to_supersede = cur.fetchall()
        if not to_supersede:
            print("No duplicates to clean up.")
        else:
            print(f"Will supersede {len(to_supersede)} duplicate row(s):")
            for r in to_supersede:
                print(
                    f"  page={r['source_page_id']} req={r['requirement_id']} "
                    f"id={r['id']} detected_at={r['detected_at']}"
                )

            cur.execute(
                """
                UPDATE pending_changes
                   SET status = 'superseded'
                 WHERE id IN (
                     SELECT id FROM (
                         SELECT id,
                                row_number() OVER (
                                    PARTITION BY workspace_key, source_page_id, requirement_id
                                    ORDER BY detected_at DESC
                                ) AS rn
                           FROM pending_changes
                          WHERE status = 'pending'
                     ) ranked
                     WHERE rn > 1
                 )
                """
            )
            flipped = cur.rowcount
            conn.commit()
            print(f"\nSuperseded {flipped} row(s). Newest per (page, requirement) remains 'pending'.")

        # Post-cleanup totals
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'pending')    AS pending,
                   count(*) FILTER (WHERE status = 'superseded') AS superseded,
                   count(*) FILTER (WHERE status = 'applied')    AS applied,
                   count(*) FILTER (WHERE status = 'dismissed')  AS dismissed
              FROM pending_changes
            """
        )
        totals = cur.fetchone()
        print(
            f"\nAfter cleanup: pending={totals['pending']} "
            f"superseded={totals['superseded']} applied={totals['applied']} "
            f"dismissed={totals['dismissed']}"
        )
except Exception as e:
    conn.rollback()
    print(f"Cleanup failed: {e}")
    raise
finally:
    conn.close()
