import psycopg2
import sys

# Update these if you know a different database name or user
host = "sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com"
port = "5432"
database = "postgres"
user = "postgres"  # Master username fetched from AWS

# Two possible passwords found in Secrets Manager for this environment:
password = "postgres"  # Try this one first (from sdlc-orchestration-agent secret)
# password = "ZoQdWCTSAC1G8rrYQhvb"  # Alternate (from sdlc-orch/rds/rds-credentials/pem-password)

print(f"Connecting to {host}:{port} as {user}...")

try:
    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=database,
        connect_timeout=10
    )
    print("✅ SUCCESS: Connected to PostgreSQL database!")
    cur = conn.cursor()
    cur.execute("SELECT version();")
    print(f"Version: {cur.fetchone()[0]}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"❌ FAILED: {e}")
    sys.exit(1)
