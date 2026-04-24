"""
Internal Orchestration Router - MCP/API-key authenticated endpoints
Separated from the public orchestration router for cleaner structure.
"""

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from services.rag_service import rag_service
from routers.internal_utils import validate_api_key
from db_helper import get_project
import logging
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestration", tags=["orchestration-internal"])


# ============================================
# REQUEST MODELS
# ============================================

class QueryRequest(BaseModel):
    project_id: str
    query: str
    max_chunks: Optional[int] = 5
    source_filter: Optional[str] = None
    include_context: Optional[bool] = True
    return_prompt: Optional[bool] = False
    frontend_requirements: Optional[str] = None
    backend_requirements: Optional[str] = None


# ============================================
# INTERNAL ENDPOINTS (MCP)
# ============================================

@router.post("/query-internal")
async def query_internal(
    request: QueryRequest,
    x_api_key: str = Header(alias="X-API-Key")
):
    """
    Internal endpoint for MCP or other backend tools.
    Bypasses Azure AD, uses API Key validation.

    - return_prompt=True  → returns plain JSON  (consumed by MCP enhance tool)
    - return_prompt=False → returns SSE stream   (consumed by streaming clients)
    """
    validate_api_key(x_api_key)

    # MCP calls have no user identity — resolve the project owner and attribute
    # the token usage to them so we don't lose those calls.
    owner_user_id: Optional[str] = None
    try:
        project = get_project(request.project_id)
        if project:
            owner_user_id = project.get("user_id")
            logger.info(f"[ORCHESTRATION] project {request.project_id} owned by user {owner_user_id} — attributing tokens")
        else:
            logger.warning(f"[ORCHESTRATION] project {request.project_id} not found — tokens will log as user=unknown")
    except Exception as e:
        logger.warning(f"[ORCHESTRATION] project owner lookup failed (non-fatal): {e}")

    # ── Plain JSON path (MCP prompt enhancement) ──
    if request.return_prompt:
        print(f"[ORCHESTRATION] Received MCP enhancement request for project: {request.project_id}")
        print(f"[ORCHESTRATION] User Query: {request.query}")
        print("[ORCHESTRATION] Generating enhanced prompt context...")

        try:
            enhanced_prompt = await rag_service.get_enhanced_prompt(
                project_id=request.project_id,
                user_query=request.query,
                max_chunks=request.max_chunks,
                source_filter=request.source_filter,
                frontend_requirements=request.frontend_requirements or "",
                backend_requirements=request.backend_requirements or "",
                user_id=owner_user_id,
            )
        except Exception as e:
            logger.error(f"Error generating enhanced prompt: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        print(f"[ORCHESTRATION] Constructed enhanced prompt ({len(enhanced_prompt)} chars).")
        print(f"[ORCHESTRATION] Response sent.")
        return {"type": "enhanced_prompt", "content": enhanced_prompt}

    # ── SSE streaming path (RAG answer stream) ──
    async def generate_sse():
        """Generate Server-Sent Events stream"""
        try:
            async for event in rag_service.query_with_rag(
                project_id=request.project_id,
                user_query=request.query,
                max_chunks=request.max_chunks,
                source_filter=request.source_filter,
                include_context=request.include_context,
                user_id=owner_user_id,
            ):
                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error(f"Error in SSE stream: {e}")
            error_event = {
                'type': 'error',
                'message': str(e)
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
