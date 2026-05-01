"""
Database Helper Functions for Session Management
Handles all database operations for users, projects, and analyst sessions
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
import logging
import time
import threading
import base64
import boto3
from psycopg2 import pool
from datetime import datetime
from typing import List, Dict, Optional, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment-specific DB params (local: direct password | VDI: Secrets Manager)
from environment import get_db_params

# Global pool variable + lock to prevent race conditions during init
_db_pool = None
_db_pool_lock = threading.Lock()

def get_db_pool():
    """Get or initialize the connection pool using centralized db_config"""
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    with _db_pool_lock:
        # Double-check after acquiring lock
        if _db_pool is not None:
            return _db_pool
        try:
            db_params = get_db_params()
            sslmode = db_params.get("sslmode", "require")
            _db_pool = pool.ThreadedConnectionPool(
                5, 50,
                host=db_params["host"],
                port=db_params["port"],
                database=db_params["database"],
                user=db_params["user"],
                password=db_params["password"],
                sslmode=sslmode,
                # TCP keepalive to prevent the remote DB from dropping idle connections
                keepalives=1,
                keepalives_idle=30,      # send keepalive after 30s idle
                keepalives_interval=10,  # retry every 10s
                keepalives_count=5       # give up after 5 missed replies
            )
            logger.info("Database connection pool initialized")
            # Run auto-migrations with a standalone connection (not from the pool)
            # to avoid ThreadedConnectionPool thread-keying issues
            _run_migrations(db_params, sslmode)
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}")
            _db_pool = None
            raise
    return _db_pool


def _run_migrations(db_params: dict, sslmode: str):
    """Run safe ALTER TABLE migrations (idempotent — skips if columns already exist).
    Uses a standalone connection to avoid pool thread-keying issues."""
    conn = psycopg2.connect(
        host=db_params["host"],
        port=db_params["port"],
        database=db_params["database"],
        user=db_params["user"],
        password=db_params["password"],
        sslmode=sslmode,
    )
    try:
        with conn.cursor() as cursor:
            # Add brd_id column to projects table
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'projects' AND column_name = 'brd_id'
                    ) THEN
                        ALTER TABLE projects ADD COLUMN brd_id TEXT;
                    END IF;
                END $$;
            """)
            # Add agentcore_session_id column to projects table
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'projects' AND column_name = 'agentcore_session_id'
                    ) THEN
                        ALTER TABLE projects ADD COLUMN agentcore_session_id TEXT;
                    END IF;
                END $$;
            """)
            # Create artifact_lineage table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artifact_lineage (
                    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    project_id                  VARCHAR(255) NOT NULL,
                    user_id                     VARCHAR(255) NOT NULL,
                    source_type                 VARCHAR(50)  NOT NULL,
                    source_id                   VARCHAR(255) NOT NULL,
                    source_section_id           VARCHAR(50)  NOT NULL,
                    source_version              INTEGER      NOT NULL,
                    source_content_hash         VARCHAR(64)  NOT NULL,
                    target_type                 VARCHAR(50)  NOT NULL,
                    target_id                   VARCHAR(255) NOT NULL,
                    target_content_hash         VARCHAR(64)  NOT NULL,
                    target_metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
                    original_generated_content  JSONB        NOT NULL,
                    status                      VARCHAR(30)  NOT NULL DEFAULT 'current',
                    created_at                  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at                  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_lineage_project FOREIGN KEY (project_id)
                        REFERENCES projects(id) ON DELETE CASCADE,
                    CONSTRAINT fk_lineage_user FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_lineage_source_lookup
                ON artifact_lineage (project_id, source_id, source_section_id, status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_lineage_target_lookup
                ON artifact_lineage (project_id, target_type, target_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_lineage_project_status
                ON artifact_lineage (project_id, status)
            """)
            # Auto-update trigger for updated_at (reuses existing function from setup_core_tables)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger WHERE tgname = 'trigger_lineage_updated'
                    ) THEN
                        CREATE TRIGGER trigger_lineage_updated
                        BEFORE UPDATE ON artifact_lineage
                        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
                    END IF;
                END $$;
            """)
            # Add token_usage column to users table — per-user cumulative LLM token count
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'token_usage'
                    ) THEN
                        ALTER TABLE users ADD COLUMN token_usage BIGINT NOT NULL DEFAULT 0;
                    END IF;
                END $$;
            """)

            conn.commit()
            logger.info("Database migrations completed (brd_id, agentcore_session_id, artifact_lineage, token_usage)")
    except Exception as e:
        conn.rollback()
        logger.error(f"Migration error (non-fatal): {e}")
    finally:
        conn.close()

