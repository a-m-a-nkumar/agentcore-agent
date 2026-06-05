"""
Unified Database Setup Script for Client Environment
=====================================================
Creates the COMPLETE database schema from scratch:
  1. pgvector extension
  2. users table (with Atlassian credential columns)
  3. projects table
  4. analyst_sessions table
  5. confluence_pages table
  6. jira_issues table (with GenAI prediction fields)
  7. document_embeddings table (with HNSW vector index)
  8. All indexes, triggers, and constraints

Usage:
  # Set DATABASE_* env vars (or use .env file), then:
  python setup_client_db.py

  # Or pass credentials directly:
  python setup_client_db.py --host <RDS_HOST> --port 5432 --dbname <DB> --user <USER> --password <PASS>
"""

import psycopg2
import os
import sys
import argparse
from dotenv import load_dotenv

load_dotenv()

try:
    from environment import EMBEDDING_DIMENSIONS
except ImportError:
    EMBEDDING_DIMENSIONS = 1024


def get_connection(args=None):
    """Get database connection from args, db_config, or environment variables."""
    if args and args.host:
        # CLI args override everything
        params = {
            "host": args.host,
            "port": int(args.port or "5432"),
            "database": args.dbname or "sdlcdev",
            "user": args.user or "postgres",
            "password": args.password or "",
        }
        print(f"🔄 Connecting to {params['host']}:{params['port']}/{params['database']} as {params['user']}...")
        conn = psycopg2.connect(**params)
    else:
        # Use centralized db_config (handles IAM auth automatically)
        try:
            from db_config import get_direct_connection
            conn = get_direct_connection()
        except ImportError:
            # Fallback if db_config is not available (e.g., running standalone)
            from db_config import get_db_params
            params = get_db_params()
            print(f"🔄 Connecting to {params['host']}:{params['port']}/{params['database']}...")
            conn = psycopg2.connect(**params)
    conn.autocommit = False
    return conn


