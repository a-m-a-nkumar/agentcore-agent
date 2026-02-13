"""
Database helper functions for vector database tables
Handles operations for confluence_pages, jira_issues, and document_embeddings
"""
 
from db_helper import get_db_connection, release_db_connection
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Optional, Any
import json
 
 
# ============================================
# Confluence Pages Functions
# ============================================
 
def upsert_confluence_page(
    project_id: str,
    user_id: str,
    page_id: str,
    space_key: str,
    title: str,
    url: str,
    version_number: int,
    last_modified_at: str
) -> Dict:
    """Insert or update a Confluence page"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            INSERT INTO confluence_pages (
                project_id, user_id, page_id, space_key, title, url,
                version_number, last_modified_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, page_id)
            DO UPDATE SET
                title = EXCLUDED.title,
                url = EXCLUDED.url,
                version_number = EXCLUDED.version_number,
                last_modified_at = EXCLUDED.last_modified_at,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
        """, (project_id, user_id, page_id, space_key, title, url, version_number, last_modified_at))
       
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
       
        return dict(result) if result else None
    finally:
        release_db_connection(conn)
 
 
def get_confluence_page(project_id: str, page_id: str) -> Optional[Dict]:
    """Get a Confluence page by project_id and page_id"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT * FROM confluence_pages
            WHERE project_id = %s AND page_id = %s
        """, (project_id, page_id))
       
        result = cursor.fetchone()
        cursor.close()
       
        return dict(result) if result else None
    finally:
        release_db_connection(conn)
 
 
def get_all_confluence_pages(project_id: str) -> List[Dict]:
    """Get all Confluence pages for a project"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT * FROM confluence_pages
            WHERE project_id = %s
            ORDER BY last_modified_at DESC
        """, (project_id,))
       
        results = cursor.fetchall()
        cursor.close()
       
        return [dict(row) for row in results]
    finally:
        release_db_connection(conn)


# ============================================
# Jira Issues Functions
# ============================================
 
def upsert_jira_issue(
    project_id: str,
    user_id: str,
    issue_key: str,
    issue_id: str,
    project_key: str,
    summary: str,
    url: str,
    issue_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    story_points: Optional[float] = None,
    original_estimate_seconds: Optional[int] = None,
    time_spent_seconds: Optional[int] = None,
    remaining_estimate_seconds: Optional[int] = None,
    sprint_name: Optional[str] = None,
    sprint_id: Optional[str] = None,
    labels: Optional[List[str]] = None,
    components: Optional[List[str]] = None,
    created_date: Optional[str] = None,
    updated_date: str = None,
    resolved_date: Optional[str] = None,
    actual_duration_days: Optional[float] = None,
    metadata: Optional[Dict] = None
) -> Dict:
    """Insert or update a Jira issue"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            INSERT INTO jira_issues (
                project_id, user_id, issue_key, issue_id, project_key, summary, url,
                issue_type, status, priority, story_points,
                original_estimate_seconds, time_spent_seconds, remaining_estimate_seconds,
                sprint_name, sprint_id, labels, components,
                created_date, updated_date, resolved_date, actual_duration_days, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, issue_key)
            DO UPDATE SET
                summary = EXCLUDED.summary,
                url = EXCLUDED.url,
                issue_type = EXCLUDED.issue_type,
                status = EXCLUDED.status,
                priority = EXCLUDED.priority,
                story_points = EXCLUDED.story_points,
                original_estimate_seconds = EXCLUDED.original_estimate_seconds,
                time_spent_seconds = EXCLUDED.time_spent_seconds,
                remaining_estimate_seconds = EXCLUDED.remaining_estimate_seconds,
                sprint_name = EXCLUDED.sprint_name,
                sprint_id = EXCLUDED.sprint_id,
                labels = EXCLUDED.labels,
                components = EXCLUDED.components,
                updated_date = EXCLUDED.updated_date,
                resolved_date = EXCLUDED.resolved_date,
                actual_duration_days = EXCLUDED.actual_duration_days,
                metadata = EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
        """, (
            project_id, user_id, issue_key, issue_id, project_key, summary, url,
            issue_type, status, priority, story_points,
            original_estimate_seconds, time_spent_seconds, remaining_estimate_seconds,
            sprint_name, sprint_id, labels, components,
            created_date, updated_date, resolved_date, actual_duration_days,
            json.dumps(metadata) if metadata else '{}'
        ))
       
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
       
        return dict(result) if result else None
    finally:
        release_db_connection(conn)
 
 
def get_jira_issue(project_id: str, issue_key: str) -> Optional[Dict]:
    """Get a Jira issue by project_id and issue_key"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT * FROM jira_issues
            WHERE project_id = %s AND issue_key = %s
        """, (project_id, issue_key))
       
        result = cursor.fetchone()
        cursor.close()
       
        return dict(result) if result else None
    finally:
        release_db_connection(conn)


def get_all_jira_issues(project_id: str) -> List[Dict]:
    """Get all Jira issues for a project"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT * FROM jira_issues
            WHERE project_id = %s
            ORDER BY updated_date DESC
        """, (project_id,))
       
        results = cursor.fetchall()
        cursor.close()
       
        return [dict(row) for row in results]
    finally:
        release_db_connection(conn)


# ============================================
# Document Embeddings Functions
# ============================================
 
