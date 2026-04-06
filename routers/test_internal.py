"""
Internal Test Router - MCP/API-key authenticated endpoints for test workflow.
Separated from the public test_generation router for cleaner structure.

Endpoints:
  POST /api/test/list-pages-internal
  POST /api/test/parse-scenarios-internal
  POST /api/test/submit-gherkin-internal
  GET  /api/test/listen/{project_id}   (Azure AD — consumed by frontend)
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from auth import verify_azure_token
from db_helper import get_user_atlassian_credentials, create_or_update_user, get_project
from services.confluence_service import ConfluenceService
from routers.internal_utils import validate_api_key, test_sessions, project_events
from routers.test_generation import extract_scenarios_with_bedrock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test-internal"])


# ============================================
# AUTHENTICATION DEPENDENCY (for SSE listener)
# ============================================

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
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


# ============================================
# REQUEST MODELS
# ============================================

class ListPagesInternalRequest(BaseModel):
    project_id: Optional[str] = None
    filter: Optional[str] = "test scenario"


class ParseScenariosInternalRequest(BaseModel):
    confluence_page_id: str
    project_id: Optional[str] = None


class SubmitGherkinInternalRequest(BaseModel):
    project_id: Optional[str] = None
    gherkin: str
    session_id: Optional[str] = None


# ============================================
# INTERNAL ENDPOINTS (API Key Auth)
# ============================================

@router.post("/list-pages-internal")
async def list_pages_internal(
    request: ListPagesInternalRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    List Confluence pages for a project. Used by MCP tool.
    Resolves Atlassian credentials from the project owner.
    """
    key_project_id = validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    owner_id = project.get("user_id")
    if not owner_id:
        raise HTTPException(status_code=400, detail="Project has no owner")

    credentials = get_user_atlassian_credentials(owner_id)
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(status_code=400, detail="Project owner has no linked Atlassian account")

    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        space_key = project.get("confluence_space_key", "SO")
        all_pages = confluence_service.get_content_pages(space_key=space_key, limit=500)

        filter_term = (request.filter or "").lower()
        if filter_term:
            pages = [
                {"id": p["id"], "title": p["title"]}
                for p in all_pages
                if filter_term in p.get("title", "").lower()
            ]
        else:
            pages = [{"id": p["id"], "title": p["title"]} for p in all_pages]

        return {"pages": pages, "total": len(pages)}
    except Exception as e:
        logger.error(f"Error listing pages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-scenarios-internal")
async def parse_scenarios_internal(
    request: ParseScenariosInternalRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Parse test scenarios from a Confluence page. Used by MCP tool.
    Returns session_id + prompt for the AI IDE to generate .feature files.
    """
    key_project_id = validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    owner_id = project.get("user_id")
    if not owner_id:
        raise HTTPException(status_code=400, detail="Project has no owner")

    credentials = get_user_atlassian_credentials(owner_id)
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(status_code=400, detail="Project owner has no linked Atlassian account")

    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        page_data = confluence_service.get_page_content(request.confluence_page_id)
        logger.info(f"[MCP] Fetched scenario page: {page_data['title']}")
    except Exception as e:
        logger.error(f"[MCP] Error fetching Confluence page: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch Confluence page: {str(e)}")

    try:
        result = extract_scenarios_with_bedrock(
            page_data["content"],
            page_data["title"],
        )

        session_id = str(uuid.uuid4())
        test_sessions[session_id] = {
            "project_id": project_id,
            "page_title": page_data["title"],
            "scenarios": result.get("scenarios", []),
            "prompt": result.get("prompt", ""),
            "gherkin": None,
        }

        return {
            "session_id": session_id,
            "page_title": page_data["title"],
            "scenarios": result.get("scenarios", []),
            "prompt": result.get("prompt", ""),
        }
    except Exception as e:
        logger.error(f"[MCP] Error extracting scenarios: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse scenarios: {str(e)}")


@router.post("/submit-gherkin-internal")
async def submit_gherkin_internal(
    request: SubmitGherkinInternalRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Submit generated Gherkin from AI IDE back to the platform.
    Signals the SSE listener so the frontend auto-populates.
    """
    key_project_id = validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id

    session_id = request.session_id or str(uuid.uuid4())

    if session_id in test_sessions:
        test_sessions[session_id]["gherkin"] = request.gherkin
    else:
        test_sessions[session_id] = {
            "project_id": project_id,
            "gherkin": request.gherkin,
        }

    if project_id not in project_events:
        project_events[project_id] = asyncio.Event()
    project_events[project_id].set()

    logger.info(f"[MCP] Gherkin submitted for project {project_id}, session {session_id}")
    return {"session_id": session_id, "status": "received"}


# ============================================
# SSE LISTENER (Azure AD Auth — called by frontend)
# ============================================

@router.get("/listen/{project_id}")
async def listen_for_test_cases(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    SSE endpoint for the frontend to listen for test cases from MCP.
    Uses Azure AD auth (this is called by the frontend, not MCP).
    """
    async def event_generator():
        if project_id not in project_events:
            project_events[project_id] = asyncio.Event()

        event = project_events[project_id]

        try:
            while True:
                try:
                    await asyncio.wait_for(event.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                    continue

                event.clear()
                for sid, session in test_sessions.items():
                    if session.get("project_id") == project_id and session.get("gherkin"):
                        yield f"data: {json.dumps({'type': 'gherkin_received', 'gherkin': session['gherkin'], 'session_id': sid})}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        return
        except asyncio.CancelledError:
            logger.info(f"[SSE] Client disconnected from listen/{project_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
