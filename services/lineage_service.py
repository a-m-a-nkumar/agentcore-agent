"""
Lineage Service — CRUD operations for the artifact_lineage table.

Tracks the relationship between source BRD requirements (Confluence)
and generated artifacts (Jira stories, test scenarios).
"""

import json
import logging
from typing import Optional, List, Dict, Any
from psycopg2.extras import RealDictCursor

from db_helper import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def record_lineage(
    project_id: str,
    user_id: str,
    source_type: str,
    source_id: str,
    source_section_id: str,
    source_version: int,
    source_content_hash: str,
    target_type: str,
    target_id: str,
    target_content_hash: str,
    target_metadata: dict,
    original_generated_content: dict,
    workspace_key: Optional[str] = None,
) -> Optional[dict]:
    """
    Insert a new lineage record linking a source requirement to a generated artifact.
    Returns the created row as a dict, or None on failure.

    `workspace_key` is optional only for backwards compatibility with callers
    that haven't been migrated yet. New callers MUST pass it (compute via
    services.workspace.compute_workspace_key) so the row participates in
    shared-source lookups.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                INSERT INTO artifact_lineage (
                    project_id, user_id, workspace_key,
                    source_type, source_id, source_section_id,
                    source_version, source_content_hash,
                    target_type, target_id, target_content_hash,
                    target_metadata, original_generated_content,
                    status
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s::jsonb, %s::jsonb,
                    'current'
                )
                RETURNING *
            """, (
                project_id, user_id, workspace_key,
                source_type, source_id, source_section_id,
                source_version, source_content_hash,
                target_type, target_id, target_content_hash,
                json.dumps(target_metadata),
                json.dumps(original_generated_content),
            ))
            row = cursor.fetchone()
            conn.commit()
            logger.info(
                f"Recorded lineage: {source_type}:{source_id}/{source_section_id} "
                f"-> {target_type}:{target_id} (project={project_id} ws={workspace_key})"
            )
            return dict(row) if row else None
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to record lineage: {e}")
        return None
    finally:
        if conn:
            release_db_connection(conn)


def get_lineage_by_source_workspace(
    workspace_key: str,
    source_id: str,
    source_section_id: str,
    status: str = "current",
) -> List[dict]:
    """
    Forward lookup keyed on workspace, not project. This is the lookup the
    Jira Sync scanner uses so Alice and Bob (both pointed at the same
    Confluence space + Jira project) see the same downstream artifacts.

    Use get_lineage_by_source(project_id, ...) only for legacy single-project
    callers.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM artifact_lineage
                 WHERE workspace_key = %s
                   AND source_id = %s
                   AND source_section_id = %s
                   AND status = %s
                 ORDER BY created_at DESC
            """, (workspace_key, source_id, source_section_id, status))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get lineage by source workspace: {e}")
        return []
    finally:
        if conn:
            release_db_connection(conn)


def bump_source_version_for_page(
    workspace_key: str,
    source_id: str,
    new_source_version: int,
) -> int:
    """
    After a successful Apply, mark every 'current' lineage row for this
    BRD page as reconciled at the given version. Returns the number of
    rows updated.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE artifact_lineage
                   SET source_version = %s
                 WHERE workspace_key = %s
                   AND source_id = %s
                   AND status = 'current'
            """, (new_source_version, workspace_key, source_id))
            updated = cursor.rowcount
            conn.commit()
            return updated
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to bump source_version: {e}")
        return 0
    finally:
        if conn:
            release_db_connection(conn)


def get_lineage_by_source(
    project_id: str,
    source_id: str,
    source_section_id: str,
    status: str = "current",
) -> List[dict]:
    """
    Forward lookup: find all artifacts generated from a specific source requirement.
    Used by Change Detection to answer "what came from FR-7 on page X?"
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM artifact_lineage
                WHERE project_id = %s
                  AND source_id = %s
                  AND source_section_id = %s
                  AND status = %s
                ORDER BY created_at DESC
            """, (project_id, source_id, source_section_id, status))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get lineage by source: {e}")
        return []
    finally:
        if conn:
            release_db_connection(conn)


def get_lineage_targets_for_workspace(
    workspace_key: str,
    target_type: Optional[str] = None,
    status: str = "current",
) -> List[dict]:
    """
    List every generated artifact (lineage row) in a workspace, optionally
    filtered by target_type. This is the candidate set for DRIFT detection:
    the reverse scan walks each current jira_story target and checks whether
    its live content still matches what we last knew was in sync.

    Workspace-scoped (not project) so the drift view is complete for everyone
    pointed at the same Confluence space + Jira project.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if target_type:
                cursor.execute("""
                    SELECT * FROM artifact_lineage
                     WHERE workspace_key = %s
                       AND target_type = %s
                       AND status = %s
                     ORDER BY created_at DESC
                """, (workspace_key, target_type, status))
            else:
                cursor.execute("""
                    SELECT * FROM artifact_lineage
                     WHERE workspace_key = %s
                       AND status = %s
                     ORDER BY created_at DESC
                """, (workspace_key, status))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get lineage targets for workspace: {e}")
        return []
    finally:
        if conn:
            release_db_connection(conn)


def update_target_snapshot(
    lineage_id: str,
    snapshot: dict,
    content_hash: str,
) -> int:
    """
    Refresh a lineage row's stored artifact snapshot to whatever we just pushed
    to the live system (forward apply, or a story-wrong revert). This keeps the
    drift "baseline" honest: a story is only flagged when live Jira differs from
    the most recent content WE wrote — so our own applied changes never
    re-appear as drift. Returns rows updated (0 or 1).
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE artifact_lineage
                   SET original_generated_content = %s::jsonb,
                       target_content_hash = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = %s
            """, (json.dumps(snapshot), content_hash, lineage_id))
            updated = cursor.rowcount
            conn.commit()
            return updated
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to update target snapshot for {lineage_id}: {e}")
        return 0
    finally:
        if conn:
            release_db_connection(conn)


def get_lineage_by_target(
    project_id: str,
    target_type: str,
    target_id: str,
) -> List[dict]:
    """
    Reverse lookup: find the source requirement for a given artifact.
    Used by Drift Detection to answer "where did PROJ-22 come from?"
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM artifact_lineage
                WHERE project_id = %s
                  AND target_type = %s
                  AND target_id = %s
                ORDER BY created_at DESC
            """, (project_id, target_type, target_id))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get lineage by target: {e}")
        return []
    finally:
        if conn:
            release_db_connection(conn)


def get_lineage_for_project(
    project_id: str,
    status: Optional[str] = None,
) -> List[dict]:
    """
    List all lineage records for a project, optionally filtered by status.
    Used by the Consistency Guardian to answer "show me all stale artifacts."
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if status:
                cursor.execute("""
                    SELECT * FROM artifact_lineage
                    WHERE project_id = %s AND status = %s
                    ORDER BY created_at DESC
                """, (project_id, status))
            else:
                cursor.execute("""
                    SELECT * FROM artifact_lineage
                    WHERE project_id = %s
                    ORDER BY created_at DESC
                """, (project_id,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get lineage for project: {e}")
        return []
    finally:
        if conn:
            release_db_connection(conn)
