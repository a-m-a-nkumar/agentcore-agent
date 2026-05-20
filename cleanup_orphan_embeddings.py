"""
One-shot orphan-page cleanup for the RAG vector DB.

CONTEXT
-------
Earlier versions of sync_service.py deleted old embeddings BEFORE generating
new ones, then upserted the metadata row. If embedding generation failed
mid-page (Bedrock 429, gateway timeout, network blip), the page ended up with:

  - a confluence_pages / jira_issues row that says "synced at version N"
  - zero rows in document_embeddings for that source_id

Because subsequent syncs check the metadata row first and skip pages whose
version_number already matches Confluence, these "orphans" never get
re-embedded automatically — they're invisible to RAG forever.

The post-fix sync code does NOT create new orphans (embeddings are generated
in memory first, then swapped in, with the metadata upsert happening last).
But existing orphans from past sync failures need to be cleaned up once.

WHAT THIS SCRIPT DOES
---------------------
1. Finds every confluence_pages / jira_issues row with NO matching
   document_embeddings rows.
2. In --dry-run mode (default): prints the orphans and a count, makes no
   changes. SAFE TO RUN ANY TIME.
3. With --apply: DELETES the orphan metadata rows. Next sync will then see
   those pages/issues as "new" and regenerate embeddings from scratch.

USAGE
-----
  python cleanup_orphan_embeddings.py                 # dry-run, all projects
  python cleanup_orphan_embeddings.py --apply         # actually delete orphans
  python cleanup_orphan_embeddings.py --project <id>  # scope to one project
  python cleanup_orphan_embeddings.py --source confluence --apply
"""

import argparse
import os
import sys
from typing import List, Tuple

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional


def get_conn():
    """Connect using the same env vars as the rest of the backend."""
    host = os.getenv("DATABASE_HOST") or os.getenv("RDS_HOST")
    port = os.getenv("DATABASE_PORT") or os.getenv("RDS_PORT", "5432")
    database = os.getenv("DATABASE_NAME") or os.getenv("RDS_DATABASE", "postgres")
    user = os.getenv("DATABASE_USER") or os.getenv("RDS_USER", "postgres")
    password = os.getenv("DATABASE_PASSWORD", "")

    if not host:
        print("ERROR: DATABASE_HOST (or RDS_HOST) not set in environment.")
        sys.exit(1)

    return psycopg2.connect(
        host=host, port=port, database=database,
        user=user, password=password, connect_timeout=10,
    )


# ------------------------------------------------------------------
# Orphan detection queries
# ------------------------------------------------------------------

# Confluence: rows in confluence_pages with zero matching embeddings.
SQL_CONFLUENCE_ORPHANS = """
    SELECT cp.project_id, cp.page_id, cp.title, cp.version_number, cp.updated_at
    FROM confluence_pages cp
    LEFT JOIN document_embeddings de
      ON de.source_id  = cp.page_id
     AND de.source_type = 'confluence'
     AND de.project_id  = cp.project_id
    WHERE de.id IS NULL
      {project_filter}
    ORDER BY cp.project_id, cp.page_id;
"""

# Jira: rows in jira_issues with zero matching embeddings.
SQL_JIRA_ORPHANS = """
    SELECT ji.project_id, ji.issue_key, ji.summary, ji.updated_at
    FROM jira_issues ji
    LEFT JOIN document_embeddings de
      ON de.source_id  = ji.issue_key
     AND de.source_type = 'jira'
     AND de.project_id  = ji.project_id
    WHERE de.id IS NULL
      {project_filter}
    ORDER BY ji.project_id, ji.issue_key;
"""

# Delete statements — only run with --apply.
SQL_DELETE_CONFLUENCE_ORPHANS = """
    DELETE FROM confluence_pages cp
    USING (
        SELECT cp2.project_id, cp2.page_id
        FROM confluence_pages cp2
        LEFT JOIN document_embeddings de
          ON de.source_id  = cp2.page_id
         AND de.source_type = 'confluence'
         AND de.project_id  = cp2.project_id
        WHERE de.id IS NULL
          {project_filter}
    ) orphans
    WHERE cp.project_id = orphans.project_id
      AND cp.page_id    = orphans.page_id;
"""

