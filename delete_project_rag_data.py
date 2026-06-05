"""
Delete all RAG-related data for a specific project_id so a fresh sync can be run.

WHAT IT TOUCHES (only for the given project_id):
  - document_embeddings   (all chunks + vectors)
  - confluence_pages      (all sync metadata)
  - jira_issues           (all sync metadata)

WHAT IT DOES NOT TOUCH:
  - projects              (the project itself stays; you can resync via the UI)
  - users / credentials   (untouched)
  - any other project's data

Two-phase: prints counts first (read-only), then deletes when --apply is passed.

USAGE:
  python delete_project_rag_data.py --project-id <uuid>           # dry run, no deletes
  python delete_project_rag_data.py --project-id <uuid> --apply   # actually delete
"""

import argparse
import os
import sys

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed.")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_conn():
    host = os.getenv("DATABASE_HOST") or os.getenv("RDS_HOST")
    port = os.getenv("DATABASE_PORT") or os.getenv("RDS_PORT", "5432")
    database = os.getenv("DATABASE_NAME") or os.getenv("RDS_DATABASE", "postgres")
    user = os.getenv("DATABASE_USER") or os.getenv("RDS_USER", "postgres")
    password = os.getenv("DATABASE_PASSWORD", "")
    if not host:
        print("ERROR: DATABASE_HOST not set in environment.")
        sys.exit(1)
    return psycopg2.connect(
        host=host, port=port, database=database,
        user=user, password=password, connect_timeout=10,
    )


def count_rows(conn, project_id: str) -> dict:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM document_embeddings WHERE project_id = %s) AS embeddings,
          (SELECT COUNT(*) FROM confluence_pages    WHERE project_id = %s) AS confluence_pages,
          (SELECT COUNT(*) FROM jira_issues         WHERE project_id = %s) AS jira_issues,
          (SELECT 1 FROM projects WHERE id = %s)                           AS project_exists
        """,
        (project_id, project_id, project_id, project_id),
    )
    return dict(cur.fetchone())


def delete_rows(conn, project_id: str) -> dict:
    """Run the three DELETEs in one transaction so we either remove all or none."""
    cur = conn.cursor()
    cur.execute("DELETE FROM document_embeddings WHERE project_id = %s", (project_id,))
    deleted_embeddings = cur.rowcount
    cur.execute("DELETE FROM confluence_pages    WHERE project_id = %s", (project_id,))
    deleted_confluence = cur.rowcount
    cur.execute("DELETE FROM jira_issues         WHERE project_id = %s", (project_id,))
    deleted_jira = cur.rowcount
    conn.commit()
    cur.close()
    return {
        "embeddings": deleted_embeddings,
        "confluence_pages": deleted_confluence,
        "jira_issues": deleted_jira,
    }


def main():
    parser = argparse.ArgumentParser(description="Delete all RAG data for a project_id.")
    parser.add_argument("--project-id", required=True, help="UUID of the project to wipe.")
    parser.add_argument("--apply", action="store_true", help="Actually delete. Without this flag, runs in dry-run mode.")
    args = parser.parse_args()

    mode = "APPLY (will DELETE rows)" if args.apply else "DRY-RUN (no changes)"
    print(f"\nMode: {mode}")
    print(f"Project ID: {args.project_id}\n")

    conn = get_conn()
    try:
        before = count_rows(conn, args.project_id)

        if not before["project_exists"]:
            print(f"WARNING: no row in 'projects' table with id={args.project_id}.")
            print("Proceeding anyway — this only affects RAG tables, not the projects table.\n")

        print("Current row counts for this project:")
        print(f"  document_embeddings : {before['embeddings']:>8}")
        print(f"  confluence_pages    : {before['confluence_pages']:>8}")
        print(f"  jira_issues         : {before['jira_issues']:>8}")

        if not args.apply:
            print("\nDRY-RUN — no rows were modified.")
            print("To delete the rows above, re-run with --apply.")
            return

        if not (before["embeddings"] or before["confluence_pages"] or before["jira_issues"]):
            print("\nNothing to delete. Exiting.")
            return

        print("\nDeleting...")
        deleted = delete_rows(conn, args.project_id)
        print(f"  document_embeddings : {deleted['embeddings']:>8} deleted")
        print(f"  confluence_pages    : {deleted['confluence_pages']:>8} deleted")
        print(f"  jira_issues         : {deleted['jira_issues']:>8} deleted")

        after = count_rows(conn, args.project_id)
        print("\nPost-delete counts:")
        print(f"  document_embeddings : {after['embeddings']:>8}")
        print(f"  confluence_pages    : {after['confluence_pages']:>8}")
        print(f"  jira_issues         : {after['jira_issues']:>8}")

        if any(after[k] != 0 for k in ("embeddings", "confluence_pages", "jira_issues")):
            print("\nWARNING: some rows remain. Investigate.")
        else:
            print("\nDone. Project is wiped and ready for a fresh sync.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