def get_db_connection():
    """
    Get a connection from the pool with health check.
    If the connection is stale (RDS dropped it), discard and get a fresh one.
    """
    start_time = time.time()
    max_retries = 2
    for attempt in range(max_retries):
        try:
            conn = get_db_pool().getconn()
            # Health check: ping the connection
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                # Connection is stale — discard it and retry
                logger.warning(f"[DB] Stale connection detected (attempt {attempt + 1}), discarding and retrying...")
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    get_db_pool().putconn(conn, close=True)
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    continue
                # Last attempt: reset the entire pool
                global _db_pool
                logger.warning("[DB] Resetting connection pool after stale connections")
                with _db_pool_lock:
                    try:
                        _db_pool.closeall()
                    except Exception:
                        pass
                    _db_pool = None
                conn = get_db_pool().getconn()
            
            duration = (time.time() - start_time) * 1000
            logger.info(f"[DB_PERF] Borrowed connection from pool in {duration:.2f}ms")
            return conn
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"[DB] Connection attempt {attempt + 1} failed: {e}, retrying...")
                continue
            logger.error(f"Failed to get connection from pool: {e}")
            raise

def release_db_connection(conn):
    """Return a connection to the pool, discarding it if broken"""
    if _db_pool and conn:
        try:
            _db_pool.putconn(conn)
        except Exception:
            # Connection is dead — close and discard it instead of returning to pool
            logger.warning("[DB] Connection broken on release, discarding from pool")
            try:
                conn.close()
            except Exception:
                pass
            try:
                _db_pool.putconn(conn, close=True)
            except Exception:
                pass


# ============================================
# USER MANAGEMENT
# ============================================

def create_or_update_user(user_id: str, email: str, name: str = None) -> Dict[str, Any]:
    """
    Create a new user or update existing user's last_login
    
    Args:
        user_id: Azure AD oid
        email: User email
        name: User display name
        
    Returns:
        User record as dictionary
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # First, check if a user with this email already exists (possibly with a different id
            # from a previous Azure AD SPN). If so, update their id to the new oid.
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing = cursor.fetchone()
            if existing and existing['id'] != user_id:
                logger.info(f"Updating user id for {email}: {existing['id']} -> {user_id}")
                cursor.execute("""
                    UPDATE users SET id = %s, name = COALESCE(%s, name), last_login = CURRENT_TIMESTAMP
                    WHERE email = %s
                    RETURNING *
                """, (user_id, name, email))
            else:
                cursor.execute("""
                    INSERT INTO users (id, email, name, last_login)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE
                    SET last_login = CURRENT_TIMESTAMP,
                        email = EXCLUDED.email,
                        name = COALESCE(EXCLUDED.name, users.name)
                    RETURNING *
                """, (user_id, email, name))

            user = dict(cursor.fetchone())
            conn.commit()

            logger.debug(f"User created/updated: {user_id}")
            return user
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating/updating user: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user by ID"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None
    finally:
        release_db_connection(conn)


# ============================================
# PROJECT MANAGEMENT
# ============================================

def create_project(
    project_id: str,
    user_id: str,
    project_name: str,
    description: str = None,
    jira_project_key: str = None,
    confluence_space_key: str = None
) -> Dict[str, Any]:
    """
    Create a new project
    
    Args:
        project_id: UUID from frontend
        user_id: Owner user ID
        project_name: Project name
        description: Project description
        jira_project_key: Jira project key
        confluence_space_key: Confluence space key
        
    Returns:
        Project record as dictionary
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Check for duplicate project name for this user
            cursor.execute(
                "SELECT id FROM projects WHERE user_id = %s AND LOWER(project_name) = LOWER(%s) AND is_deleted = FALSE",
                (user_id, project_name)
            )
            if cursor.fetchone():
                raise ValueError(f"A project named '{project_name}' already exists. Please choose a different name.")

            cursor.execute("""
                INSERT INTO projects (
                    id, user_id, project_name, description,
                    jira_project_key, confluence_space_key
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (project_id, user_id, project_name, description,
                  jira_project_key, confluence_space_key))

            project = dict(cursor.fetchone())
            conn.commit()

            logger.info(f"Project created: {project_id} for user {user_id}")
            return project
    except ValueError:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating project: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_user_projects(user_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
    """
    Get all projects for a user
    
    Args:
        user_id: User ID
        include_deleted: Include soft-deleted projects
        
    Returns:
        List of project dictionaries
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
                SELECT id, user_id, project_name, description,
                       jira_project_key, confluence_space_key,
                       created_at, updated_at, is_deleted
                FROM projects
                WHERE user_id = %s
            """
            
            if not include_deleted:
                query += " AND is_deleted = FALSE"
            
            query += " ORDER BY updated_at DESC"
            
            cursor.execute(query, (user_id,))
            projects = [dict(row) for row in cursor.fetchall()]
            
            # Convert timestamps to milliseconds for frontend
            for project in projects:
                project['created_at'] = int(project['created_at'].timestamp() * 1000)
                project['updated_at'] = int(project['updated_at'].timestamp() * 1000)
            
            return projects
    finally:
        release_db_connection(conn)


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Get project by ID"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM projects 
                WHERE id = %s AND is_deleted = FALSE
            """, (project_id,))
            project = cursor.fetchone()
            
            if project:
                project = dict(project)
                project['created_at'] = int(project['created_at'].timestamp() * 1000)
                project['updated_at'] = int(project['updated_at'].timestamp() * 1000)
            
            return project
    finally:
        release_db_connection(conn)


