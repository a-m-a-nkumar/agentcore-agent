
import logging
import os
from dotenv import load_dotenv

# Load environment variables first
load_dotenv(override=True)
print(f"DEBUG: Loaded DATABASE_HOST={os.getenv('DATABASE_HOST')}")

from db_helper import get_db_connection, release_db_connection

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def apply_indices():
    """Apply performance indices to the database"""
    print("Connecting to database using pool...")
    conn = None
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()

        print("Executing SQL: CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON analyst_sessions(project_id)...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON analyst_sessions(project_id);")
        
        print("Executing SQL: CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);")
        
        print("Executing SQL: CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON analyst_sessions(user_id)...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON analyst_sessions(user_id);")

        print("✅ Performance indices applied successfully!")
        
        cursor.close()
    except Exception as e:
        print(f"❌ Error applying indices: {e}")
    finally:
        if conn:
            release_db_connection(conn)

if __name__ == "__main__":
    apply_indices()
