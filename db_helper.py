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

            # Add access_role column to users table — derived from Azure AD group
            # membership and refreshed on every authenticated request. Acceptable
            # values: 'BOTH', 'TECH', 'BUSINESS', 'NONE'. See auth.py
            # `compute_access_role()` for the mapping.
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'access_role'
                    ) THEN
                        ALTER TABLE users ADD COLUMN access_role VARCHAR(16) NOT NULL DEFAULT 'NONE';
                    END IF;
                END $$;
            """)

            # user_module_activity — per-event log feeding Organization Usage dashboard
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_module_activity (
                    id            BIGSERIAL PRIMARY KEY,
                    user_id       VARCHAR(255) NOT NULL,
                    project_id    VARCHAR(255),
                    module        VARCHAR(64)  NOT NULL,
                    event_type    VARCHAR(128) NOT NULL,
                    source        VARCHAR(32)  NOT NULL DEFAULT 'web',
                    occurred_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata      JSONB        NOT NULL DEFAULT '{}'::jsonb,
                    CONSTRAINT fk_uma_user    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
                    CONSTRAINT fk_uma_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_uma_user_time   ON user_module_activity(user_id, occurred_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_uma_module_time ON user_module_activity(module, occurred_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_uma_event_time  ON user_module_activity(event_type, occurred_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_uma_user_module ON user_module_activity(user_id, module)")

            conn.commit()
            logger.info("Database migrations completed (brd_id, agentcore_session_id, artifact_lineage, token_usage, user_module_activity)")
    except Exception as e:
        conn.rollback()
        logger.error(f"Migration error (non-fatal): {e}")
    finally:
        conn.close()

def get_db_connection():
    """
    Get a connection from the pool with a CHEAP stale-connection check.

    The old implementation ran `SELECT 1` against RDS on every borrow as a
    health check. Over a cross-region/VDI network that round-trip cost ~400ms
    per borrow, which combined with N borrows per RAG sync page added up to
    hours of pure network latency on initial syncs.

    The new check uses psycopg2's `Connection.closed` attribute — a local read
    of the socket state with NO network round-trip. If the connection passes
    `closed` but is silently dead (e.g. RDS killed it without our knowing),
    the caller's actual query will raise, and the per-function exception
    handlers + TCP keepalive (keepalives_idle=30, configured at pool creation)
    will detect and recover. So we trade an exotic safety net for ~400ms back
    on every single borrow.
    """
    start_time = time.time()
    max_retries = 2
    for attempt in range(max_retries):
        try:
            conn = get_db_pool().getconn()

            # Cheap local check: is the socket closed?
            if conn.closed:
                logger.warning(f"[DB] Closed connection from pool (attempt {attempt + 1}), discarding and retrying...")
                try:
                    get_db_pool().putconn(conn, close=True)
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    continue
                # Last attempt: reset the entire pool
                global _db_pool
                logger.warning("[DB] Resetting connection pool after closed connections")
                with _db_pool_lock:
                    try:
                        _db_pool.closeall()
                    except Exception:
                        pass
                    _db_pool = None
                conn = get_db_pool().getconn()

            duration = (time.time() - start_time) * 1000
            # Only log when slow — healthy borrows should be sub-millisecond
            # and would otherwise flood the log during heavy sync work.
            if duration > 50:
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
#
# BRD session stage enum (mirrors _DESIGN_SESSION_STAGES below for SAD).
# Set on analyst_sessions.stage via migrations/add_analyst_sessions_stage.py.
#
#   NEW         — session created, no chat yet
#   GATHERING   — Mary is asking follow-ups; chat accumulating
#   GENERATING  — generation in flight (long-poll guard; /turn returns
#                 generation_in_progress card and skips the router)
#   DRAFTED     — BRD JSON exists, no edits yet
#   REFINING    — BRD exists AND at least one edit/regenerate/audit landed
#
# Kept separate from _DESIGN_SESSION_STAGES so SAD and BRD evolve
# independently; the table name (analyst_sessions vs design_sessions)
# already partitions them at the DB layer.

BRD_SESSION_STAGES = frozenset({
    "NEW", "GATHERING", "GENERATING", "DRAFTED", "REFINING",
})


def validate_brd_session_stage(stage: str) -> str:
    """Raise ValueError if `stage` is not a recognised BRD session stage.
    Returns the stage unchanged on success so callers can chain it."""
    if stage not in BRD_SESSION_STAGES:
        raise ValueError(
            f"Invalid BRD session stage: {stage!r}. "
            f"Must be one of: {sorted(BRD_SESSION_STAGES)}"
        )
    return stage


def create_session(
    session_id: str,
    project_id: str,
    user_id: str,
    title: str = "New Chat",
    stage: str = "NEW",
    use_long_term_context: bool = True,
) -> Dict[str, Any]:
    """
    Create a new analyst session.

    Args:
        session_id: Session ID (min 33 chars for AgentCore)
        project_id: Project ID
        user_id: User ID
        title: Session title
        stage: BRD session stage. Defaults to "NEW". Validated against
            BRD_SESSION_STAGES. Old callers that don't pass this keep the
            DB default behaviour ("NEW").
        use_long_term_context: When True (default), the unified BRD
            orchestrator retrieves long-term semantic facts from
            AgentCore Memory and seeds prompts with project context.
            When False, the session starts fresh — no retrieval; writes
            still feed long-term memory for future sessions.

    Returns:
        Session record as dictionary.
    """
    validate_brd_session_stage(stage)

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                INSERT INTO analyst_sessions
                    (id, project_id, user_id, title, stage, use_long_term_context)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (session_id, project_id, user_id, title, stage, use_long_term_context))

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


def update_brd_session_stage(session_id: str, stage: str) -> None:
    """Update the BRD session stage for `session_id`. Validates against
    BRD_SESSION_STAGES. Also bumps last_updated. Idempotent — writing the
    same stage twice is a no-op as far as the caller is concerned."""
    validate_brd_session_stage(stage)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE analyst_sessions
                   SET stage = %s,
                       last_updated = NOW()
                 WHERE id = %s
                """,
                (stage, session_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"update_brd_session_stage: no row matched id={session_id}")
            else:
                logger.info(f"Session {session_id} stage -> {stage}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating BRD session stage: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_brd_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single BRD session row by id. Returns None if not found or
    soft-deleted. Includes stage and use_long_term_context."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, project_id, user_id, title, brd_id,
                       stage, use_long_term_context,
                       message_count, created_at, last_updated, is_deleted
                  FROM analyst_sessions
                 WHERE id = %s
                   AND is_deleted = FALSE
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            session = dict(row)
            session['created_at']   = int(session['created_at'].timestamp() * 1000)
            session['last_updated'] = int(session['last_updated'].timestamp() * 1000)
            # Normalize UUID-typed columns to strings for JSON safety.
            for k in ("id", "project_id"):
                if session.get(k) is not None:
                    session[k] = str(session[k])
            return session
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
    """Fetch a single design_session by id. Returns None if missing.

    Hard-delete model — there's no soft-deleted state to filter for. If the
    row exists in the table it's a real session; if not, it was deleted.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM design_sessions WHERE id = %s",
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
    """List design_sessions for a project, newest activity first."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if user_id:
                cursor.execute(
                    """
                    SELECT * FROM design_sessions
                    WHERE project_id = %s AND user_id = %s
                    ORDER BY last_activity_ts DESC
                    """,
                    (project_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM design_sessions
                    WHERE project_id = %s
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


def delete_design_session(session_id: str) -> bool:
    """Hard-delete a design_session row. S3 artefacts are intentionally NOT
    cleaned up — they stay under sessions/{id}/* in case the user wants to
    pull them back via direct S3 access. Add an explicit S3 cleanup
    helper later if/when product wants true scrubbing."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM design_sessions WHERE id = %s", (session_id,))
            conn.commit()
            logger.info(f"design_session deleted: {session_id}")
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
                WHERE id = %s
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
                "SELECT diagram_slots FROM design_sessions WHERE id = %s",
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


def update_user_lucid_credentials(user_id: str, api_key: str) -> bool:
    """Encrypt the user's Lucid REST API key with KMS then persist to DB.

    Mirrors update_user_atlassian_credentials. The api_key is stored
    KMS-encrypted (kms:<base64> prefix) by _encrypt_token; if KMS_KEY_ARN
    isn't set the helper falls back to plaintext storage for dev with a
    warning logged.
    """
    encrypted_key = _encrypt_token(api_key)
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET lucid_api_key   = %s,
                    lucid_linked_at = NOW()
                WHERE id = %s
            """, (encrypted_key, user_id))
            conn.commit()
            logger.info(f"[LUCID] Credentials updated (KMS-encrypted) for user: {user_id}")
            return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[LUCID] Error updating credentials for user {user_id}: {e}")
        raise
    finally:
        release_db_connection(conn)


def get_user_lucid_credentials(user_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve Lucid credentials from DB and decrypt the API key.

    Returns None if the user has no api_key on file (never linked, or
    unlinked). Otherwise returns {"lucid_api_key": "...", "lucid_linked_at": ...}.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT lucid_api_key, lucid_linked_at
                FROM users
                WHERE id = %s
            """, (user_id,))
            result = cursor.fetchone()
            if not result:
                return None
            creds = dict(result)
            if not creds.get("lucid_api_key"):
                return None
            creds["lucid_api_key"] = _decrypt_token(creds["lucid_api_key"])
            return creds
    finally:
        release_db_connection(conn)


def clear_user_lucid_credentials(user_id: str) -> bool:
    """Unlink: clear the user's Lucid api_key and linked_at."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET lucid_api_key   = NULL,
                    lucid_linked_at = NULL
                WHERE id = %s
            """, (user_id,))
            conn.commit()
            logger.info(f"[LUCID] Credentials cleared for user: {user_id}")
            return cursor.rowcount > 0
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


_VALID_ACCESS_ROLES = frozenset({"BOTH", "TECH", "BUSINESS", "NONE"})


def update_user_access_role(
    user_id: str,
    access_role: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> bool:
    """Single-shot upsert of users.access_role for the given user_id.

    Returns True iff the DB now contains the requested access_role for this
    user — caller uses the return value to decide whether to update the
    per-worker `_LAST_ACCESS_ROLE_CACHE`. Returning False on failure prevents
    the cache from claiming a value the DB doesn't actually hold (which would
    short-circuit retries on subsequent requests and leave the row stuck).

    Acceptable roles: 'BOTH', 'TECH', 'BUSINESS', 'NONE'.

    email + name come from the verified JWT in get_current_user. We supply
    them in the INSERT VALUES so the NOT NULL constraint on users.email is
    satisfied even when the row doesn't yet exist. (PostgreSQL evaluates
    NOT NULL on the prospective INSERT row BEFORE running ON CONFLICT
    detection, so without these we'd fail the constraint and never even
    reach the UPDATE branch — manifesting as a "stuck NONE / stuck NO
    GROUPS" pill on the org-usage page.)

    On the ON CONFLICT path:
      - access_role is updated unconditionally to the JWT-derived value
        (when it differs from stored).
      - email and name are filled via COALESCE so an admin who edits a
        user's name in the DB isn't stomped by the auth flow on the next
        request — the auth-supplied value only wins if the stored cell
        is null.

    Logs an INFO line on every successful write describing whether the
    role was unchanged or actually flipped, so the auth flow's effect on
    the row is grep-able from CloudWatch / local logs.

    Silently skips on missing user_id or invalid role; never raises —
    auth flow must not break on a tracking write.
    """
    if not user_id:
        return False
    if access_role not in _VALID_ACCESS_ROLES:
        logger.warning(
            f"[access_role] Invalid role '{access_role}' for user {user_id}; skipping"
        )
        return False
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # RETURNING (xmax = 0) is PostgreSQL's idiomatic way of
                # detecting which branch of an UPSERT actually fired:
                #   xmax = 0 → INSERT (row was newly created)
                #   xmax > 0 → UPDATE (existing row was modified)
                # We log this so a quick `grep [access_role]` shows
                # whether your row got created, updated, or left alone.
                cursor.execute(
                    """
                    INSERT INTO users (id, email, name, access_role)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                       SET access_role = EXCLUDED.access_role,
                           email       = COALESCE(users.email, EXCLUDED.email),
                           name        = COALESCE(users.name,  EXCLUDED.name)
                     WHERE users.access_role IS DISTINCT FROM EXCLUDED.access_role
                        OR users.email IS NULL
                        OR users.name  IS NULL
                    RETURNING (xmax = 0) AS inserted, access_role
                    """,
                    (user_id, email, name, access_role),
                )
                row = cursor.fetchone()
                conn.commit()
                if row is None:
                    # No-op: existing row already had matching access_role
                    # and email/name. The WHERE clause filtered the UPDATE
                    # out, so RETURNING produces nothing. Still a success.
                    logger.info(
                        f"[access_role] no-op for user {user_id} "
                        f"(already at {access_role!r})"
                    )
                else:
                    inserted, stored = row[0], row[1]
                    branch = "INSERTED" if inserted else "UPDATED"
                    logger.info(
                        f"[access_role] {branch} user {user_id} "
                        f"(email={email!r}, role -> {stored!r})"
                    )
                return True
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.warning(f"[access_role] Failed to update for user {user_id}: {e}")
        return False


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


def get_user_usage(user_id: str) -> Optional[Dict[str, Any]]:
    """Return a single user's usage row: id, email, name, created_at, last_login,
    token_usage, access_role."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, email, name, created_at, last_login,
                       COALESCE(token_usage, 0) AS token_usage,
                       COALESCE(access_role, 'NONE') AS access_role
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        release_db_connection(conn)


def track_event(
    user_id: str,
    module: str,
    event_type: str,
    *,
    project_id: Optional[str] = None,
    source: str = "web",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert one row into user_module_activity.

    Wrapped in try/except so a tracking failure never breaks the user-facing
    request — mirrors the increment_user_token_usage contract.
    """
    if not user_id or not module or not event_type:
        return
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO user_module_activity
                        (user_id, project_id, module, event_type, source, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        user_id,
                        project_id,
                        module,
                        event_type,
                        source,
                        json.dumps(metadata or {}),
                    ),
                )
                conn.commit()
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.warning(
            f"[track_event] failed user={user_id} module={module} event={event_type}: {e}"
        )


def get_user_module_rollup(user_id: str) -> List[Dict[str, Any]]:
    """Per-module rollup for a single user — events_count and last_event_at.

    Token counts are not derivable from the activity log alone (tokens live on
    users.token_usage as a single cumulative number), so `tokens` is returned
    as 0 for now and the dashboard's bars are sized by event count.
    """
    if not user_id:
        return []
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT module,
                       COUNT(*) AS events_count,
                       MAX(occurred_at) AS last_event_at
                FROM user_module_activity
                WHERE user_id = %s
                GROUP BY module
                ORDER BY events_count DESC
                """,
                (user_id,),
            )
            rows = cursor.fetchall() or []
            return [
                {
                    "id": r["module"],
                    "label": r["module"],
                    "tokens": 0,
                    "events_count": int(r["events_count"]),
                    "last_event_at": (
                        r["last_event_at"].isoformat() if r["last_event_at"] else None
                    ),
                }
                for r in rows
            ]
    finally:
        release_db_connection(conn)


def get_user_recent_events(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Latest N events for a user, newest first. Shape matches frontend UsageEvent type."""
    if not user_id:
        return []
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, module, event_type, occurred_at, metadata
                FROM user_module_activity
                WHERE user_id = %s
                ORDER BY occurred_at DESC
                LIMIT %s
                """,
                (user_id, int(limit)),
            )
            rows = cursor.fetchall() or []
            return [
                {
                    "id": str(r["id"]),
                    "module": r["module"],
                    "action": _humanize_event(r["event_type"], r["metadata"]),
                    "timestamp": r["occurred_at"].isoformat() if r["occurred_at"] else None,
                }
                for r in rows
            ]
    finally:
        release_db_connection(conn)


def _humanize_event(event_type: str, metadata: Optional[Dict[str, Any]]) -> str:
    """Map machine event_type → display string. Falls back to title-cased event_type."""
    labels = {
        "pm_agent_brd_generated": "PM Agent · BRD generated",
        "analyst_agent_brd_generated": "Analyst Agent · BRD generated",
        "test_scenarios_generated_confluence": "Confluence · Test scenarios generated",
        "jira_items_generated_confluence": "Confluence · Jira items generated",
    }
    return labels.get(event_type, event_type.replace("_", " ").capitalize())


def list_all_users_usage() -> List[Dict[str, Any]]:
    """Return every user's usage row, ordered by token_usage desc.

    Used by the admin Organization Usage view. Returns an empty list if the
    table is empty or on read failure (caller decides how to surface).
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, email, name, created_at, last_login,
                       COALESCE(token_usage, 0) AS token_usage,
                       COALESCE(is_active, TRUE) AS is_active,
                       COALESCE(access_role, 'NONE') AS access_role
                FROM users
                ORDER BY token_usage DESC NULLS LAST, last_login DESC NULLS LAST
                """
            )
            rows = cursor.fetchall() or []
            return [dict(r) for r in rows]
    finally:
        release_db_connection(conn)