def update_project(
    project_id: str,
    project_name: str = None,
    description: str = None,
    jira_project_key: str = None,
    confluence_space_key: str = None
) -> Dict[str, Any]:
    """Update project details"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Build dynamic update query
            updates = []
            params = []
            
            if project_name is not None:
                updates.append("project_name = %s")
                params.append(project_name)
            if description is not None:
                updates.append("description = %s")
                params.append(description)
            if jira_project_key is not None:
                updates.append("jira_project_key = %s")
                params.append(jira_project_key)
            if confluence_space_key is not None:
                updates.append("confluence_space_key = %s")
                params.append(confluence_space_key)
            
            if not updates:
                raise ValueError("No fields to update")
            
            params.append(project_id)
            query = f"""
                UPDATE projects
                SET {', '.join(updates)}
                WHERE id = %s
                RETURNING *
            """
            
            cursor.execute(query, params)
            row = cursor.fetchone()
            
            if row:
                project = dict(row)
                conn.commit()
                logger.info(f"Project updated: {project_id}")
                return project
            else:
                conn.rollback()
                logger.warning(f"Update returned no rows for {project_id}")
                return None
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating project: {e}")
        raise
    finally:
        release_db_connection(conn)


def delete_project(project_id: str, hard_delete: bool = False) -> bool:
    """
    Delete project (soft delete by default)
    
    Args:
        project_id: Project ID
        hard_delete: If True, permanently delete; if False, soft delete
        
    Returns:
        True if successful
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if hard_delete:
                cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            else:
                cursor.execute("""
                    UPDATE projects 
                    SET is_deleted = TRUE 
                    WHERE id = %s
                """, (project_id,))
            
            
            rows_affected = cursor.rowcount
            
            # Measure commit time
            commit_start = time.time()
            conn.commit()
            commit_duration = (time.time() - commit_start) * 1000
            logger.info(f"[DB_PERF] DB commit for delete took {commit_duration:.2f}ms")
            
            if rows_affected > 0:
                logger.info(f"Project {'hard' if hard_delete else 'soft'} deleted: {project_id}")
                return True
            else:
                logger.warning(f"Delete returned no rows for {project_id}")
                return False
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting project: {e}")
        raise
    finally:
        release_db_connection(conn)


