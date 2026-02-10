from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import logging

from auth import verify_azure_token
from db_helper import (
    update_user_atlassian_credentials,
    get_user_atlassian_credentials,
    create_or_update_user
)
from services.jira_service import JiraService
from services.confluence_service import ConfluenceService

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
logger = logging.getLogger(__name__)


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
# REQUEST MODELS
# ============================================


class LinkAtlassianRequest(BaseModel):
    domain: str = Field(..., description="Atlassian domain (e.g., mycompany.atlassian.net)")
    email: str = Field(..., description="Email address")
    api_token: str = Field(..., description="Atlassian API token")


@router.post("/atlassian/link")
async def link_atlassian_account(
    request: LinkAtlassianRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Link user's Atlassian account by saving credentials
    
    The credentials will be validated by testing the connection to Jira.
    If successful, they will be saved to the database.
    """
    
    # Validate credentials by testing connection
    jira_service = JiraService(request.domain, request.email, request.api_token)
    
    success, error_message = jira_service.test_connection()
    if not success:
        raise HTTPException(
            status_code=400,
            detail=error_message or "Invalid Atlassian credentials. Please check your domain, email, and API token."
        )
    
    # Save credentials to database
    try:
        update_user_atlassian_credentials(
            user_id=current_user['id'],
            domain=request.domain,
            email=request.email,
            api_token=request.api_token
        )
        
        return {
            "status": "success",
            "message": "Atlassian account linked successfully"
        }
    except Exception as e:
        logger.error(f"Error linking Atlassian account: {e}")
        raise HTTPException(status_code=500, detail="Failed to link Atlassian account")


@router.get("/atlassian/status")
async def get_atlassian_status(current_user: dict = Depends(get_current_user)):
    """
    Check if user has linked their Atlassian account
    
    Returns:
        - linked: bool - Whether account is linked
        - domain: str (optional) - Atlassian domain
        - email: str (optional) - Email used for authentication
        - linked_at: timestamp (optional) - When the account was linked
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if credentials and credentials.get('atlassian_api_token'):
        return {
            "linked": True,
            "domain": credentials.get('atlassian_domain'),
            "email": credentials.get('atlassian_email'),
            "linked_at": int(credentials['atlassian_linked_at'].timestamp() * 1000) if credentials.get('atlassian_linked_at') else None
        }
    
    return {"linked": False}


@router.get("/jira/projects")
async def list_jira_projects(current_user: dict = Depends(get_current_user)):
    """
    List all accessible Jira projects for the linked Atlassian account
    
    Returns:
        List of Jira projects with key, name, id, and type
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        projects = jira_service.get_projects()
        return {"projects": projects}
    
    except Exception as e:
        logger.error(f"Error fetching Jira projects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/spaces")
async def list_confluence_spaces(current_user: dict = Depends(get_current_user)):
    """
    List all accessible Confluence spaces for the linked Atlassian account
    
    Returns:
        List of Confluence spaces with key, name, id, and type
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        spaces = confluence_service.get_spaces()
        return {"spaces": spaces}
    
    except Exception as e:
        logger.error(f"Error fetching Confluence spaces: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/jira/issues/{project_key}")
async def get_jira_issues(
    project_key: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch Jira issues for a specific project
    
    Args:
        project_key: The Jira project key (e.g., 'PROJ', 'DEV')
        
    Returns:
        List of Jira issues from the specified project
    """
    logger.info(f"Fetching Jira issues for project_key: '{project_key}' (user: {current_user['id']})")
    
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        logger.info(f"Using Jira domain: {credentials['atlassian_domain']}")
        issues = jira_service.get_project_issues(project_key, max_results=100)
        logger.info(f"Successfully fetched {len(issues)} issues for project {project_key}")
        return {"issues": issues, "total": len(issues)}
    
    except Exception as e:
        logger.error(f"Error fetching Jira issues for project {project_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

