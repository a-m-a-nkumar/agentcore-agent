import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables, overriding potential shell defaults
load_dotenv(override=True)

def fix_database_triggers():
    host = os.getenv("DATABASE_HOST")
    print(f"Connecting to database at {host}...")
    try:
        conn = psycopg2.connect(
            host=os.getenv("DATABASE_HOST"),
            port=os.getenv("DATABASE_PORT", "5432"),
            database=os.getenv("DATABASE_NAME"),
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
        )
        cur = conn.cursor()

        print("1. Creating specific trigger function for 'updated_at' column...")
        cur.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)

        print("2. identifying existing triggers on 'projects' table...")
        cur.execute("""
            SELECT trigger_name 
            FROM information_schema.triggers 
            WHERE event_object_table = 'projects';
        """)
        triggers = cur.fetchall()
        
        for (t_name,) in triggers:
            print(f"   - Removing old trigger: {t_name}")
            cur.execute(f"DROP TRIGGER IF EXISTS {t_name} ON projects;")

        print("3. Creating fresh trigger for projects table...")
        cur.execute("""
            CREATE TRIGGER update_projects_updated_at
            BEFORE UPDATE ON projects
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
        """)

        conn.commit()
        print("\nSUCCESS: Database triggers updated. The 'projects' table will now use 'updated_at'.")
        
    except Exception as e:
        print(f"\nERROR: Failed to update database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    fix_database_triggers()
