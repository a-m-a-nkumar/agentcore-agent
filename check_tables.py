contin"""
Check what tables exist in the database
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

cursor = conn.cursor()

# Check all tables
cursor.execute("""
    SELECT table_name FROM information_schema.tables 
    WHERE table_schema = 'public'
    ORDER BY table_name
""")

tables = cursor.fetchall()
print(f"Found {len(tables)} tables:")
for table in tables:
    print(f"  - {table[0]}")

# Check columns in users table if it exists
if any(t[0] == 'users' for t in tables):
    print("\nColumns in users table:")
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'users'
        ORDER BY ordinal_position
    """)
    columns = cursor.fetchall()
    for col in columns:
        print(f"  - {col[0]}: {col[1]}")

cursor.close()
conn.close()
