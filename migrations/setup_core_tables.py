"""
Migration 02: Create Core Tables
=================================
Creates the three foundational tables:
  - users            (Azure AD authenticated users)
  - projects         (user projects linking Jira/Confluence)
  - analyst_sessions (chat sessions per project)

Also creates:
  - All performance indexes on each table
  - Auto-update triggers for timestamps

Safe to run on a fresh database. All statements use IF NOT EXISTS.
Run AFTER: 01_enable_pgvector.py
Run BEFORE: 03_add_vector_tables.py
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()


def run():
    print("🔄 Connecting to database...")
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        print("✅ Connected!\n")
        cursor = conn.cursor()

        # ── 1. USERS TABLE ────────────────────────────────────────────────────
        print("📊 [1/3] Creating users table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           VARCHAR(255) PRIMARY KEY,           -- Azure AD oid
                email        VARCHAR(500) UNIQUE NOT NULL,
                name         VARCHAR(500),
                created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_login   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_active    BOOLEAN DEFAULT TRUE,
                metadata     JSONB DEFAULT '{}'::jsonb,
                -- Atlassian integration columns
                atlassian_domain     VARCHAR(255),
                atlassian_email      VARCHAR(255),
                atlassian_api_token  TEXT,
                atlassian_linked_at  TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active)")
        conn.commit()
        print("   ✅ users table + indexes created")

        # ── 2. PROJECTS TABLE ─────────────────────────────────────────────────
        print("📊 [2/3] Creating projects table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id                   VARCHAR(255) PRIMARY KEY,
                user_id              VARCHAR(255) NOT NULL,
                project_name         VARCHAR(500) NOT NULL,
                description          TEXT,
                jira_project_key     VARCHAR(100),
                confluence_space_key VARCHAR(100),
                created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_deleted           BOOLEAN DEFAULT FALSE,
                metadata             JSONB DEFAULT '{}'::jsonb,
                CONSTRAINT fk_project_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_is_deleted ON projects(is_deleted)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_active ON projects(user_id, is_deleted)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC)")
        conn.commit()
        print("   ✅ projects table + indexes created")

        # ── 3. ANALYST SESSIONS TABLE ─────────────────────────────────────────
        print("📊 [3/3] Creating analyst_sessions table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyst_sessions (
                id            VARCHAR(255) PRIMARY KEY,          -- 33+ chars for AgentCore
                project_id    VARCHAR(255) NOT NULL,
                user_id       VARCHAR(255) NOT NULL,
                title         VARCHAR(500) NOT NULL DEFAULT 'New Chat',
                brd_id        VARCHAR(255),
                message_count INTEGER DEFAULT 0,
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_updated  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                is_deleted    BOOLEAN DEFAULT FALSE,
                metadata      JSONB DEFAULT '{}'::jsonb,
                CONSTRAINT fk_session_project FOREIGN KEY (project_id)
                    REFERENCES projects(id) ON DELETE CASCADE,
                CONSTRAINT fk_session_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON analyst_sessions(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON analyst_sessions(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON analyst_sessions(created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last_updated ON analyst_sessions(last_updated DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_is_deleted ON analyst_sessions(is_deleted)")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_project_active_updated
            ON analyst_sessions(project_id, is_deleted, last_updated DESC)
        """)
        conn.commit()
        print("   ✅ analyst_sessions table + indexes created")

        # ── 4. TRIGGERS ───────────────────────────────────────────────────────
        print("\n⚙️  Creating auto-update triggers...")

        # Trigger function for last_updated
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_last_updated_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.last_updated = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)

        # Trigger function for updated_at (used by projects)
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)

        cursor.execute("DROP TRIGGER IF EXISTS trigger_sessions_last_updated ON analyst_sessions")
        cursor.execute("""
            CREATE TRIGGER trigger_sessions_last_updated
            BEFORE UPDATE ON analyst_sessions
            FOR EACH ROW EXECUTE FUNCTION update_last_updated_column()
        """)

        cursor.execute("DROP TRIGGER IF EXISTS trigger_projects_updated ON projects")
        cursor.execute("""
            CREATE TRIGGER trigger_projects_updated
            BEFORE UPDATE ON projects
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """)
        conn.commit()
        print("   ✅ Triggers created")

        # ── 5. VERIFY ─────────────────────────────────────────────────────────
        print("\n🔍 Verifying tables...")
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('users', 'projects', 'analyst_sessions')
            ORDER BY table_name
        """)
        tables = [r[0] for r in cursor.fetchall()]
        for t in tables:
            print(f"   ✅ {t}")

        cursor.close()

        print("\n" + "=" * 60)
        print("✅ Migration 02 completed successfully!")
        print("=" * 60)
        print("\n📋 Tables created:")
        print("  - users              (with Atlassian columns included)")
        print("  - projects")
        print("  - analyst_sessions")
        print("\n⏭️  Next step: python migrations/03_add_vector_tables.py")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        if conn:
            conn.close()
            print("\n🔌 Database connection closed.")


if __name__ == "__main__":
    run()
