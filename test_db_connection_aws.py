"""
Enhanced Database Connection Test with AWS Secrets Manager
"""

import os
import sys
import json

try:
    import boto3
    import psycopg2
    from psycopg2 import OperationalError
except ImportError as e:
    print(f"❌ Missing required package: {e}")
    print("Run: pip install psycopg2-binary boto3")
    sys.exit(1)


def get_secret_from_aws(secret_arn):
    """Retrieve database password from AWS Secrets Manager"""
    try:
        print(f"🔐 Retrieving secret from AWS Secrets Manager...")
        print(f"   ARN: {secret_arn[:50]}...")
        
        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name='us-east-1'
        )
        
        response = client.get_secret_value(SecretId=secret_arn)
        
        if 'SecretString' in response:
            secret = json.loads(response['SecretString'])
            print("✅ Secret retrieved successfully!")
            return secret
        else:
            print("❌ Secret is binary, expected string")
            return None
            
    except Exception as e:
        print(f"❌ Failed to retrieve secret: {e}")
        return None


def test_database_connection_with_secrets():
    """Test database connection using AWS Secrets Manager"""
    
    # Database configuration
    db_host = os.getenv('DATABASE_HOST', 'deluxe-db.c7ameyeqe2m2.us-east-1.rds.amazonaws.com')
    db_port = os.getenv('DATABASE_PORT', '5432')
    db_name = os.getenv('DATABASE_NAME', 'postgres')
    db_user = os.getenv('DATABASE_USER', 'postgres')
    secret_arn = os.getenv('DATABASE_SECRET_ARN', 'arn:aws:secretsmanager:us-east-1:448049797912:secret:rds!db-82383b0f-6182-4a26-a0a6-d8513a9d0c74-KnJB4P')
    
    print("=" * 70)
    print("DATABASE CONNECTION TEST WITH AWS SECRETS MANAGER")
    print("=" * 70)
    
    # Try to get password from Secrets Manager
    db_password = os.getenv('DATABASE_PASSWORD')
    
    if not db_password and secret_arn:
        secret = get_secret_from_aws(secret_arn)
        if secret:
            db_password = secret.get('password')
            if not db_password:
                print("⚠️  Secret retrieved but no 'password' field found")
                print(f"   Available fields: {list(secret.keys())}")
    
    if not db_password:
        print("\n❌ No password available!")
        print("Set DATABASE_PASSWORD environment variable or ensure AWS credentials are configured")
        return False
    
    print(f"\n📋 Connection Details:")
    print(f"   Host: {db_host}")
    print(f"   Port: {db_port}")
    print(f"   Database: {db_name}")
    print(f"   User: {db_user}")
    print(f"   Password: {'*' * min(len(db_password), 20)}")
    print("=" * 70)
    
    try:
        print("\n🔄 Attempting to connect...")
        
        connection = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password,
            connect_timeout=10
        )
        
        print("✅ CONNECTION SUCCESSFUL!")
        
        cursor = connection.cursor()
        
        # Get PostgreSQL version
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()[0]
        print(f"\n📊 Database Info:")
        print(f"   PostgreSQL: {db_version[:80]}...")
        
        # Get current database
        cursor.execute("SELECT current_database();")
        current_db = cursor.fetchone()[0]
        print(f"   Current DB: {current_db}")
        
        # List schemas
        cursor.execute("""
            SELECT schema_name 
            FROM information_schema.schemata 
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schema_name;
        """)
        schemas = cursor.fetchall()
        
        if schemas:
            print(f"\n📁 Schemas:")
            for schema in schemas:
                print(f"   - {schema[0]}")
        
        # List tables
        cursor.execute("""
            SELECT schemaname, tablename 
            FROM pg_tables 
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schemaname, tablename
            LIMIT 20;
        """)
        tables = cursor.fetchall()
        
        if tables:
            print(f"\n📋 Tables (showing first 20):")
            for schema, table in tables:
                print(f"   - {schema}.{table}")
        else:
            print(f"\n📋 No user tables found")
        
        cursor.close()
        connection.close()
        
        print("\n" + "=" * 70)
        print("✅ DATABASE CONNECTION TEST PASSED")
        print("=" * 70)
        return True
        
    except OperationalError as e:
        error_msg = str(e)
        print(f"\n❌ CONNECTION FAILED!")
        print(f"\nError: {error_msg}")
        
        if "timeout" in error_msg.lower() or "could not connect" in error_msg.lower():
            print("\n🔍 Diagnosis: NETWORK/SECURITY GROUP ISSUE")
            print("\n   The database server is not reachable from your location.")
            print("   This is likely due to AWS RDS Security Group restrictions.")
            print("\n   Solutions:")
            print("   1. Update RDS Security Group to allow your IP address")
            print("   2. Use AWS Systems Manager Session Manager")
            print("   3. Connect from an EC2 instance in the same VPC")
            print("   4. Use AWS RDS Proxy")
            print("\n   Your Lambda functions can already connect because")
            print("   they are in the same VPC as the database.")
            
        elif "authentication" in error_msg.lower() or "password" in error_msg.lower():
            print("\n🔍 Diagnosis: AUTHENTICATION ISSUE")
            print("   The credentials may be incorrect.")
            
        return False
        
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR!")
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    success = test_database_connection_with_secrets()
    sys.exit(0 if success else 1)
