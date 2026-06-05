import os
import sys
import psycopg2
from dotenv import load_dotenv

def test_connection():
    load_dotenv()
    host = os.getenv('DATABASE_HOST')
    port = os.getenv('DATABASE_PORT', '5432')
    user = os.getenv('DATABASE_USER')
    password = os.getenv('DATABASE_PASSWORD')
    dbname = os.getenv('DATABASE_NAME')
    
    print(f"Testing connection to {host}:{port} as {user}")
    
    try:
        print("\nAttempt 1: SSL Required (sslmode='require')")
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, database=dbname,
            sslmode='require'
        )
        print("✅ SUCCESS: Connected with SSL")
        conn.close()
    except Exception as e:
        print(f"❌ FAILURE: {e}")

    try:
        print("\nAttempt 2: No SSL (default)")
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, database=dbname
        )
        print("✅ SUCCESS: Connected without SSL")
        conn.close()
    except Exception as e:
        print(f"❌ FAILURE: {e}")

if __name__ == "__main__":
    test_connection()
