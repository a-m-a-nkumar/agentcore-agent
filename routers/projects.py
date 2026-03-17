"""
FastAPI Router for Project Management
Handles all project-related API endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
import logging
import traceback
import time

# Import database helper functions - assuming app run from root
from db_helper import (
    create_project,
    get_user_projects,
    get_project,
    update_project,
    delete_project,
    create_or_update_user,
    save_project_brd_session,
    get_project_brd_session
)

# Import authentication from auth.py - assuming app run from root
from auth import verify_azure_token

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/projects", tags=["projects"])


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class ProjectCreate(BaseModel):
    project_id: str
    project_name: str
    description: Optional[str] = None
    jira_project_key: Optional[str] = None
    confluence_space_key: Optional[str] = None


class ProjectUpdate(BaseModel):
    project_name: Optional[str] = None
    description: Optional[str] = None
    jira_project_key: Optional[str] = None
    confluence_space_key: Optional[str] = None


class BrdSessionUpdate(BaseModel):
    brd_id: Optional[str] = None
    session_id: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    project_name: str
    description: Optional[str]
    jira_project_key: Optional[str]
    confluence_space_key: Optional[str]
    created_at: int  # Unix timestamp in milliseconds
    updated_at: int  # Unix timestamp in milliseconds
    is_deleted: bool


# ============================================
# AUTHENTICATION DEPENDENCY
# ============================================

def get_current_user(token_data: dict = Depends(verify_azure_token)):
    """
    Get current user from Azure AD token
    Creates/updates user in database if needed.
    Using def (not async def) so FastAPI runs this in a thread pool,
    preventing synchronous DB calls from blocking the event loop.
    """
    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")

    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")

    # Create or update user in database
    try:
        user = create_or_update_user(user_id, email, name)
        return user
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ============================================
# API ENDPOINTS
# ============================================

@router.get("/", response_model=List[ProjectResponse])
def get_projects(current_user: dict = Depends(get_current_user)):
    """
    Get all projects for the current user
    """
    try:
        user_id = current_user["id"]
        projects = get_user_projects(user_id, include_deleted=False)
        return projects
    except Exception as e:
        logger.error(f"Error fetching projects: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch projects")


@router.post("/", response_model=ProjectResponse, status_code=201)
def create_new_project(
    project_data: ProjectCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new project
    Triggers initial sync if Jira or Confluence keys are provided
    """
    try:
        user_id = current_user["id"]
        
        project = create_project(
            project_id=project_data.project_id,
            user_id=user_id,
            project_name=project_data.project_name,
            description=project_data.description,
            jira_project_key=project_data.jira_project_key,
            confluence_space_key=project_data.confluence_space_key
        )
        
        # Trigger initial sync in background if Jira or Confluence is configured
        if project_data.jira_project_key or project_data.confluence_space_key:
            from services.sync_service import sync_project
            background_tasks.add_task(
                sync_project,
                project_id=project_data.project_id,
                user_id=user_id,
                sync_type='initial'
            )
            logger.info(f"Initial sync triggered for project {project_data.project_id}")
        
        # Convert timestamps to milliseconds
        project['created_at'] = int(project['created_at'].timestamp() * 1000)
        project['updated_at'] = int(project['updated_at'].timestamp() * 1000)
        
        return project
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        raise HTTPException(status_code=500, detail="Failed to create project")


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project_by_id(
    project_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific project by ID
    """
    try:
        project = get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Verify ownership
        if project["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to access this project")
        
        return project
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching project: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch project")


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project_by_id(
    project_id: str,
    project_data: ProjectUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update a project
    """
    try:
        # Verify project exists and user owns it
        existing_project = get_project(project_id)
        if not existing_project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if existing_project["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to update this project")
        
        # Update project
        updated_project = update_project(
            project_id=project_id,
            project_name=project_data.project_name,
            description=project_data.description,
            jira_project_key=project_data.jira_project_key,
            confluence_space_key=project_data.confluence_space_key
        )

        if not updated_project:
             raise HTTPException(status_code=404, detail="Project not found or update failed")
        
        # Convert timestamps
        updated_project['created_at'] = int(updated_project['created_at'].timestamp() * 1000)
        updated_project['updated_at'] = int(updated_project['updated_at'].timestamp() * 1000)
        
        return updated_project
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error updating project: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update project: {str(e)}")


@router.delete("/{project_id}", status_code=204)
def delete_project_by_id(
    project_id: str,
    hard_delete: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a project (hard delete by default)
    """
    try:
        # Verify project exists and user owns it
        existing_project = get_project(project_id)
        if not existing_project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if existing_project["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to delete this project")
        
        # Delete project
        start_time = time.time()
        logger.info(f"[PERF] Starting delete for project: {project_id}")
        
        result = delete_project(project_id, hard_delete=hard_delete)
        
        duration = (time.time() - start_time) * 1000
        logger.info(f"[PERF] Delete for project {project_id} took {duration:.2f}ms")
        
        if not result:
             raise HTTPException(status_code=404, detail="Project not found or delete failed")

        return None  # 204 No Content
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error deleting project: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {str(e)}")


# ============================================
# BRD SESSION PERSISTENCE
# ============================================

@router.get("/{project_id}/brd-session")
def get_brd_session(
    project_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get saved BRD session (brd_id + agentcore_session_id) for a project.
    Returns null fields if no session exists yet.
    """
    try:
        # Verify project exists and user owns it
        existing_project = get_project(project_id)
        if not existing_project:
            raise HTTPException(status_code=404, detail="Project not found")
        if existing_project["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")

        session_data = get_project_brd_session(project_id)
        if session_data:
            return {
                "brd_id": session_data.get("brd_id"),
                "session_id": session_data.get("agentcore_session_id")
            }
        return {"brd_id": None, "session_id": None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching BRD session: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch BRD session")


@router.put("/{project_id}/brd-session")
def save_brd_session(
    project_id: str,
    data: BrdSessionUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Save/update BRD session (brd_id + session_id) for a project.
    """
    try:
        # Verify project exists and user owns it
        existing_project = get_project(project_id)
        if not existing_project:
            raise HTTPException(status_code=404, detail="Project not found")
        if existing_project["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")

        save_project_brd_session(
            project_id=project_id,
            brd_id=data.brd_id,
            agentcore_session_id=data.session_id
        )
        return {"status": "ok", "project_id": project_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving BRD session: {e}")
        raise HTTPException(status_code=500, detail="Failed to save BRD session")
