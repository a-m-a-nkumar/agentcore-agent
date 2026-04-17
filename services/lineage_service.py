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
) -> Optional[dict]:
    """
    Insert a new lineage record linking a source requirement to a generated artifact.
    Returns the created row as a dict, or None on failure.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                INSERT INTO artifact_lineage (
                    project_id, user_id,
                    source_type, source_id, source_section_id,
                    source_version, source_content_hash,
                    target_type, target_id, target_content_hash,
                    target_metadata, original_generated_content,
                    status
                ) VALUES (
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s::jsonb, %s::jsonb,
                    'current'
                )
                RETURNING *
            """, (
                project_id, user_id,
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
                f"-> {target_type}:{target_id} (project={project_id})"
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
