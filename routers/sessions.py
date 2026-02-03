"""
FastAPI Router for Session Management
Handles all analyst session-related API endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
import logging

# Import database helper functions
from db_helper import (
    create_session,
    get_project_sessions,
    get_session,
    update_session,
    delete_session,
    increment_message_count,
    get_project
)

# Import authentication from projects router
# Changed from 'projects_api' to '.projects' as they are in the same package
from .projects import get_current_user

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class SessionCreate(BaseModel):
    session_id: str
    project_id: str
    title: Optional[str] = "New Chat"


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    brd_id: Optional[str] = None
    message_count: Optional[int] = None


class SessionResponse(BaseModel):
    id: str
    project_id: str
    user_id: str
    title: str
    brd_id: Optional[str]
    message_count: int
    created_at: int  # Unix timestamp in milliseconds
    last_updated: int  # Unix timestamp in milliseconds
    is_deleted: bool


# ============================================
# API ENDPOINTS
# ============================================

@router.get("/", response_model=List[SessionResponse])
async def get_sessions(
    project_id: str = Query(..., description="Project ID to filter sessions"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all sessions for a project
    Simple query - NO JOIN NEEDED
    """
    try:
        user_id = current_user["id"]
        
        # Verify user owns the project
        project = get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this project")
        
        # Get sessions for project
        sessions = get_project_sessions(project_id, user_id, include_deleted=False)
        return sessions
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching sessions: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch sessions")


@router.post("/", response_model=SessionResponse, status_code=201)
async def create_new_session(
    session_data: SessionCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new session
    """
    try:
        user_id = current_user["id"]
        
        # Verify user owns the project
        project = get_project(session_data.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to create session in this project")
        
        # Create session
        session = create_session(
            session_id=session_data.session_id,
            project_id=session_data.project_id,
            user_id=user_id,
            title=session_data.title
        )
        
        return session
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session_by_id(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific session by ID
    """
    try:
        session = get_session(session_id)
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Verify ownership
        if session["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to access this session")
        
        return session
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching session: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch session")


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session_by_id(
    session_id: str,
    session_data: SessionUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update a session
    """
    try:
        # Verify session exists and user owns it
        existing_session = get_session(session_id)
        if not existing_session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if existing_session["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to update this session")
        
        # Update session
        updated_session = update_session(
            session_id=session_id,
            title=session_data.title,
            brd_id=session_data.brd_id,
            message_count=session_data.message_count
        )
        
        return updated_session
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating session: {e}")
        raise HTTPException(status_code=500, detail="Failed to update session")


@router.post("/{session_id}/increment-messages", response_model=dict)
async def increment_session_messages(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Increment message count for a session
    """
    try:
        # Verify session exists and user owns it
        existing_session = get_session(session_id)
        if not existing_session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if existing_session["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to update this session")
        
        # Increment count
        new_count = increment_message_count(session_id)
        
        return {"session_id": session_id, "message_count": new_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error incrementing message count: {e}")
        raise HTTPException(status_code=500, detail="Failed to increment message count")


@router.delete("/{session_id}", status_code=204)
async def delete_session_by_id(
    session_id: str,
    hard_delete: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a session (soft delete by default)
    """
    try:
        # Verify session exists and user owns it
        existing_session = get_session(session_id)
        if not existing_session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if existing_session["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to delete this session")
        
        # Delete session
        delete_session(session_id, hard_delete=hard_delete)
        
        return None  # 204 No Content
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete session")
