"""
Orchestration Router - RAG-based question answering endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from services.rag_service import rag_service
from auth import verify_azure_token
from db_helper import create_or_update_user
from langfuse_client import get_langfuse
import logging
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestration", tags=["orchestration"])


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

class QueryRequest(BaseModel):
    project_id: str
    query: str
    max_chunks: Optional[int] = 5
    source_filter: Optional[str] = None  # 'confluence' or 'jira'
    include_context: Optional[bool] = True  # Include chunk ±1


# ============================================
# API ENDPOINTS
# ============================================

@router.post("/query")
async def query_documentation(
    request: QueryRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Query project documentation using RAG
    
    Returns streaming SSE response with:
    - LLM-generated answer chunks
    - Source citations
    - Completion signal
    """
    langfuse = get_langfuse()
    trace_metadata = {
        "project_id": request.project_id,
        "max_chunks": request.max_chunks,
        "source_filter": request.source_filter or "",
        "user_query_preview": (request.query[:200] + "..." if len(request.query) > 200 else request.query),
    }
    user_id = str(current_user.get("id") or current_user.get("user_id") or current_user.get("email") or "")

    try:
        async def generate_sse():
            """Generate Server-Sent Events stream"""
            root_span = None
            try:
                trace_meta = {**trace_metadata}
                if user_id:
                    trace_meta["user_id"] = user_id
                with langfuse.start_as_current_observation(
                    as_type="span",
                    name="rag.query",
                    input={"query_preview": trace_metadata["user_query_preview"], "project_id": request.project_id},
                    metadata=trace_meta,
                ) as root_span:
                    async for event in rag_service.query_with_rag(
                        project_id=request.project_id,
                        user_query=request.query,
                        max_chunks=request.max_chunks,
                        source_filter=request.source_filter,
                        include_context=request.include_context
                    ):
                        yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                logger.error(f"Error in SSE stream: {e}")
                if root_span is not None:
                    try:
                        root_span.update(metadata={"error": str(e)})
                    except Exception:
                        pass
                error_event = {
                    'type': 'error',
                    'message': str(e)
                }
                yield f"data: {json.dumps(error_event)}\n\n"
            finally:
                try:
                    langfuse.flush()
                except Exception:
                    pass

        return StreamingResponse(
            generate_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # Disable nginx buffering
            }
        )
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "orchestration"}
