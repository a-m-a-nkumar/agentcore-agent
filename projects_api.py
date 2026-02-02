"""
FastAPI Router for Project Management
Handles all project-related API endpoints
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import logging

# Import database helper functions
from db_helper import (
    create_project,
    get_user_projects,
    get_project,
    update_project,
    delete_project,
    create_or_update_user
)

# Import authentication from auth.py
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

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    """
    Get current user from Azure AD token
    Creates/updates user in database if needed
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
async def get_projects(current_user: dict = Depends(get_current_user)):
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
async def create_new_project(
    project_data: ProjectCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new project
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
        
        # Convert timestamps to milliseconds
        project['created_at'] = int(project['created_at'].timestamp() * 1000)
        project['updated_at'] = int(project['updated_at'].timestamp() * 1000)
        
        return project
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        raise HTTPException(status_code=500, detail="Failed to create project")


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project_by_id(
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
async def update_project_by_id(
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
        
        # Convert timestamps
        updated_project['created_at'] = int(updated_project['created_at'].timestamp() * 1000)
        updated_project['updated_at'] = int(updated_project['updated_at'].timestamp() * 1000)
        
        return updated_project
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating project: {e}")
        raise HTTPException(status_code=500, detail="Failed to update project")


@router.delete("/{project_id}", status_code=204)
async def delete_project_by_id(
    project_id: str,
    hard_delete: bool = False,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a project (soft delete by default)
    """
    try:
        # Verify project exists and user owns it
        existing_project = get_project(project_id)
        if not existing_project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if existing_project["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to delete this project")
        
        # Delete project
        delete_project(project_id, hard_delete=hard_delete)
        
        return None  # 204 No Content
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting project: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete project")
