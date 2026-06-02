"""
Internal Orchestration Router - MCP/API-key authenticated endpoints.

Two endpoints share the same underlying RAG + prompt-enhancement logic but
expose distinct paths so the user-module activity tracker can attribute each
call to its source MCP (prompt-enhancer vs pipeline-analyzer).
"""

import os
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
from services.rag_service import rag_service
from routers.internal_utils import validate_api_key
from db_helper import get_project, track_event, get_user_harness_credentials
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestration", tags=["orchestration-internal"])


# ============================================
# REQUEST MODEL
# ============================================

class EnhanceRequest(BaseModel):
    project_id: str
    query: str
    max_chunks: Optional[int] = 5
    source_filter: Optional[str] = None
    frontend_requirements: Optional[str] = None
    backend_requirements: Optional[str] = None


class HarnessConfigRequest(BaseModel):
    project_id: Optional[str] = None


# ============================================
# SHARED HELPERS
# ============================================

def _resolve_owner(project_id: str) -> Optional[str]:
    """MCP calls have no user identity — attribute token usage to the project owner."""
    try:
        project = get_project(project_id)
        if project:
            owner = project.get("user_id")
            logger.info(
                f"[ORCHESTRATION] project {project_id} owned by user {owner} — attributing tokens"
            )
            return owner
        logger.warning(
            f"[ORCHESTRATION] project {project_id} not found — tokens will log as user=unknown"
        )
    except Exception as e:
        logger.warning(f"[ORCHESTRATION] project owner lookup failed (non-fatal): {e}")
    return None


async def _build_enhanced_prompt(
    request: EnhanceRequest,
    source_label: str,
    owner_user_id: Optional[str],
) -> dict:
    logger.info(f"[ORCHESTRATION:{source_label}] Project: {request.project_id}")
    logger.info(f"[ORCHESTRATION:{source_label}] User Query: {request.query}")
    logger.info(f"[ORCHESTRATION:{source_label}] Generating enhanced prompt context...")

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

    logger.info(
        f"[ORCHESTRATION:{source_label}] Constructed enhanced prompt "
        f"({len(enhanced_prompt)} chars). Response sent."
    )
    return {"type": "enhanced_prompt", "content": enhanced_prompt}


# ============================================
# INTERNAL ENDPOINTS (MCP)
# ============================================

@router.post("/enhance-prompt-internal")
async def enhance_prompt_internal(
    request: EnhanceRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Consumed by the prompt-enhancer MCP. Runs RAG retrieval, then uses Claude
    to generate an enhanced prompt for the user's task.

    Activity tracker logs this call as `pair-programming / prompt_enhancement`.
    """
    validate_api_key(x_api_key)

    owner_user_id = _resolve_owner(request.project_id)
    response = await _build_enhanced_prompt(
        request, source_label="prompt_enhancer", owner_user_id=owner_user_id
    )

    if owner_user_id:
        track_event(
            user_id=owner_user_id,
            module="pair-programming",
            event_type="prompt_enhancement",
            project_id=request.project_id,
            source="mcp",
            metadata={
                "query_length": len(request.query),
                "max_chunks": request.max_chunks,
                "response_chars": len(response.get("content", "")),
            },
        )

    return response


@router.post("/pipeline-rag-internal")
async def pipeline_rag_internal(
    request: EnhanceRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Consumed by the pipeline-analyzer MCP. Returns raw RAG chunks for
    organizational context (past incidents, runbooks) — no Claude call.
    The pipeline-analyzer formats these into its failure-analysis blob.

    Activity tracker logs this call as `deployment / pipeline_failure_root_cause_analysis`.
    """
    validate_api_key(x_api_key)

    owner_user_id = _resolve_owner(request.project_id)

    logger.info(f"[ORCHESTRATION:pipeline_rag] Project: {request.project_id}")
    logger.info(f"[ORCHESTRATION:pipeline_rag] User Query: {request.query}")

    try:
        results = rag_service.get_rag_context(
            project_id=request.project_id,
            user_query=request.query,
            max_chunks=request.max_chunks,
            source_filter=request.source_filter,
            user_id=owner_user_id,
        )
    except Exception as e:
        logger.error(f"Error retrieving pipeline RAG context: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    logger.info(f"[ORCHESTRATION:pipeline_rag] Returned {len(results)} chunk(s).")

    if owner_user_id:
        track_event(
            user_id=owner_user_id,
            module="deployment",
            event_type="pipeline_failure_root_cause_analysis",
            project_id=request.project_id,
            source="mcp",
            metadata={
                "query_length": len(request.query),
                "max_chunks": request.max_chunks,
                "result_count": len(results),
            },
        )

    return {"type": "rag_context", "results": results}


@router.post("/harness-config-internal")
async def harness_config_internal(
    request: HarnessConfigRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Resolve Harness credentials for a project's owner so the pipeline-analyzer
    MCP doesn't need any HARNESS_* env vars in its config.

    Auth: X-API-Key (INTERNAL_API_KEYS).
    Body: {project_id} — falls back to the key-mapped project if omitted.
    Returns the same shape harness_client.py expects:
      {api_key, account_id, org_id, project_id, base_url}
    """
    key_project_id = validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    user_id = project.get("user_id")
    if not user_id:
        raise HTTPException(status_code=500, detail="Project has no owner; cannot resolve Harness credentials.")

    creds = get_user_harness_credentials(user_id)
    if not creds or not creds.get("harness_pat"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Project owner has not linked a Harness account. "
                "Owner must visit Settings → Harness on the SDLC frontend before using the pipeline-analyzer MCP."
            ),
        )

    return {
        "api_key":    creds["harness_pat"],
        "account_id": creds.get("harness_account_id", "") or "",
        "org_id":     creds.get("harness_org_id", "default") or "default",
        "project_id": creds.get("harness_project_id", "") or "",
        "base_url":   os.getenv("HARNESS_BASE_URL", "https://app.harness.io").rstrip("/"),
    }
