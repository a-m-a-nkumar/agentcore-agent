"""
Sync Router - API endpoints for syncing Confluence and Jira data
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from services.sync_service import sync_project
from auth import verify_azure_token
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ============================================
# AUTHENTICATION DEPENDENCY
# ============================================

def get_current_user(token_data: dict = Depends(verify_azure_token)):
    """
    Get current user from Azure AD token.
    Using def (not async def) so FastAPI runs this in a thread pool.
    """
    from db_helper import create_or_update_user

    if token_data is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")

    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")

    try:
        user = create_or_update_user(user_id, email, name)
        return user
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


class SyncRequest(BaseModel):
    sync_type: Optional[str] = 'incremental'  # 'initial' or 'incremental'


@router.post("/projects/{project_id}/sync")
def trigger_sync(
    project_id: str,
    background_tasks: BackgroundTasks,
    sync_request: SyncRequest = SyncRequest(),
    current_user: dict = Depends(get_current_user)
):
    """
    Trigger a sync for a project's Confluence and Jira data
    
    - **project_id**: Project ID to sync
    - **sync_type**: 'initial' (sync everything) or 'incremental' (only changed items)
    """
    try:
        # Add sync task to background
        background_tasks.add_task(
            sync_project,
            project_id=project_id,
            user_id=current_user['id'],
            sync_type=sync_request.sync_type
        )
        
        return {
            "status": "sync_started",
            "project_id": project_id,
            "sync_type": sync_request.sync_type,
            "message": f"{sync_request.sync_type.capitalize()} sync started in background"
        }
    
    except Exception as e:
        logger.error(f"Error triggering sync for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/status")
def get_sync_status(
    project_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get sync status for a project.
    Returns counts of synced pages/issues and whether a sync is currently in progress.
    """
    try:
        from db_helper_vector import get_sync_status_counts
        from db_helper import get_project
        from services.sync_service import get_sync_progress

        # Check in-memory sync progress first (lightweight, no DB hit)
        progress = get_sync_progress(project_id)
        is_syncing = progress.get('is_syncing', False) if progress else False
        sync_message = progress.get('message', '') if progress else ''

        # Get project (1 DB call)
        project = get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # OPTIMIZATION: Get all counts + last sync times in 1 DB call instead of 3
        counts = get_sync_status_counts(project_id)

        return {
            "project_id": project_id,
            "project_name": project['project_name'],
            "is_syncing": is_syncing,
            "sync_message": sync_message,
            "confluence_pages": counts['page_count'],
            "jira_issues": counts['issue_count'],
            "total_embeddings": counts['embedding_count'],
            "last_synced": {
                "confluence": counts['last_confluence_sync'],
                "jira": counts['last_jira_sync']
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sync status for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
