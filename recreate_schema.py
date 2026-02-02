"""
Drop old users table and create new schema
WARNING: This will delete the existing users table!
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def recreate_schema():
    """Drop old tables and create new schema"""
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
        
        print("✅ Connected to database successfully!")
        
        cursor = conn.cursor()
        
        # Drop old tables (CASCADE will drop dependent tables too)
        print("\n⚠️  Dropping old tables...")
        print("   - Dropping analyst_sessions (if exists)...")
        cursor.execute("DROP TABLE IF EXISTS analyst_sessions CASCADE")
        conn.commit()
        
        print("   - Dropping projects (if exists)...")
        cursor.execute("DROP TABLE IF EXISTS projects CASCADE")
        conn.commit()
        
        print("   - Dropping users (if exists)...")
        cursor.execute("DROP TABLE IF EXISTS users CASCADE")
        conn.commit()
        
        print("✅ Old tables dropped!")
        
        # Now create new schema
        print("\n📊 Creating new schema...")
        
        # 1. Create users table
        print("   - Creating users table...")
        cursor.execute("""
            CREATE TABLE users (
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
        
        cursor.execute("CREATE INDEX idx_users_email ON users(email)")
        cursor.execute("CREATE INDEX idx_users_is_active ON users(is_active)")
        conn.commit()
        print("     ✅ Users table created")
        
        # 2. Create projects table
        print("   - Creating projects table...")
        cursor.execute("""
            CREATE TABLE projects (
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
        
        cursor.execute("CREATE INDEX idx_projects_user_id ON projects(user_id)")
        cursor.execute("CREATE INDEX idx_projects_is_deleted ON projects(is_deleted)")
        cursor.execute("CREATE INDEX idx_projects_user_active ON projects(user_id, is_deleted)")
        cursor.execute("CREATE INDEX idx_projects_updated_at ON projects(updated_at DESC)")
        conn.commit()
        print("     ✅ Projects table created")
        
        # 3. Create analyst_sessions table
        print("   - Creating analyst_sessions table...")
        cursor.execute("""
            CREATE TABLE analyst_sessions (
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
        
        cursor.execute("CREATE INDEX idx_sessions_project_id ON analyst_sessions(project_id)")
        cursor.execute("CREATE INDEX idx_sessions_user_id ON analyst_sessions(user_id)")
        cursor.execute("CREATE INDEX idx_sessions_created_at ON analyst_sessions(created_at DESC)")
        cursor.execute("CREATE INDEX idx_sessions_last_updated ON analyst_sessions(last_updated DESC)")
        cursor.execute("CREATE INDEX idx_sessions_is_deleted ON analyst_sessions(is_deleted)")
        cursor.execute("""
            CREATE INDEX idx_sessions_project_active_updated 
            ON analyst_sessions(project_id, is_deleted, last_updated DESC)
        """)
        conn.commit()
        print("     ✅ Analyst_sessions table created")
        
        # 4. Create trigger function
        print("   - Creating triggers...")
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
        
        cursor.execute("DROP TRIGGER IF EXISTS trigger_sessions_last_updated ON analyst_sessions")
        cursor.execute("""
            CREATE TRIGGER trigger_sessions_last_updated
            BEFORE UPDATE ON analyst_sessions
            FOR EACH ROW
            EXECUTE FUNCTION update_last_updated_column()
        """)
        
        cursor.execute("DROP TRIGGER IF EXISTS trigger_projects_updated ON projects")
        cursor.execute("""
            CREATE TRIGGER trigger_projects_updated
            BEFORE UPDATE ON projects
            FOR EACH ROW
            EXECUTE FUNCTION update_last_updated_column()
        """)
        conn.commit()
        print("     ✅ Triggers created")
        
        print("\n✅ All tables and triggers created successfully!")
        
        # Verify tables exist
        print("\n📊 Verifying new schema...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('users', 'projects', 'analyst_sessions')
            ORDER BY table_name
        """)
        
        tables = cursor.fetchall()
        print(f"✅ Found {len(tables)} tables:")
        for table in tables:
            print(f"   - {table[0]}")
        
        # Show column details for each table
        for table_name in ['users', 'projects', 'analyst_sessions']:
            cursor.execute(f"""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = '{table_name}'
                ORDER BY ordinal_position
            """)
            columns = cursor.fetchall()
            print(f"\n   {table_name} columns:")
            for col in columns:
                print(f"     - {col[0]}: {col[1]}")
        
        print("\n🎉 Database schema recreation complete!")
        cursor.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if conn is not None:
            conn.close()
            print("\n🔌 Database connection closed")


if __name__ == "__main__":
    print("=" * 60)
    print("⚠️  WARNING: This will DELETE existing users, projects,")
    print("   and analyst_sessions tables!")
    print("=" * 60)
    
    response = input("\nAre you sure you want to continue? (yes/no): ")
    
    if response.lower() == 'yes':
        recreate_schema()
    else:
        print("❌ Operation cancelled")
