"""
Simple test to create just the users table
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv("DATABASE_HOST"),
    port=os.getenv("DATABASE_PORT", "5432"),
    database=os.getenv("DATABASE_NAME"),
    user=os.getenv("DATABASE_USER"),
    password=os.getenv("DATABASE_PASSWORD"),
)

print("✅ Connected!")

cursor = conn.cursor()

# Try creating table
print("Creating users table...")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id VARCHAR(255) PRIMARY KEY,
        email VARCHAR(500) UNIQUE NOT NULL,
        name VARCHAR(500)
    )
""")
conn.commit()
print("✅ Table created!")

# Verify it exists
cursor.execute("""
    SELECT table_name FROM information_schema.tables 
    WHERE table_schema = 'public' AND table_name = 'users'
""")
result = cursor.fetchall()
print(f"Tables found: {result}")

cursor.close()
conn.close()
