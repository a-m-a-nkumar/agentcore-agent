"""
Quick Database Connection Status Check
"""
import os
import sys

try:
    import psycopg2
except ImportError:
    print("❌ psycopg2 not installed")
    sys.exit(1)

# Connection details
db_config = {
    'host': 'deluxe-db.c7ameyeqe2m2.us-east-1.rds.amazonaws.com',
    'port': '5432',
    'database': 'postgres',
    'user': 'postgres',
    'password': os.getenv('DATABASE_PASSWORD', ']S7]_qph(k(GNiM9oGU>EXKuUQz$'),
    'connect_timeout': 10
}

print("=" * 70)
print("DATABASE CONNECTION STATUS CHECK")
print("=" * 70)
print(f"\n📍 Host: {db_config['host']}")
print(f"📍 Port: {db_config['port']}")
print(f"📍 Database: {db_config['database']}")
print(f"📍 User: {db_config['user']}")
print("\n🔄 Testing connection...\n")

try:
    # Attempt connection
    conn = psycopg2.connect(**db_config)
    cursor = conn.cursor()
    
    print("✅ CONNECTION SUCCESSFUL!\n")
    
    # Get PostgreSQL version
    cursor.execute("SELECT version();")
    version = cursor.fetchone()[0]
    print(f"📊 PostgreSQL Version:")
    print(f"   {version}\n")
    
    # Get database size
    cursor.execute("""
        SELECT pg_size_pretty(pg_database_size(current_database())) as size;
    """)
    db_size = cursor.fetchone()[0]
    print(f"💾 Database Size: {db_size}\n")
    
    # Count tables
    cursor.execute("""
        SELECT COUNT(*) 
        FROM information_schema.tables 
        WHERE table_schema = 'public';
    """)
    table_count = cursor.fetchone()[0]
    print(f"📋 Tables in 'public' schema: {table_count}\n")
    
    # List all tables
    if table_count > 0:
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables = cursor.fetchall()
        print("📁 Table List:")
        for table in tables:
            print(f"   • {table[0]}")
        print()
    
    # Get connection info
    cursor.execute("SELECT current_user, current_database(), inet_server_addr(), inet_server_port();")
    user, db, server_ip, server_port = cursor.fetchone()
    print(f"🔗 Connection Details:")
    print(f"   Current User: {user}")
    print(f"   Current Database: {db}")
    print(f"   Server IP: {server_ip}")
    print(f"   Server Port: {server_port}\n")
    
    cursor.close()
    conn.close()
    
    print("=" * 70)
    print("✅ DATABASE IS ACCESSIBLE AND READY TO USE!")
    print("=" * 70)
    
except psycopg2.OperationalError as e:
    print("❌ CONNECTION FAILED!\n")
    print(f"Error: {e}\n")
    print("=" * 70)
    print("⚠️  DATABASE IS NOT ACCESSIBLE")
    print("=" * 70)
    sys.exit(1)
    
except Exception as e:
    print(f"❌ Unexpected Error: {e}")
    sys.exit(1)
