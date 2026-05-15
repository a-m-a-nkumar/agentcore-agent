import os
import sys
import logging

# Add parent directory to path so we can import db_helper
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

# Load environment variables from the project root .env file
from dotenv import load_dotenv
env_path = os.path.join(parent_dir, '.env')
load_dotenv(env_path, override=True)  # Override system env vars

from db_helper import get_db_connection, release_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Verify environment variables are loaded
logger.info(f"DATABASE_HOST: {os.getenv('DATABASE_HOST', 'NOT SET')}")
logger.info(f"DATABASE_NAME: {os.getenv('DATABASE_NAME', 'NOT SET')}")


def add_lucid_columns():
    """Add Lucid credential columns to users table.

    Mirrors the Atlassian credential pattern (encrypted PAT-style storage).
    The API key is stored KMS-encrypted at rest by update_user_lucid_credentials.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            logger.info("Adding Lucid columns to users table...")

            columns_to_add = [
                ("lucid_api_key",   "TEXT"),
                ("lucid_linked_at", "TIMESTAMP WITH TIME ZONE"),
            ]

            for column_name, column_type in columns_to_add:
                try:
                    cursor.execute(f"""
                        ALTER TABLE users
                        ADD COLUMN IF NOT EXISTS {column_name} {column_type}
                    """)
                    logger.info(f"+ Added column: {column_name}")
                except Exception as e:
                    logger.warning(f"Column {column_name} may already exist: {e}")

            conn.commit()
            logger.info("Successfully added Lucid columns to users table")

            cursor.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'users'
                AND column_name LIKE 'lucid%'
                ORDER BY column_name
            """)
            columns = cursor.fetchall()
            logger.info(f"\nVerification - Found {len(columns)} Lucid columns:")
            for col in columns:
                logger.info(f"  - {col[0]} ({col[1]})")

    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding Lucid columns: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Lucid Integration - Database Migration")
    logger.info("=" * 60)
    add_lucid_columns()
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
