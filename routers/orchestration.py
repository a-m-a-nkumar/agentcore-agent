"""
Orchestration Router - RAG-based question answering endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from services.rag_service import rag_service
from auth import verify_azure_token
from db_helper import create_or_update_user
from langfuse_client import get_langfuse
import logging
import json
import os

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
    return_prompt: Optional[bool] = False  # If True, returns compiled prompt instead of LLM answer


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


# ============================================
# INTERNAL ENDPOINTS (MCP)
# ============================================

@router.post("/query-internal")
async def query_internal(
    request: QueryRequest,
    x_api_key: str = Header(alias="X-API-Key")
):
    """
    Internal endpoint for MCP or other backend tools
    Bypasses Azure AD, uses API Key validation
    """
    
    # 1. Validate API Key
    internal_keys_str = os.environ.get("INTERNAL_API_KEYS", "{}")
    try:
        valid_keys = json.loads(internal_keys_str)
    except json.JSONDecodeError:
        logger.error("Failed to parse INTERNAL_API_KEYS from env")
        valid_keys = {}
        
    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid API Key")
        
    # 2. Call RAG Service
    async def generate_sse():
        """Generate Server-Sent Events stream"""
        try:
            # OPTION A: RETURN PROMPT ONLY (for MCP/IDE)
            if request.return_prompt:
                print(f"[ORCHESTRATION] Received MCP enhancement request for project: {request.project_id}")
                print(f"[ORCHESTRATION] User Query: {request.query}")
                print("[ORCHESTRATION] Generating enhanced prompt context...")
                
                # Fetch RAG context and build the prompt
                enhanced_prompt = await rag_service.get_enhanced_prompt(
                    project_id=request.project_id,
                    user_query=request.query,
                    max_chunks=request.max_chunks,
                    source_filter=request.source_filter
                )
                
                print(f"[ORCHESTRATION] Constructed enhanced prompt ({len(enhanced_prompt)} chars).")
                print(f"[ORCHESTRATION] Sending response to MCP client...")

                # Send single event with the full prompt
                yield f"data: {json.dumps({'type': 'enhanced_prompt', 'content': enhanced_prompt})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                print(f"[ORCHESTRATION] Response sent.")
                return

            # OPTION B: STANDARD RAG ANSWER STREAM
            async for event in rag_service.query_with_rag(
                project_id=request.project_id,
                user_query=request.query,
                max_chunks=request.max_chunks,
                source_filter=request.source_filter,
                include_context=request.include_context
            ):
                # Send event as SSE
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
