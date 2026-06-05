import boto3
import json
import psycopg2
import sys

def get_secret():
    secret_name = "sdlc-orch/rds/rds-credentials/sdlc-orchestration-agent"
    region_name = "us-east-1"
    
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    
    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except Exception as e:
        print(f"Error retrieving secret: {e}")
        return None
        
    secret = get_secret_value_response['SecretString']
    return json.loads(secret)

def test_connection():
    print("Fetching secret...")
    credentials = get_secret()
    if not credentials:
        print("Failed to get credentials")
        sys.exit(1)

    host = credentials.get("DATABASE_URI", "sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com")
    port = credentials.get("POSTGRES_PORT", 5432)
    user = credentials.get("POSTGRES_USER", "postgres")
    password = credentials.get("POSTGRES_PASSWORD")
    dbname = credentials.get("POSTGRES_DATABASE", "postgres")

    print(f"Attempting to connect to host: {host}")
    try:
        conn = psycopg2.connect(
            host=host,
            user=user,
            password=password,
            dbname=dbname,
            port=port,
            connect_timeout=10
        )
        print("Connection successful!")
        
        # Test a simple query
        cur = conn.cursor()
        cur.execute('SELECT version()')
        db_version = cur.fetchone()
        print(f"Database version:\n{db_version[0]}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error connecting to the database: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_connection()