# ============================================
# PROJECT BRD SESSION PERSISTENCE
# ============================================

def save_project_brd_session(
    project_id: str,
    brd_id: str = None,
    agentcore_session_id: str = None
) -> bool:
    """
    Save/update the BRD session for a project.
    Stores brd_id and agentcore_session_id so chat can be restored later.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            updates = []
            params = []
            if brd_id is not None:
                updates.append("brd_id = %s")
                params.append(brd_id)
            if agentcore_session_id is not None:
                updates.append("agentcore_session_id = %s")
                params.append(agentcore_session_id)
            if not updates:
                return False
            params.append(project_id)
            cursor.execute(f"""
                UPDATE projects
                SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND is_deleted = FALSE
            """, params)
            conn.commit()
            logger.info(f"Saved BRD session for project {project_id}: brd_id={brd_id}, session_id={agentcore_session_id}")
            return cursor.rowcount > 0
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving project BRD session: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_project_brd_session(project_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the saved BRD session for a project.
    Returns {brd_id, agentcore_session_id} or None.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT brd_id, agentcore_session_id
                FROM projects
                WHERE id = %s AND is_deleted = FALSE
            """, (project_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                # Only return if at least one field is set
                if result.get('brd_id') or result.get('agentcore_session_id'):
                    return result
            return None
    finally:
        release_db_connection(conn)


# ============================================
# SESSION MANAGEMENT
# ============================================

def create_session(
    session_id: str,
    project_id: str,
    user_id: str,
    title: str = "New Chat"
) -> Dict[str, Any]:
    """
    Create a new analyst session
    
    Args:
        session_id: Session ID (min 33 chars for AgentCore)
        project_id: Project ID
        user_id: User ID
        title: Session title
        
    Returns:
        Session record as dictionary
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                INSERT INTO analyst_sessions (id, project_id, user_id, title)
                VALUES (%s, %s, %s, %s)
                RETURNING *
            """, (session_id, project_id, user_id, title))
            
            session = dict(cursor.fetchone())
            conn.commit()
            
            # Convert timestamps to milliseconds
            session['created_at'] = int(session['created_at'].timestamp() * 1000)
            session['last_updated'] = int(session['last_updated'].timestamp() * 1000)
            
            logger.info(f"Session created: {session_id} in project {project_id}")
            return session
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating session: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_project_sessions(
    project_id: str,
    user_id: str = None,
    include_deleted: bool = False
) -> List[Dict[str, Any]]:
    """
    Get all sessions for a project (NO JOIN NEEDED)
    
    Args:
        project_id: Project ID
        user_id: Optional user ID for additional filtering
        include_deleted: Include soft-deleted sessions
        
    Returns:
        List of session dictionaries
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
                SELECT id, project_id, user_id, title, brd_id, 
                       message_count, created_at, last_updated, is_deleted
                FROM analyst_sessions
                WHERE project_id = %s
            """
            params = [project_id]
            
            if user_id:
                query += " AND user_id = %s"
                params.append(user_id)
            
            if not include_deleted:
                query += " AND is_deleted = FALSE"
            
            query += " ORDER BY last_updated DESC"
            
            cursor.execute(query, params)
            sessions = [dict(row) for row in cursor.fetchall()]
            
            # Convert timestamps to milliseconds for frontend
            for session in sessions:
                session['created_at'] = int(session['created_at'].timestamp() * 1000)
                session['last_updated'] = int(session['last_updated'].timestamp() * 1000)
            
            return sessions
    finally:
        release_db_connection(conn)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get session by ID"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM analyst_sessions 
                WHERE id = %s AND is_deleted = FALSE
            """, (session_id,))
            session = cursor.fetchone()
            
            if session:
                session = dict(session)
                session['created_at'] = int(session['created_at'].timestamp() * 1000)
                session['last_updated'] = int(session['last_updated'].timestamp() * 1000)
            
            return session
    finally:
        release_db_connection(conn)


def update_session(
    session_id: str,
    title: str = None,
    brd_id: str = None,
    message_count: int = None
) -> Dict[str, Any]:
    """
    Update session details
    
    Args:
        session_id: Session ID
        title: New title
        brd_id: BRD ID
        message_count: Message count
        
    Returns:
        Updated session record
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Build dynamic update query
            updates = []
            params = []
            
            if title is not None:
                updates.append("title = %s")
                params.append(title)
            if brd_id is not None:
                updates.append("brd_id = %s")
                params.append(brd_id)
            if message_count is not None:
                updates.append("message_count = %s")
                params.append(message_count)
            
            if not updates:
                raise ValueError("No fields to update")
            
            params.append(session_id)
            query = f"""
                UPDATE analyst_sessions
                SET {', '.join(updates)}
                WHERE id = %s
                RETURNING *
            """
            
            cursor.execute(query, params)
            session = dict(cursor.fetchone())
            conn.commit()
            
            # Convert timestamps
            session['created_at'] = int(session['created_at'].timestamp() * 1000)
            session['last_updated'] = int(session['last_updated'].timestamp() * 1000)
            
            logger.info(f"Session updated: {session_id}")
            return session
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating session: {e}")
        raise
    finally:
        release_db_connection(conn)


