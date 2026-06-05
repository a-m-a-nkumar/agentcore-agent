"""
Database Connection Test Script
Tests PostgreSQL RDS connection using environment variables
"""

import os
import sys

try:
    import psycopg2
    from psycopg2 import OperationalError
except ImportError:
    print("❌ psycopg2 not installed. Installing...")
    print("Run: pip install psycopg2-binary")
    sys.exit(1)


def test_database_connection():
    """Test database connection and print status"""
    
    # Read from environment variables
    db_host = os.getenv('DATABASE_HOST')
    db_port = os.getenv('DATABASE_PORT', '5432')
    db_name = os.getenv('DATABASE_NAME', 'postgres')
    db_user = os.getenv('DATABASE_USER', 'postgres')
    db_password = os.getenv('DATABASE_PASSWORD')
    
    # Validate required variables
    if not all([db_host, db_password]):
        print("❌ Missing required environment variables!")
        print("Please set: DATABASE_HOST, DATABASE_PASSWORD")
        print("\nExample:")
        print("  $env:DATABASE_HOST='your-host'")
        print("  $env:DATABASE_PASSWORD='your-password'")
        return False
    
    print("=" * 60)
    print("DATABASE CONNECTION TEST")
    print("=" * 60)
    print(f"Host: {db_host}")
    print(f"Port: {db_port}")
    print(f"Database: {db_name}")
    print(f"User: {db_user}")
    print(f"Password: {'*' * len(db_password)}")
    print("=" * 60)
    
    try:
        print("\n🔄 Attempting to connect...")
        
        # Create connection
        connection = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password,
            connect_timeout=10
        )
        
        print("✅ Connection successful!")
        
        # Test with a simple query
        cursor = connection.cursor()
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()
        
        print(f"\n📊 Database Info:")
        print(f"   PostgreSQL Version: {db_version[0][:50]}...")
        
        # Get current database
        cursor.execute("SELECT current_database();")
        current_db = cursor.fetchone()[0]
        print(f"   Current Database: {current_db}")
        
        # List tables (if any)
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            LIMIT 10;
        """)
        tables = cursor.fetchall()
        
        if tables:
            print(f"\n📋 Tables in '{current_db}':")
            for table in tables:
                print(f"   - {table[0]}")
        else:
            print(f"\n📋 No tables found in '{current_db}' (public schema)")
        
        # Close connections
        cursor.close()
        connection.close()
        
        print("\n" + "=" * 60)
        print("✅ DATABASE CONNECTION TEST PASSED")
        print("=" * 60)
        return True
        
    except OperationalError as e:
        print(f"\n❌ Connection failed!")
        print(f"Error: {e}")
        print("\nPossible issues:")
        print("  1. Incorrect credentials")
        print("  2. Database host not reachable")
        print("  3. Firewall/Security group blocking connection")
        print("  4. Database is not running")
        return False
        
    except Exception as e:
        print(f"\n❌ Unexpected error!")
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    success = test_database_connection()
    sys.exit(0 if success else 1)