def insert_document_embedding(
    project_id: str,
    user_id: str,
    source_type: str,
    source_id: str,
    title: str,
    content_chunk: str,
    chunk_index: int,
    embedding: List[float],
    url: Optional[str] = None,
    metadata: Optional[Dict] = None
) -> Dict:
    """Insert a document embedding"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
       
        # Convert embedding list to pgvector format
        embedding_str = '[' + ','.join(map(str, embedding)) + ']'
       
        cursor.execute("""
            INSERT INTO document_embeddings (
                project_id, user_id, source_type, source_id, title,
                content_chunk, chunk_index, embedding, url, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
            RETURNING *
        """, (
            project_id, user_id, source_type, source_id, title,
            content_chunk, chunk_index, embedding_str, url,
            json.dumps(metadata) if metadata else '{}'
        ))
       
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
       
        return dict(result) if result else None
    finally:
        release_db_connection(conn)
 
 
def delete_embeddings(project_id: str, source_type: str, source_id: str):
    """Delete all embeddings for a specific source"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            DELETE FROM document_embeddings
            WHERE project_id = %s AND source_type = %s AND source_id = %s
        """, (project_id, source_type, source_id))
       
        deleted_count = cursor.rowcount
        conn.commit()
        cursor.close()
       
        return deleted_count
    finally:
        release_db_connection(conn)
 
 
def search_embeddings(
    project_id: str,
    query_embedding: List[float],
    limit: int = 5,
    source_type: Optional[str] = None
) -> List[Dict]:
    """
    Search for similar embeddings using vector similarity
   
    Args:
        project_id: Project ID to search within
        query_embedding: Query vector
        limit: Number of results to return
        source_type: Optional filter by source type ('confluence' or 'jira')
   
    Returns:
        List of similar documents with similarity scores
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
       
        # Convert embedding to pgvector format
        embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'
       
        # Build query
        if source_type:
            query = """
                SELECT *,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM document_embeddings
                WHERE project_id = %s AND source_type = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (embedding_str, project_id, source_type, embedding_str, limit)
        else:
            query = """
                SELECT *,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM document_embeddings
                WHERE project_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (embedding_str, project_id, embedding_str, limit)
       
        cursor.execute(query, params)
        results = cursor.fetchall()
        cursor.close()
       
        return [dict(row) for row in results]
    finally:
        release_db_connection(conn)
 
 
def get_surrounding_chunks(
    project_id: str,
    source_id: str,
    chunk_index: int,
    window: int = 1
) -> Dict[str, Optional[str]]:
    """
    Get chunks before and after a specific chunk for context
   
    Args:
        project_id: Project ID
        source_id: Source document ID
        chunk_index: Index of the matched chunk
        window: Number of chunks before/after to retrieve
   
    Returns:
        Dict with 'before' and 'after' chunk content
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
       
        # Get chunk before
        cursor.execute("""
            SELECT content_chunk FROM document_embeddings
            WHERE project_id = %s AND source_id = %s AND chunk_index = %s
        """, (project_id, source_id, chunk_index - window))
        before_result = cursor.fetchone()
       
        # Get chunk after
        cursor.execute("""
            SELECT content_chunk FROM document_embeddings
            WHERE project_id = %s AND source_id = %s AND chunk_index = %s
        """, (project_id, source_id, chunk_index + window))
        after_result = cursor.fetchone()
       
        cursor.close()
       
        return {
            'before': before_result['content_chunk'] if before_result else None,
            'after': after_result['content_chunk'] if after_result else None
        }
    finally:
        release_db_connection(conn)


def get_surrounding_chunks_batch(
    project_id: str,
    chunk_identifiers: List[Dict[str, Any]],
    window: int = 1
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Get surrounding chunks for multiple chunks using a single DB connection and query
    Args:
        project_id: Project ID
        chunk_identifiers: List of dicts with 'source_id' and 'chunk_index'
        window: Number of chunks before/after to retrieve
    Returns:
        Dict keyed by f"{source_id}_{chunk_index}" containing 'before' and 'after'
    """
    if not chunk_identifiers:
        return {}

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build a list of all (source_id, chunk_index) tuples we need to fetch
        # For each chunk, we need: chunk-1 (before) and chunk+1 (after)
        fetch_list = []
        for item in chunk_identifiers:
            source_id = item['source_id']
            chunk_index = item['chunk_index']
            # Add before chunk
            fetch_list.append((source_id, chunk_index - window))
            # Add after chunk
            fetch_list.append((source_id, chunk_index + window))

        # Remove duplicates while preserving order
        seen = set()
        unique_fetch_list = []
        for sid, cidx in fetch_list:
            key = (sid, cidx)
            if key not in seen:
                seen.add(key)
                unique_fetch_list.append(key)

        # Single batch query using VALUES and IN clause
        # Build the query dynamically
        if not unique_fetch_list:
            return {}

        # Use psycopg2's execute_values for efficient batch query
        from psycopg2.extras import execute_values

        query = """
            SELECT source_id, chunk_index, content_chunk
            FROM document_embeddings
            WHERE project_id = %s
            AND (source_id, chunk_index) IN %s
        """

        # Execute batch query
        cursor.execute(query, (project_id, tuple(unique_fetch_list)))
        rows = cursor.fetchall()

        # Build a lookup map: (source_id, chunk_index) -> content_chunk
        chunk_map = {}
        for row in rows:
            key = (row['source_id'], row['chunk_index'])
            chunk_map[key] = row['content_chunk']

        # Build results for each original chunk
        results = {}
        for item in chunk_identifiers:
            source_id = item['source_id']
            chunk_index = item['chunk_index']
            result_key = f"{source_id}_{chunk_index}"

            results[result_key] = {
                'before': chunk_map.get((source_id, chunk_index - window)),
                'after': chunk_map.get((source_id, chunk_index + window))
            }

        cursor.close()
        return results
    finally:
        release_db_connection(conn)
