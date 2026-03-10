import os
import sys

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

# Load environment variables from the project root .env file
from dotenv import load_dotenv
env_path = os.path.join(parent_dir, '.env')
load_dotenv(env_path, override=True)

from db_helper import get_db_connection, release_db_connection

def verify_columns():
    """Verify that Atlassian columns were added successfully"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Check for Atlassian columns
            cursor.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                AND column_name LIKE 'atlassian%'
                ORDER BY column_name
            """)
            
            columns = cursor.fetchall()
            
            print("\n" + "="*60)
            print("Atlassian Columns in 'users' table:")
            print("="*60)
            
            if columns:
                for col in columns:
                    col_name, data_type, nullable = col
                    print(f"✓ {col_name:<25} {data_type:<20} (Nullable: {nullable})")
                print(f"\n Total: {len(columns)} columns added successfully")
            else:
                print("❌ No Atlassian columns found!")
                
            print("="*60 + "\n")
            
    finally:
        release_db_connection(conn)

if __name__ == "__main__":
    verify_columns()