def increment_message_count(session_id: str) -> int:
    """Increment message count for a session"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE analyst_sessions
                SET message_count = message_count + 1
                WHERE id = %s
                RETURNING message_count
            """, (session_id,))
            
            new_count = cursor.fetchone()[0]
            conn.commit()
            return new_count
    except Exception as e:
        conn.rollback()
        logger.error(f"Error incrementing message count: {e}")
        raise
    finally:
        release_db_connection(conn)


def delete_session(session_id: str, hard_delete: bool = False) -> bool:
    """
    Delete session (soft delete by default)
    
    Args:
        session_id: Session ID
        hard_delete: If True, permanently delete; if False, soft delete
        
    Returns:
        True if successful
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if hard_delete:
                cursor.execute("DELETE FROM analyst_sessions WHERE id = %s", (session_id,))
            else:
                cursor.execute("""
                    UPDATE analyst_sessions 
                    SET is_deleted = TRUE 
                    WHERE id = %s
                """, (session_id,))
            
            conn.commit()
            logger.info(f"Session {'hard' if hard_delete else 'soft'} deleted: {session_id}")
            return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting session: {e}")
        raise
    finally:
        release_db_connection(conn)


# ============================================
# DESIGN SESSION MANAGEMENT
# ============================================
# A design_session spans the Diagram phase + the SAD phase. Stage values:
#   NEW | DIAGRAM_GATHERING | DIAGRAM_READY | SAD_GATHERING | SAD_GENERATING | SAD_REFINING

_DESIGN_SESSION_STAGES = {
    "NEW", "DIAGRAM_GATHERING", "DIAGRAM_READY",
    "SAD_GATHERING", "SAD_GENERATING", "SAD_REFINING",
}


def _design_session_to_dict(row: Any) -> Dict[str, Any]:
    s = dict(row)
    if s.get("created_at"):
        s["created_at"] = int(s["created_at"].timestamp() * 1000)
    if s.get("last_activity_ts"):
        s["last_activity_ts"] = int(s["last_activity_ts"].timestamp() * 1000)
    # Normalize UUIDs to strings for JSON safety
    for k in ("id", "project_id", "sad_id"):
        if s.get(k) is not None:
            s[k] = str(s[k])
    return s


def create_design_session(
    session_id: str,
    project_id: str,
    user_id: str,
    name: str,
    stage: str = "NEW",
) -> Dict[str, Any]:
    """Create a new design_session row. Returns the inserted row as a dict."""
    if stage not in _DESIGN_SESSION_STAGES:
        raise ValueError(f"Invalid stage: {stage}")
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                INSERT INTO design_sessions (id, project_id, user_id, name, stage)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (session_id, project_id, user_id, name, stage),
            )
            row = cursor.fetchone()
            conn.commit()
            logger.info(f"design_session created: {session_id} (project {project_id}, stage {stage})")
            return _design_session_to_dict(row)
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating design_session: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_design_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single design_session by id. Returns None if missing or soft-deleted."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT * FROM design_sessions
                WHERE id = %s AND is_deleted = FALSE
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            return _design_session_to_dict(row) if row else None
    finally:
        release_db_connection(conn)


def list_design_sessions(
    project_id: str,
    user_id: str = None,
) -> List[Dict[str, Any]]:
    """List non-deleted design_sessions for a project, newest activity first."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if user_id:
                cursor.execute(
                    """
                    SELECT * FROM design_sessions
                    WHERE project_id = %s AND user_id = %s AND is_deleted = FALSE
                    ORDER BY last_activity_ts DESC
                    """,
                    (project_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM design_sessions
                    WHERE project_id = %s AND is_deleted = FALSE
                    ORDER BY last_activity_ts DESC
                    """,
                    (project_id,),
                )
            return [_design_session_to_dict(r) for r in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def update_design_session(
    session_id: str,
    name: str = None,
    stage: str = None,
    diagram_s3_key: str = None,
    diagram_svg_s3_key: str = None,
    sad_id: str = None,
    confluence_page_id: str = None,
    bump_activity: bool = True,
) -> Dict[str, Any]:
    """
    Patch a design_session. Only the non-None fields are updated. Always
    bumps last_activity_ts unless caller opts out.
    """
    if stage is not None and stage not in _DESIGN_SESSION_STAGES:
        raise ValueError(f"Invalid stage: {stage}")
    sets: List[str] = []
    params: List[Any] = []
    if name is not None:
        sets.append("name = %s"); params.append(name)
    if stage is not None:
        sets.append("stage = %s"); params.append(stage)
    if diagram_s3_key is not None:
        sets.append("diagram_s3_key = %s"); params.append(diagram_s3_key)
    if diagram_svg_s3_key is not None:
        sets.append("diagram_svg_s3_key = %s"); params.append(diagram_svg_s3_key)
    if sad_id is not None:
        sets.append("sad_id = %s"); params.append(sad_id)
    if confluence_page_id is not None:
        sets.append("confluence_page_id = %s"); params.append(confluence_page_id)
    if bump_activity:
        sets.append("last_activity_ts = NOW()")
    if not sets:
        raise ValueError("No fields to update")
    params.append(session_id)

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"UPDATE design_sessions SET {', '.join(sets)} WHERE id = %s RETURNING *",
                params,
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"design_session {session_id} not found")
            conn.commit()
            return _design_session_to_dict(row)
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating design_session {session_id}: {e}")
        raise
    finally:
        release_db_connection(conn)


def delete_design_session(session_id: str, hard_delete: bool = False) -> bool:
    """Soft-delete by default; hard delete the row if requested."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if hard_delete:
                cursor.execute("DELETE FROM design_sessions WHERE id = %s", (session_id,))
            else:
                cursor.execute(
                    "UPDATE design_sessions SET is_deleted = TRUE WHERE id = %s",
                    (session_id,),
                )
            conn.commit()
            logger.info(f"design_session {'hard' if hard_delete else 'soft'} deleted: {session_id}")
            return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting design_session {session_id}: {e}")
        raise
    finally:
        release_db_connection(conn)


# ============================================
# DESIGN DIAGRAM SLOTS (per-type slot model)
# ============================================
# Each design_session has three slots — Logical, Infrastructure, Security.
# Stored as JSONB on `design_sessions.diagram_slots`. The migration that
# adds the column lives at `migrations/add_design_diagram_slots.py`.
#
# Slot status enum:
#   pending | in_progress | done | skipped | skipped_saved | failed
#
# Per-slot shape:
#   { status, tool?, artifact_key?, saved_at?, error? }

_DIAGRAM_TYPES = ("logical", "infrastructure", "security")
_SLOT_STATUSES = {
    "pending", "in_progress", "done",
    "skipped", "skipped_saved", "failed",
}
_AUTHORING_TOOLS = {"drawio", "lucid"}


def get_diagram_slots(session_id: str) -> Dict[str, Any]:
    """Return the diagram_slots JSONB + authoring_tool for a session.

    Shape:
        {
          "tool": "drawio" | "lucid" | None,
          "slots": {
            "logical":        {...},
            "infrastructure": {...},
            "security":       {...}
          }
        }

    Raises ValueError if the session doesn't exist.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT diagram_slots, authoring_tool
                FROM design_sessions
                WHERE id = %s AND is_deleted = FALSE
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"design_session {session_id} not found")
            slots = row["diagram_slots"] or {}
            # Defensive defaulting — older rows might miss a key.
            for t in _DIAGRAM_TYPES:
                if t not in slots:
                    slots[t] = {"status": "pending"}
            return {"tool": row["authoring_tool"], "slots": slots}
    finally:
        release_db_connection(conn)


def update_diagram_slot(
    session_id: str,
    diagram_type: str,
    patch: Dict[str, Any],
    *,
    bump_activity: bool = True,
) -> Dict[str, Any]:
    """Merge `patch` into the slot for `diagram_type`. Returns the updated slot.

    `patch` keys may include any of: status, tool, artifact_key, saved_at, error.
    Unknown keys are dropped silently — the column stays JSON-clean.

    Setting `status` to a terminal-clean state ("pending") clears `error`
    automatically; callers don't need to remember to.
    """
    if diagram_type not in _DIAGRAM_TYPES:
        raise ValueError(f"Invalid diagram_type: {diagram_type}")

    allowed_keys = {"status", "tool", "artifact_key", "saved_at", "error"}
    clean = {k: v for k, v in patch.items() if k in allowed_keys}
    if "status" in clean:
        if clean["status"] not in _SLOT_STATUSES:
            raise ValueError(f"Invalid slot status: {clean['status']}")
        # Clearing error when transitioning out of failed.
        if clean["status"] != "failed":
            clean.setdefault("error", None)
    if "tool" in clean and clean["tool"] is not None and clean["tool"] not in _AUTHORING_TOOLS:
        raise ValueError(f"Invalid tool: {clean['tool']}")

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Read existing slot so we can merge instead of overwrite —
            # callers typically PATCH partial state.
            cursor.execute(
                "SELECT diagram_slots FROM design_sessions WHERE id = %s AND is_deleted = FALSE",
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"design_session {session_id} not found")
            slots = row["diagram_slots"] or {}
            current = slots.get(diagram_type, {"status": "pending"})
            merged = {**current, **clean}
            # Drop nulled keys so the row stays compact.
            merged = {k: v for k, v in merged.items() if v is not None}
            slots[diagram_type] = merged

            sets = ["diagram_slots = %s"]
            params: List[Any] = [json.dumps(slots)]
            if bump_activity:
                sets.append("last_activity_ts = NOW()")
            params.append(session_id)

            cursor.execute(
                f"UPDATE design_sessions SET {', '.join(sets)} WHERE id = %s RETURNING diagram_slots",
                params,
            )
            updated_row = cursor.fetchone()
            conn.commit()
            return (updated_row["diagram_slots"] or {}).get(diagram_type, merged)
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating diagram slot {session_id}.{diagram_type}: {e}")
        raise
    finally:
        release_db_connection(conn)


def set_session_authoring_tool(session_id: str, tool: Optional[str]) -> None:
    """Set the session's preferred authoring tool. `None` clears it."""
    if tool is not None and tool not in _AUTHORING_TOOLS:
        raise ValueError(f"Invalid tool: {tool}")
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE design_sessions
                SET authoring_tool = %s, last_activity_ts = NOW()
                WHERE id = %s
                """,
                (tool, session_id),
            )
            conn.commit()
    finally:
        release_db_connection(conn)


# ============================================
# Atlassian Integration Functions
# ============================================

# KMS encryption for Atlassian PAT tokens (industry standard: never store secrets in plain text)
_KMS_KEY_ARN = os.getenv("KMS_KEY_ARN", "")
_kms_client = None

def _get_kms_client():
    global _kms_client
    if _kms_client is None:
        _kms_client = boto3.client("kms", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return _kms_client

def _encrypt_token(plain_token: str) -> str:
    """Encrypt a PAT token using AWS KMS before storing in DB."""
    if not _KMS_KEY_ARN:
        logger.warning("[KMS] KMS_KEY_ARN not set — storing token without encryption (dev only)")
        return plain_token
    try:
        response = _get_kms_client().encrypt(
            KeyId=_KMS_KEY_ARN,
            Plaintext=plain_token.encode("utf-8"),
        )
        # Store as base64 string with a prefix so we can detect encrypted values
        encrypted_b64 = base64.b64encode(response["CiphertextBlob"]).decode("utf-8")
        return f"kms:{encrypted_b64}"
    except Exception as e:
        logger.error(f"[KMS] Encryption failed: {e}")
        raise RuntimeError("Failed to encrypt Atlassian token. Check KMS configuration.") from e

def _decrypt_token(stored_token: str) -> str:
    """Decrypt a KMS-encrypted PAT token retrieved from DB."""
    if not stored_token:
        return stored_token
    # If token was stored without encryption (dev/legacy), return as-is
    if not stored_token.startswith("kms:"):
        return stored_token
    if not _KMS_KEY_ARN:
        logger.warning("[KMS] KMS_KEY_ARN not set — cannot decrypt token")
        return stored_token
    try:
        ciphertext = base64.b64decode(stored_token[4:])  # strip "kms:" prefix
        response = _get_kms_client().decrypt(
            KeyId=_KMS_KEY_ARN,
            CiphertextBlob=ciphertext,
        )
        return response["Plaintext"].decode("utf-8")
    except Exception as e:
        logger.error(f"[KMS] Decryption failed: {e}")
        raise RuntimeError("Failed to decrypt Atlassian token. Check KMS configuration.") from e


def update_user_atlassian_credentials(
    user_id: str,
    domain: str,
    email: str,
    api_token: str
) -> bool:
    """Encrypt PAT token with KMS then persist all Atlassian credentials to DB."""
    encrypted_token = _encrypt_token(api_token)
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET atlassian_domain    = %s,
                    atlassian_email     = %s,
                    atlassian_api_token = %s,
                    atlassian_linked_at = NOW()
                WHERE id = %s
            """, (domain, email, encrypted_token, user_id))
            conn.commit()
            logger.info(f"[ATLASSIAN] Credentials updated (KMS-encrypted) for user: {user_id}")
            return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[ATLASSIAN] Error updating credentials for user {user_id}: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_user_atlassian_credentials(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve Atlassian credentials from DB and decrypt the PAT token with KMS."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT atlassian_domain, atlassian_email, atlassian_api_token, atlassian_linked_at
                FROM users
                WHERE id = %s
            """, (user_id,))
            result = cursor.fetchone()
            if not result:
                return None
            creds = dict(result)
            # Decrypt the token before returning — callers always get plain text
            if creds.get("atlassian_api_token"):
                creds["atlassian_api_token"] = _decrypt_token(creds["atlassian_api_token"])
            return creds
    finally:
        release_db_connection(conn)


def update_user_figma_credentials(user_id: str, pat: str, team_id: str) -> bool:
    """Save Figma PAT and Team ID for a user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET figma_pat = %s,
                    figma_team_id = %s,
                    figma_linked_at = NOW()
                WHERE id = %s
            """, (pat, team_id, user_id))
            conn.commit()
            return cursor.rowcount > 0
    finally:
        release_db_connection(conn)


def get_user_figma_credentials(user_id: str):
    """Retrieve stored Figma PAT and Team ID. Returns None if not linked."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT figma_pat, figma_team_id, figma_linked_at
                FROM users
                WHERE id = %s
            """, (user_id,))
            result = cursor.fetchone()
            return dict(result) if result else None
    finally:
        release_db_connection(conn)


def increment_user_token_usage(user_id: str, tokens: int) -> None:
    """Atomically add `tokens` to users.token_usage for the given user_id.

    Silently skips if user_id is missing or tokens <= 0. Never raises — token
    accounting must not break user-facing flows.
    """
    if not user_id or not tokens or tokens <= 0:
        return
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET token_usage = token_usage + %s WHERE id = %s",
                    (int(tokens), user_id),
                )
                conn.commit()
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.warning(f"[token_usage] Failed to increment for user {user_id}: {e}")
