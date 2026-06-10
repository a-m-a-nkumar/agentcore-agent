"""
Workspace identity helpers.

A "workspace" is the (confluence_space_key, jira_project_key) pair that
identifies the shared source of truth two or more projects can point at.
Lineage and Jira Sync state are keyed on the workspace_key so users
mapped to the same source see the same data without re-running scans.
"""

import hashlib
import logging
from typing import Optional

from psycopg2.extras import RealDictCursor

from db_helper import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def compute_workspace_key(
    confluence_space_key: Optional[str],
    jira_project_key: Optional[str],
) -> str:
    """
    Deterministic 32-hex workspace identifier derived from the source pair.

    Must match the SQL backfill in add_workspace_key_to_lineage.py:
        substr(encode(digest(<space>||'|'||<jira>, 'sha1'), 'hex'), 1, 32)
    """
    space = (confluence_space_key or "").strip()
    jira = (jira_project_key or "").strip()
    raw = f"{space}|{jira}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:32]


def get_workspace_key_for_project(project_id: str) -> Optional[str]:
    """
    Look up a project's source pair and compute its workspace_key.
    Returns None if the project doesn't exist.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT confluence_space_key, jira_project_key
                  FROM projects
                 WHERE id = %s
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return compute_workspace_key(
                row["confluence_space_key"],
                row["jira_project_key"],
            )
    except Exception as e:
        logger.error(f"Failed to look up workspace_key for project {project_id}: {e}")
        return None
    finally:
        if conn:
            release_db_connection(conn)


def verify_user_has_workspace_access(user_id: str, workspace_key: str) -> bool:
    """
    True iff the user owns at least one (non-deleted) project that resolves
    to this workspace_key. This is the access boundary at every API endpoint
    that serves workspace-scoped data.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, confluence_space_key, jira_project_key
                  FROM projects
                 WHERE user_id = %s
                   AND coalesce(is_deleted, FALSE) = FALSE
                """,
                (user_id,),
            )
            for _id, space, jira in cursor.fetchall():
                if compute_workspace_key(space, jira) == workspace_key:
                    return True
        return False
    except Exception as e:
        logger.error(f"workspace access check failed for user={user_id} ws={workspace_key}: {e}")
        return False
    finally:
        if conn:
            release_db_connection(conn)
