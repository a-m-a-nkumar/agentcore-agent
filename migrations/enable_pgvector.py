"""
Enable pgvector extension in PostgreSQL database
Run this first before creating vector tables
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def enable_pgvector():
    """Enable pgvector extension in the database"""
    
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
        
        # Enable pgvector extension
        print("📦 Enabling pgvector extension...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
        
        print("✅ pgvector extension enabled!")
        
        # Verify extension is installed
        print("\n🔍 Verifying installation...")
        cursor.execute("""
            SELECT extname, extversion 
            FROM pg_extension 
            WHERE extname = 'vector';
        """)
        
        result = cursor.fetchone()
        if result:
            print(f"✅ pgvector version {result[1]} is installed and ready!")
        else:
            print("⚠️  Warning: pgvector extension not found after installation")
        
        cursor.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n💡 Troubleshooting:")
        print("1. Make sure your PostgreSQL version is 11 or higher")
        print("2. Check if you have permissions to create extensions")
        print("3. For AWS RDS, pgvector should be available by default")
        raise
    finally:
        if conn is not None:
            conn.close()
            print("🔌 Database connection closed")

if __name__ == "__main__":
    enable_pgvector()