SQL_DELETE_JIRA_ORPHANS = """
    DELETE FROM jira_issues ji
    USING (
        SELECT ji2.project_id, ji2.issue_key
        FROM jira_issues ji2
        LEFT JOIN document_embeddings de
          ON de.source_id  = ji2.issue_key
         AND de.source_type = 'jira'
         AND de.project_id  = ji2.project_id
        WHERE de.id IS NULL
          {project_filter}
    ) orphans
    WHERE ji.project_id = orphans.project_id
      AND ji.issue_key  = orphans.issue_key;
"""


def find_orphans(conn, source: str, project_id: str = None):
    """Return list of orphan rows for the given source ('confluence' or 'jira')."""
    if source == "confluence":
        sql = SQL_CONFLUENCE_ORPHANS
        prefix = "cp"
    else:
        sql = SQL_JIRA_ORPHANS
        prefix = "ji"

    if project_id:
        project_filter = f"AND {prefix}.project_id = %s"
        params: Tuple = (project_id,)
    else:
        project_filter = ""
        params = ()

    sql_filled = sql.format(project_filter=project_filter)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql_filled, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def delete_orphans(conn, source: str, project_id: str = None) -> int:
    """Delete orphan rows and return the number deleted."""
    if source == "confluence":
        sql = SQL_DELETE_CONFLUENCE_ORPHANS
    else:
        sql = SQL_DELETE_JIRA_ORPHANS

    if project_id:
        project_filter = (
            "AND cp2.project_id = %s" if source == "confluence" else "AND ji2.project_id = %s"
        )
        params: Tuple = (project_id,)
    else:
        project_filter = ""
        params = ()

    sql_filled = sql.format(project_filter=project_filter)
    cur = conn.cursor()
    cur.execute(sql_filled, params)
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    return deleted


def print_orphan_report(source: str, rows: List[dict]):
    print(f"\n{'=' * 70}")
    print(f"  {source.upper()} ORPHANS — {len(rows)} found")
    print(f"{'=' * 70}")

    if not rows:
        print("  (none)")
        return

    # group by project_id for readability
    by_project: dict = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(r)

    for project_id, group in sorted(by_project.items()):
        print(f"\n  project_id = {project_id}  ({len(group)} orphans)")
        for r in group[:10]:  # preview first 10 per project
            if source == "confluence":
                print(f"    - page_id={r['page_id']:<12} v{r['version_number']}  title={r['title'][:60]}")
            else:
                print(f"    - issue={r['issue_key']:<15} summary={(r['summary'] or '')[:60]}")
        if len(group) > 10:
            print(f"    ... and {len(group) - 10} more")


def main():
    parser = argparse.ArgumentParser(description="Find and clean up orphan metadata rows.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete orphan rows. Without this flag, runs in dry-run mode.")
    parser.add_argument("--project", default=None,
                        help="Limit to a single project_id. Omit to scan all projects.")
    parser.add_argument("--source", choices=["confluence", "jira", "both"], default="both",
                        help="Which source type to check (default: both).")
    args = parser.parse_args()

    mode = "APPLY (will DELETE rows)" if args.apply else "DRY-RUN (no changes)"
    scope = f"project={args.project}" if args.project else "ALL projects"
    print(f"\nOrphan cleanup — mode: {mode}  |  scope: {scope}  |  source: {args.source}")

    conn = get_conn()

    sources_to_check = ["confluence", "jira"] if args.source == "both" else [args.source]
    total_found = 0
    total_deleted = 0

    try:
        for src in sources_to_check:
            rows = find_orphans(conn, src, args.project)
            print_orphan_report(src, rows)
            total_found += len(rows)

            if args.apply and rows:
                deleted = delete_orphans(conn, src, args.project)
                total_deleted += deleted
                print(f"\n  Deleted {deleted} {src} orphan rows.")

        print(f"\n{'=' * 70}")
        print(f"  TOTAL orphans found: {total_found}")
        if args.apply:
            print(f"  TOTAL rows deleted:  {total_deleted}")
            print(f"\n  Next step: ask affected users to trigger a sync.")
            print(f"  The previously-orphaned pages/issues will be detected as 'new'")
            print(f"  and their embeddings will be regenerated from scratch.")
        else:
            print(f"  DRY-RUN — no rows were modified.")
            print(f"\n  To delete orphan rows, re-run with --apply.")
        print(f"{'=' * 70}\n")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
