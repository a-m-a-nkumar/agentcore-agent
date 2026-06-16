"""Ad-hoc query: find the most recent BRD page used to generate Jira stories."""
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
    print("=== Most recent jira_story lineage rows ===")
    cur.execute(
        """
        SELECT al.created_at,
               al.source_id           AS brd_page_id,
               al.source_section_id   AS requirement,
               al.source_version,
               al.target_id           AS jira_key,
               al.project_id,
               al.workspace_key,
               cp.title               AS brd_title,
               cp.space_key           AS confluence_space,
               p.project_name,
               p.jira_project_key
          FROM artifact_lineage al
          LEFT JOIN confluence_pages cp ON cp.page_id = al.source_id
          LEFT JOIN projects p ON p.id = al.project_id
         WHERE al.target_type = 'jira_story'
         ORDER BY al.created_at DESC
         LIMIT 10
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("(no jira_story lineage rows found)")
    for r in rows:
        print("---")
        print(f"created_at      : {r['created_at']}")
        print(f"brd_page_id     : {r['brd_page_id']}")
        print(f"brd_title       : {r['brd_title']}")
        print(f"confluence_space: {r['confluence_space']}")
        print(f"requirement     : {r['requirement']}  (source_version={r['source_version']})")
        print(f"-> jira_key     : {r['jira_key']}  (jira_project_key={r['jira_project_key']})")
        print(f"project         : {r['project_name']} ({r['project_id']})")
        print(f"workspace_key   : {r['workspace_key']}")

    print()
    print("=== Distinct BRD pages that have generated Jira stories (most recent first) ===")
    cur.execute(
        """
        SELECT al.source_id           AS brd_page_id,
               cp.title               AS brd_title,
               cp.space_key,
               max(al.source_version) AS latest_source_version,
               max(al.created_at)     AS latest_use,
               count(*)               AS stories_generated,
               array_agg(DISTINCT al.source_section_id ORDER BY al.source_section_id) AS requirements
          FROM artifact_lineage al
          LEFT JOIN confluence_pages cp ON cp.page_id = al.source_id
         WHERE al.target_type = 'jira_story'
         GROUP BY al.source_id, cp.title, cp.space_key
         ORDER BY max(al.created_at) DESC
         LIMIT 5
        """
    )
    for r in cur.fetchall():
        title = r["brd_title"] or "(unknown title)"
        print(f"- {title}")
        print(f"    page_id={r['brd_page_id']} space={r['space_key']} v{r['latest_source_version']}")
        print(f"    stories_generated={r['stories_generated']} last_used={r['latest_use']}")
        print(f"    requirements={r['requirements']}")

conn.close()