def setup_database(conn):
    """Create all tables, indexes, triggers from scratch."""
    cursor = conn.cursor()

    # ============================================
    # STEP 1: Enable pgvector extension
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 1: Enabling pgvector extension...")
    print("=" * 60)
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
        print("✅ pgvector extension enabled!")
    except Exception as e:
        conn.rollback()
        print(f"⚠️  pgvector extension error: {e}")
        print("   If pgvector is not installed on this RDS instance,")
        print("   you may need to enable it via RDS parameter group or use")
        print("   a PostgreSQL version that supports it (14+).")
        print("   Continuing without vector support...")

    # ============================================
    # STEP 2: Create users table
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 2: Creating users table...")
    print("=" * 60)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR(255) PRIMARY KEY,
            email VARCHAR(500) UNIQUE NOT NULL,
            name VARCHAR(500),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            metadata JSONB DEFAULT '{}'::jsonb
        )
    """)
    conn.commit()

    # Add Atlassian credential columns (from migrations/add_atlassian_credentials.py)
    atlassian_columns = [
        ("atlassian_domain", "VARCHAR(500)"),
        ("atlassian_email", "VARCHAR(500)"),
        ("atlassian_api_token", "TEXT"),
        ("atlassian_linked_at", "TIMESTAMP WITH TIME ZONE"),
    ]
    for col_name, col_type in atlassian_columns:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
            conn.commit()
        except Exception:
            conn.rollback()

    # Indexes for users
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);")
    conn.commit()
    print("✅ users table created (with Atlassian credentials columns)")

    # ============================================
    # STEP 3: Create projects table
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 3: Creating projects table...")
    print("=" * 60)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            project_name VARCHAR(500) NOT NULL,
            description TEXT,
            jira_project_key VARCHAR(100),
            confluence_space_key VARCHAR(100),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN DEFAULT FALSE,
            metadata JSONB DEFAULT '{}'::jsonb,
            CONSTRAINT fk_project_user FOREIGN KEY (user_id)
                REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_is_deleted ON projects(is_deleted);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_active ON projects(user_id, is_deleted);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);")
    conn.commit()
    print("✅ projects table created")

    # ============================================
    # STEP 4: Create analyst_sessions table
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 4: Creating analyst_sessions table...")
    print("=" * 60)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analyst_sessions (
            id VARCHAR(255) PRIMARY KEY,
            project_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            title VARCHAR(500) NOT NULL DEFAULT 'New Chat',
            brd_id VARCHAR(255),
            message_count INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN DEFAULT FALSE,
            metadata JSONB DEFAULT '{}'::jsonb,
            CONSTRAINT fk_session_project FOREIGN KEY (project_id)
                REFERENCES projects(id) ON DELETE CASCADE,
            CONSTRAINT fk_session_user FOREIGN KEY (user_id)
                REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON analyst_sessions(project_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON analyst_sessions(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON analyst_sessions(created_at DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last_updated ON analyst_sessions(last_updated DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_is_deleted ON analyst_sessions(is_deleted);")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_project_active_updated
        ON analyst_sessions(project_id, is_deleted, last_updated DESC)
    """)
    conn.commit()
    print("✅ analyst_sessions table created")

    # ============================================
    # STEP 5: Create confluence_pages table
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 5: Creating confluence_pages table...")
    print("=" * 60)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS confluence_pages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR(255) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            page_id VARCHAR(255) NOT NULL,
            space_key VARCHAR(100) NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            last_modified_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_confluence_page UNIQUE (project_id, page_id)
        )
    """)
    conn.commit()

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_confluence_project_id ON confluence_pages(project_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_confluence_space_key ON confluence_pages(space_key);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_confluence_page_id ON confluence_pages(page_id);")
    conn.commit()
    print("✅ confluence_pages table created")

    # ============================================
    # STEP 6: Create jira_issues table
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 6: Creating jira_issues table (with GenAI fields)...")
    print("=" * 60)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jira_issues (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR(255) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            issue_key VARCHAR(50) NOT NULL,
            issue_id VARCHAR(255) NOT NULL,
            project_key VARCHAR(100) NOT NULL,
            summary TEXT NOT NULL,
            url TEXT NOT NULL,
            issue_type VARCHAR(50),
            status VARCHAR(50),
            priority VARCHAR(50),

            -- Estimation & Time Tracking
            story_points DECIMAL(5,2),
            original_estimate_seconds INTEGER,
            time_spent_seconds INTEGER,
            remaining_estimate_seconds INTEGER,

            -- Sprint & Velocity Data
            sprint_name VARCHAR(255),
            sprint_id VARCHAR(100),

            -- Labels & Components
            labels TEXT[],
            components TEXT[],

            -- Dates
            created_date TIMESTAMP WITH TIME ZONE,
            updated_date TIMESTAMP WITH TIME ZONE NOT NULL,
            resolved_date TIMESTAMP WITH TIME ZONE,

            -- Calculated Fields
            actual_duration_days DECIMAL(10,2),

            -- Metadata
            metadata JSONB DEFAULT '{}'::jsonb,

            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_jira_issue UNIQUE (project_id, issue_key)
        )
    """)
    conn.commit()

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_project_id ON jira_issues(project_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_project_key ON jira_issues(project_key);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_issue_key ON jira_issues(issue_key);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_status ON jira_issues(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_story_points ON jira_issues(story_points);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_issue_type ON jira_issues(issue_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_resolved_date ON jira_issues(resolved_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_labels ON jira_issues USING GIN(labels);")
    conn.commit()
    print("✅ jira_issues table created")

    # ============================================
    # STEP 7: Create document_embeddings table
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 7: Creating document_embeddings table...")
    print("=" * 60)
    try:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                project_id VARCHAR(255) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                user_id VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source_type VARCHAR(50) NOT NULL CHECK (source_type IN ('confluence', 'jira')),
                source_id VARCHAR(255) NOT NULL,
                title TEXT NOT NULL,
                content_chunk TEXT NOT NULL,
                chunk_index INTEGER DEFAULT 0,
                embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL,
                url TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                metadata JSONB DEFAULT '{{}}'::jsonb
            )
        """)
        conn.commit()

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_project_id ON document_embeddings(project_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_source_type ON document_embeddings(source_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_source_id ON document_embeddings(source_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_project_source ON document_embeddings(project_id, source_type);")
        conn.commit()

        # Vector similarity index (HNSW - fast approximate nearest neighbor)
        print("📊 Creating HNSW vector similarity index...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_vector ON document_embeddings USING hnsw (embedding vector_cosine_ops);")
        conn.commit()
        print("✅ document_embeddings table created (with HNSW vector index)")
    except Exception as e:
        conn.rollback()
        print(f"⚠️  document_embeddings table creation error: {e}")
        print("   This likely means pgvector is not available.")
        print("   The table will be created without vector column.")

    # ============================================
    # STEP 8: Create triggers
    # ============================================
    print("\n" + "=" * 60)
    print("STEP 8: Creating auto-update triggers...")
    print("=" * 60)
    cursor.execute("""
        CREATE OR REPLACE FUNCTION update_last_updated_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.last_updated = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    conn.commit()

    cursor.execute("DROP TRIGGER IF EXISTS trigger_sessions_last_updated ON analyst_sessions;")
    cursor.execute("""
        CREATE TRIGGER trigger_sessions_last_updated
        BEFORE UPDATE ON analyst_sessions
        FOR EACH ROW
        EXECUTE FUNCTION update_last_updated_column()
    """)
    conn.commit()

    cursor.execute("DROP TRIGGER IF EXISTS trigger_projects_updated ON projects;")
    cursor.execute("""
        CREATE TRIGGER trigger_projects_updated
        BEFORE UPDATE ON projects
        FOR EACH ROW
        EXECUTE FUNCTION update_last_updated_column()
    """)
    conn.commit()
    print("✅ Auto-update triggers created")

    # ============================================
    # VERIFICATION
    # ============================================
    print("\n" + "=" * 60)
    print("VERIFICATION: Checking created schema...")
    print("=" * 60)

    expected_tables = [
        'users', 'projects', 'analyst_sessions',
        'confluence_pages', 'jira_issues', 'document_embeddings'
    ]

    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    existing_tables = [row[0] for row in cursor.fetchall()]

    print(f"\n📊 Tables found ({len(existing_tables)}):")
    for table in expected_tables:
        status = "✅" if table in existing_tables else "❌ MISSING"
        print(f"   {status} {table}")

    # Column count per table
    for table in expected_tables:
        if table in existing_tables:
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_name = '{table}' AND table_schema = 'public'
            """)
            col_count = cursor.fetchone()[0]
            print(f"      → {col_count} columns")

    # Check indexes
    cursor.execute("""
        SELECT COUNT(*)
        FROM pg_indexes
        WHERE schemaname = 'public'
    """)
    index_count = cursor.fetchone()[0]
    print(f"\n📊 Total indexes: {index_count}")

    # Check extensions
    cursor.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';")
    vector_ext = cursor.fetchone()
    if vector_ext:
        print(f"📊 pgvector extension: v{vector_ext[1]}")
    else:
        print("⚠️  pgvector extension NOT installed")

    cursor.close()

    print("\n" + "=" * 60)
    print("🎉 DATABASE SETUP COMPLETE!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Setup client database schema")
    parser.add_argument("--host", help="Database host")
    parser.add_argument("--port", default="5432", help="Database port (default: 5432)")
    parser.add_argument("--dbname", default="postgres", help="Database name (default: postgres)")
    parser.add_argument("--user", help="Database user")
    parser.add_argument("--password", help="Database password")
    args = parser.parse_args()

    conn = None
    try:
        conn = get_connection(args)
        print("✅ Connected to database successfully!\n")
        setup_database(conn)
    except psycopg2.OperationalError as e:
        print(f"\n❌ Cannot connect to database: {e}")
        print("\n💡 If connecting to RDS in a private subnet, you need to run this from:")
        print("   1. An EC2 instance in the same VPC")
        print("   2. Via VPN connected to the VPC")
        print("   3. Through an SSH tunnel via a bastion host")
        print("   4. As an ECS task in the same VPC")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if conn:
            conn.close()
            print("\n🔌 Database connection closed")


if __name__ == "__main__":
    main()
