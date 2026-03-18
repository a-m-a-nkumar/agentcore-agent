"""
Add vector database tables for Confluence and Jira RAG
Enhanced schema with fields for GenAI-based t-shirt size predictions
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def run_migration():
    """Create vector database tables"""
    
    print("🔄 Connecting to database...")
    
    conn = None
    try:
        # Connect to database
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST") or os.getenv("RDS_HOST"),
            port=os.getenv("DATABASE_PORT") or os.getenv("RDS_PORT", "5432"),
            database=os.getenv("DATABASE_NAME") or os.getenv("RDS_DATABASE"),
            user=os.getenv("DATABASE_USER") or os.getenv("RDS_USER", "postgres"),
            password=os.getenv("DATABASE_PASSWORD", ""),
        )
        
        print("✅ Connected to database successfully!")
        cursor = conn.cursor()
        
        # Create confluence_pages table
        print("\n📄 Creating confluence_pages table...")
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
            );
        """)
        
        # Create indexes for confluence_pages
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_confluence_project_id ON confluence_pages(project_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_confluence_space_key ON confluence_pages(space_key);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_confluence_page_id ON confluence_pages(page_id);")
        
        print("✅ confluence_pages table created!")
        
        # Create jira_issues table with enhanced fields
        print("\n🎫 Creating jira_issues table (with GenAI prediction fields)...")
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
                
                -- Estimation & Time Tracking (for GenAI predictions)
                story_points DECIMAL(5,2),
                original_estimate_seconds INTEGER,
                time_spent_seconds INTEGER,
                remaining_estimate_seconds INTEGER,
                
                -- Sprint & Velocity Data
                sprint_name VARCHAR(255),
                sprint_id VARCHAR(100),
                
                -- Labels & Components (for categorization)
                labels TEXT[],
                components TEXT[],
                
                -- Dates
                created_date TIMESTAMP WITH TIME ZONE,
                updated_date TIMESTAMP WITH TIME ZONE NOT NULL,
                resolved_date TIMESTAMP WITH TIME ZONE,
                
                -- Calculated Fields
                actual_duration_days DECIMAL(10,2),
                
                -- Metadata (flexible JSONB for custom fields)
                metadata JSONB DEFAULT '{}'::jsonb,
                
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_jira_issue UNIQUE (project_id, issue_key)
            );
        """)
        
        # Create indexes for jira_issues
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_project_id ON jira_issues(project_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_project_key ON jira_issues(project_key);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_issue_key ON jira_issues(issue_key);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_status ON jira_issues(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_story_points ON jira_issues(story_points);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_issue_type ON jira_issues(issue_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_resolved_date ON jira_issues(resolved_date);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jira_labels ON jira_issues USING GIN(labels);")
        
        print("✅ jira_issues table created!")
        
        # Create document_embeddings table
        print("\n🔍 Creating document_embeddings table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                project_id VARCHAR(255) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                user_id VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source_type VARCHAR(50) NOT NULL CHECK (source_type IN ('confluence', 'jira')),
                source_id VARCHAR(255) NOT NULL,
                title TEXT NOT NULL,
                content_chunk TEXT NOT NULL,
                chunk_index INTEGER DEFAULT 0,
                embedding vector(1024) NOT NULL,
                url TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                metadata JSONB DEFAULT '{}'::jsonb
            );
        """)
        
        # Create indexes for document_embeddings
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_project_id ON document_embeddings(project_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_source_type ON document_embeddings(source_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_source_id ON document_embeddings(source_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_project_source ON document_embeddings(project_id, source_type);")
        
        # Vector similarity index (CRITICAL for fast search!)
        print("📊 Creating vector similarity index (this may take a moment)...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_vector ON document_embeddings USING hnsw (embedding vector_cosine_ops);")
        
        print("✅ document_embeddings table created!")
        
        # Commit all changes
        conn.commit()
        
        print("\n" + "="*60)
        print("✅ Migration completed successfully!")
        print("="*60)
        print("\n📊 Tables created:")
        print("  1. confluence_pages - Minimal metadata for change detection")
        print("  2. jira_issues - Enhanced with GenAI prediction fields")
        print("  3. document_embeddings - Vector search index")
        print("\n🎯 Ready for:")
        print("  - Semantic search across Confluence & Jira")
        print("  - GenAI-based t-shirt size predictions")
        print("="*60)
        
        cursor.close()
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        if conn is not None:
            conn.close()
            print("\n🔌 Database connection closed")

if __name__ == "__main__":
    run_migration()
