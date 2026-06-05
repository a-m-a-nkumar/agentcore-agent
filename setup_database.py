"""
╔══════════════════════════════════════════════════════════════╗
║          AgentCore — Full Database Setup Script              ║
║          Run this ONCE on a brand-new environment            ║
║          (also safe to re-run — every step is idempotent)    ║
╚══════════════════════════════════════════════════════════════╝

Runs all migrations in the correct order. Every step is idempotent
(IF NOT EXISTS clauses), so re-running on an env that already has some
of these objects will only apply the gaps.

  Step  1 → enable_pgvector.py                Enable pgvector extension
  Step  2 → setup_core_tables.py              Create users, projects, analyst_sessions
  Step  3 → add_token_usage_column.py         Add users.token_usage (per-user LLM token counter)
  Step  4 → add_access_role_column.py         Add users.access_role (BOTH/TECH/BUSINESS/NONE)
  Step  5 → add_user_module_activity_table.py Create user_module_activity (org-usage event log)
  Step  6 → add_vector_tables.py              Create confluence_pages, jira_issues, document_embeddings + HNSW
  Step  7 → add_dedup_hash_index.py           Upgrade dedup index to 4-column (content_hash)
  Step  8 → add_fulltext_search.py            Add content_tsvector to document_embeddings
  Step  9 → add_atlassian_credentials.py      Add Atlassian integration columns to users
  Step 10 → add_figma_credentials.py          Add Figma credentials column to users
  Step 11 → add_lucid_credentials.py          Add Lucid credentials columns to users
  Step 12 → add_brd_session_columns.py        Add BRD session columns to projects
  Step 13 → add_brd_feedback.py               Create brd_feedback table
  Step 14 → add_artifact_lineage.py           Create artifact_lineage table
  Step 15 → add_design_sessions.py            Create design_sessions table
  Step 16 → add_design_diagram_slots.py       Add diagram_slots + authoring_tool to design_sessions

Prerequisites:
  - PostgreSQL 13+ running and accessible
  - pgvector extension available on the server (AWS RDS Aurora Postgres supports it by default)
  - .env file configured with DATABASE_HOST, PORT, NAME, USER, PASSWORD
  - pip install -r requirements.txt done

Usage:
  python setup_database.py
"""

import sys
import os

# Make sure we run from the project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# ── Validate .env before starting ────────────────────────────────────────────
REQUIRED_VARS = ["DATABASE_HOST", "DATABASE_NAME", "DATABASE_USER", "DATABASE_PASSWORD"]
missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
if missing:
    print("❌ Missing required environment variables in .env:")
    for v in missing:
        print(f"   - {v}")
    print("\nPlease configure your .env file before running setup.")
    sys.exit(1)

print("=" * 62)
print("  AgentCore — Full Database Setup")
print("=" * 62)
print(f"  Host     : {os.getenv('DATABASE_HOST')}")
print(f"  Database : {os.getenv('DATABASE_NAME')}")
print(f"  User     : {os.getenv('DATABASE_USER')}")
print("=" * 62)
print()

# ── Import all migration modules ──────────────────────────────────────────────
from migrations.enable_pgvector            import enable_pgvector       as step_enable_pgvector
from migrations.setup_core_tables          import run                   as step_core_tables
from migrations.add_token_usage_column     import run                   as step_token_usage
from migrations.add_access_role_column     import run                   as step_access_role
from migrations.add_user_module_activity_table import run               as step_user_module_activity
from migrations.add_vector_tables          import run_migration         as step_vector_tables
from migrations.add_dedup_hash_index       import run                   as step_dedup_index
from migrations.add_fulltext_search        import run                   as step_fulltext_search
from migrations.add_atlassian_credentials  import add_atlassian_columns as step_atlassian_credentials
from migrations.add_figma_credentials      import run                   as step_figma_credentials
from migrations.add_lucid_credentials      import add_lucid_columns     as step_lucid_credentials
from migrations.add_brd_session_columns    import run                   as step_brd_session_columns
from migrations.add_brd_feedback           import run                   as step_brd_feedback
from migrations.add_artifact_lineage       import run                   as step_artifact_lineage
from migrations.add_design_sessions        import run                   as step_design_sessions
from migrations.add_design_diagram_slots   import run                   as step_design_diagram_slots


STEPS = [
    ("Step  1/16", "Enable pgvector extension",                            step_enable_pgvector),
    ("Step  2/16", "Create core tables (users / projects / analyst_sessions)", step_core_tables),
    ("Step  3/16", "Add users.token_usage (LLM token counter)",            step_token_usage),
    ("Step  4/16", "Add users.access_role (BOTH/TECH/BUSINESS/NONE)",      step_access_role),
    ("Step  5/16", "Create user_module_activity (event log)",              step_user_module_activity),
    ("Step  6/16", "Create vector tables + HNSW index",                    step_vector_tables),
    ("Step  7/16", "Upgrade dedup index to 4-column composite",            step_dedup_index),
    ("Step  8/16", "Add document_embeddings.content_tsvector (fulltext)",  step_fulltext_search),
    ("Step  9/16", "Add Atlassian integration columns",                    step_atlassian_credentials),
    ("Step 10/16", "Add Figma credentials column",                         step_figma_credentials),
    ("Step 11/16", "Add Lucid credentials columns",                        step_lucid_credentials),
    ("Step 12/16", "Add BRD session columns to projects",                  step_brd_session_columns),
    ("Step 13/16", "Create brd_feedback table",                            step_brd_feedback),
    ("Step 14/16", "Create artifact_lineage table",                        step_artifact_lineage),
    ("Step 15/16", "Create design_sessions table",                         step_design_sessions),
    ("Step 16/16", "Add diagram_slots + authoring_tool columns",           step_design_diagram_slots),
]

passed = []
failed = []

for step_label, description, fn in STEPS:
    print()
    print(f"{'─' * 62}")
    print(f"  {step_label}: {description}")
    print(f"{'─' * 62}")
    try:
        fn()
        passed.append(description)
        print(f"\n  ✅ {step_label} PASSED")
    except Exception as e:
        failed.append((description, str(e)))
        print(f"\n  ❌ {step_label} FAILED: {e}")
        print("\n  ⚠️  Setup aborted. Fix the error above and re-run.")
        print("  (All completed steps are idempotent — safe to re-run)")
        sys.exit(1)

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 62)
print("  🎉 DATABASE SETUP COMPLETE!")
print("=" * 62)
for step in passed:
    print(f"  ✅ {step}")
print()
print("  Your database is ready. Start the backend with:")
print("  .\\START_BACKEND.ps1")
print("=" * 62)
