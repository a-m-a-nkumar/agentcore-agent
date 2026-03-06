from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import logging
import boto3
import json
import os
from datetime import datetime

from auth import verify_azure_token
from db_helper import (
    update_user_atlassian_credentials,
    get_user_atlassian_credentials,
    create_or_update_user,
    get_project
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


class UploadBRDToConfluenceRequest(BaseModel):
    brd_id: str = Field(..., description="BRD ID to upload")
    project_id: str = Field(..., description="Project ID")
    page_title: Optional[str] = Field(None, description="Custom page title (optional)")


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


@router.get("/confluence/pages")
async def list_confluence_pages(
    space_key: str = "SO",
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
):
    """
    List pages in a Confluence space using the current user's linked Atlassian credentials.
    Replaces frontend calling /confluence-api/ with hardcoded auth.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        results = confluence_service.get_content_pages(space_key=space_key, limit=limit)
        return {"results": results}
    except Exception as e:
        logger.error(f"Error fetching Confluence pages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/pages/{page_id}")
async def get_confluence_page(
    page_id: str,
    expand: str = "body.storage,version,ancestors",
    current_user: dict = Depends(get_current_user),
):
    """
    Get a Confluence page by ID using the current user's linked Atlassian credentials.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        return confluence_service.get_content_page_by_id(page_id=page_id, expand=expand)
    except Exception as e:
        logger.error(f"Error fetching Confluence page {page_id}: {e}")
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


@router.post("/confluence/upload-brd")
async def upload_brd_to_confluence(
    request: UploadBRDToConfluenceRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Upload BRD from S3 to Confluence
    
    Creates a new Confluence page with the BRD content from S3.
    The page will be created in the Confluence space linked to the project.
    
    Args:
        request: Contains brd_id and project_id
        
    Returns:
        Confluence page details including page ID and web URL
    """
    logger.info(f"Uploading BRD {request.brd_id} to Confluence for project {request.project_id}")
    
    # 1. Get user's Atlassian credentials
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    # 2. Get project to find Confluence space key
    project = get_project(request.project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if not project.get('confluence_space_key'):
        raise HTTPException(
            status_code=400,
            detail="No Confluence space linked to this project. Please link a Confluence space in project settings."
        )
    
    confluence_space_key = project['confluence_space_key']
    
    # 3. Fetch BRD from S3
    try:
        s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        bucket_name = os.getenv('S3_BUCKET_NAME', 'sdlc-orch-dev-us-east-1-app-data')
        
        # Try to fetch JSON structure first
        json_key = f"brds/{request.brd_id}/brd_structure.json"
        
        try:
            logger.info(f"Fetching BRD from S3: s3://{bucket_name}/{json_key}")
            response = s3_client.get_object(Bucket=bucket_name, Key=json_key)
            brd_json = json.loads(response['Body'].read().decode('utf-8'))
            logger.info(f"Successfully loaded BRD JSON with {len(brd_json.get('sections', []))} sections")
        except Exception as e:
            logger.warning(f"Could not load JSON structure: {e}. Trying text format...")
            # Fallback to text format
            txt_key = f"brds/{request.brd_id}/BRD_{request.brd_id}.txt"
            response = s3_client.get_object(Bucket=bucket_name, Key=txt_key)
            brd_text = response['Body'].read().decode('utf-8')
            # Convert text to simple structure
            brd_json = {
                "sections": [{
                    "title": "Business Requirements Document",
                    "content": [{"type": "paragraph", "text": brd_text}]
                }]
            }
    
    except Exception as e:
        logger.error(f"Error fetching BRD from S3: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch BRD from S3: {str(e)}"
        )
    
    # 4. Convert BRD to Confluence format
    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        # Convert BRD JSON to Confluence storage format
        confluence_content = confluence_service.convert_brd_to_confluence_storage(brd_json)
        
        # Generate page title
        if request.page_title:
            page_title = request.page_title
        else:
            # Use project name + timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            page_title = f"BRD - {project.get('project_name', 'Untitled')} - {timestamp}"
        
        logger.info(f"Creating Confluence page: '{page_title}' in space '{confluence_space_key}'")
        
        # 5. Create Confluence page
        page_result = confluence_service.create_page(
            space_key=confluence_space_key,
            title=page_title,
            content=confluence_content
        )
        
        logger.info(f"Successfully created Confluence page: {page_result['web_url']}")
        
        return {
            "status": "success",
            "message": "BRD uploaded to Confluence successfully",
            "confluence_page": {
                "id": page_result['id'],
                "title": page_result['title'],
                "web_url": page_result['web_url'],
                "space_key": confluence_space_key
            }
        }
    
    except Exception as e:
        logger.error(f"Error creating Confluence page: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Confluence page: {str(e)}"
        )


