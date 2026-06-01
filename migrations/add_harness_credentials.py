import os
import sys
import logging

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from dotenv import load_dotenv
env_path = os.path.join(parent_dir, '.env')
load_dotenv(env_path, override=True)

from db_helper import get_db_connection, release_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(f"DATABASE_HOST: {os.getenv('DATABASE_HOST', 'NOT SET')}")
logger.info(f"DATABASE_NAME: {os.getenv('DATABASE_NAME', 'NOT SET')}")


def add_harness_columns():
    """Add Harness credential columns to the users table.

    Mirrors add_lucid_credentials.py. The PAT is stored KMS-encrypted
    by update_user_harness_credentials; account/org/project IDs are plain text.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            logger.info("Adding Harness columns to users table...")

            columns_to_add = [
                ("harness_pat",        "TEXT"),
                ("harness_account_id", "VARCHAR(200)"),
                ("harness_org_id",     "VARCHAR(200)"),
                ("harness_project_id", "VARCHAR(200)"),
                ("harness_linked_at",  "TIMESTAMP WITH TIME ZONE"),
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
            logger.info("Successfully added Harness columns to users table")

            cursor.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'users'
                AND column_name LIKE 'harness%'
                ORDER BY column_name
            """)
            columns = cursor.fetchall()
            logger.info(f"\nVerification — Found {len(columns)} Harness columns:")
            for col in columns:
                logger.info(f"  - {col[0]} ({col[1]})")

    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding Harness columns: {e}")
        raise
    finally:
        release_db_connection(conn)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Harness Integration — Database Migration")
    logger.info("=" * 60)
    add_harness_columns()
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
