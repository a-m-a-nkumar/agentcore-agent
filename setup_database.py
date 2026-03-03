"""
╔══════════════════════════════════════════════════════════════╗
║          AgentCore — Full Database Setup Script              ║
║          Run this ONCE on a brand-new environment            ║
╚══════════════════════════════════════════════════════════════╝

Runs all migrations in the correct order:

  Step 1 → enable_pgvector.py       : Enable pgvector extension
  Step 2 → setup_core_tables.py     : Create users, projects, analyst_sessions
  Step 3 → add_vector_tables.py     : Create confluence_pages, jira_issues, document_embeddings + HNSW index
  Step 4 → add_dedup_hash_index.py  : Upgrade dedup index to 4-column (content_hash included)

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
from migrations.enable_pgvector import enable_pgvector as step_enable_pgvector
from migrations.setup_core_tables import run as step_core_tables
from migrations.add_vector_tables import run_migration as step_vector_tables
from migrations.add_dedup_hash_index import run as step_dedup_index


STEPS = [
    ("Step 1/4", "Enable pgvector extension",                    step_enable_pgvector),
    ("Step 2/4", "Create core tables (users/projects/sessions)",  step_core_tables),
    ("Step 3/4", "Create vector tables + HNSW index",            step_vector_tables),
    ("Step 4/4", "Upgrade dedup index to 4-column composite",    step_dedup_index),
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
