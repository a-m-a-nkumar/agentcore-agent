# AgentCore — Database Setup Guide
### For new environment / company migration

---

## ⚡ Quick Start (One Command)

```bash
# From project root
python setup_database.py
```

That's it. This runs all 4 steps in order automatically.

---

## Prerequisites

Before running setup, ensure:

1. **PostgreSQL 13+** is running and network-accessible
2. **pgvector** is available on the server
   - AWS RDS Aurora Postgres → supported natively ✅
   - Self-hosted → install via `apt install postgresql-15-pgvector`
3. **`.env` file** is configured:

```env
DATABASE_HOST=your-db-host.rds.amazonaws.com
DATABASE_PORT=5432
DATABASE_NAME=postgres
DATABASE_USER=postgres
DATABASE_PASSWORD=your-password
```

4. **Python dependencies** installed:

```bash
pip install -r requirements.txt
```

---

## What `setup_database.py` Runs (In Order)

| Step | File | What It Does |
|---|---|---|
| 1 | `migrations/enable_pgvector.py` | Enables the `vector` extension in PostgreSQL |
| 2 | `migrations/setup_core_tables.py` | Creates `users`, `projects`, `analyst_sessions` tables + all indexes + triggers |
| 3 | `migrations/add_vector_tables.py` | Creates `confluence_pages`, `jira_issues`, `document_embeddings` tables + HNSW vector index |
| 4 | `migrations/add_dedup_hash_index.py` | Upgrades the dedup index to the 4-column composite (includes `content_hash`) |

> **All steps are idempotent** — safe to re-run if one fails. No data destruction.

---

## Tables Created

```
users
  ├── id (PK)              Azure AD object ID
  ├── email, name
  ├── is_active
  ├── atlassian_domain     \
  ├── atlassian_email       ├── Atlassian integration columns
  ├── atlassian_api_token  /
  └── atlassian_linked_at

projects
  ├── id (PK)
  ├── user_id (FK → users)
  ├── project_name
  ├── jira_project_key
  ├── confluence_space_key
  └── is_deleted           (soft delete)

analyst_sessions
  ├── id (PK)              Must be 33+ chars for AgentCore
  ├── project_id (FK → projects)
  ├── user_id (FK → users)
  ├── title
  ├── brd_id
  ├── message_count
  └── is_deleted           (soft delete)

confluence_pages
  ├── id (UUID PK)
  ├── project_id (FK → projects)
  ├── page_id, space_key, title, url
  └── version_number       (for change detection)

jira_issues
  ├── id (UUID PK)
  ├── project_id (FK → projects)
  ├── issue_key, issue_id, summary
  ├── story_points, sprint_name, labels, components
  └── time tracking fields

document_embeddings
  ├── id (UUID PK)
  ├── project_id (FK → projects)
  ├── source_type          'confluence' or 'jira'
  ├── source_id            page_id or issue_key
  ├── content_chunk        raw text
  ├── chunk_index
  ├── embedding vector(1536)   ← pgvector type
  └── content_hash         SHA-256 for dedup
```

---

## Indexes Summary

| Index Name | Table | Columns | Purpose |
|---|---|---|---|
| `idx_users_email` | users | email | Fast login lookup |
| `idx_projects_user_active` | projects | user_id, is_deleted | List user's active projects |
| `idx_sessions_project_active_updated` | analyst_sessions | project_id, is_deleted, last_updated | List project's sessions sorted by recency |
| `idx_embeddings_content_lookup` | document_embeddings | source_type, source_id, chunk_index, content_hash | **Dedup check** — pure index scan |
| `idx_embeddings_vector` (HNSW) | document_embeddings | embedding | **Vector similarity search** — ~100x faster than exact scan |

---

## Individual Migration Files (For Reference)

These can also be run individually if needed:

```bash
# Run individually (from project root):
python migrations/enable_pgvector.py
python migrations/setup_core_tables.py
python migrations/add_vector_tables.py
python migrations/add_dedup_hash_index.py
```

---

## Files NOT Needed for Setup (Dev/Debug Only)

These files in the root are for debugging, not setup — **do not run in production**:

| File | Purpose |
|---|---|
| `check_db_status.py` | Checks connection, lists tables |
| `diagnose_db.py` | Tests SSL vs non-SSL connection |
| `test_db_connection.py` | Basic connection test |
| `recreate_schema.py` | ⚠️ DESTRUCTIVE — drops and recreates tables |
| `fix_db_triggers.py` | One-time trigger fix (already merged into setup) |
| `apply_db_indices.py` | Old partial index script (superseded by setup) |
| `create_tables.py` | Old setup script (superseded by migrations/) |
| `run_migration.py` | Old single-migration runner |
| `migrations/add_content_hash_column.py` | Already included in `add_dedup_hash_index.py` |
| `migrations/verify_embedding_reuse.py` | Diagnostic — checks if dedup is working |
| `migrations/verify_atlassian_columns.py` | Diagnostic — checks Atlassian columns |
