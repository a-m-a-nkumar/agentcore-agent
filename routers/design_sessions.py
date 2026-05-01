"""
FastAPI router for the multi-session Design Assistant.

A `design_session` is the top-level container that spans the Diagram phase
(mxGraph XML + rendered SVG saved to S3) and the SAD phase
(sad_structure.json, facts.json, audit results in S3 + chat in AgentCore
Memory). One project can have many sessions; each resumes from its DB row.

Endpoints (all under /api/design/sessions):
  POST    /                  → create
  GET     /?project_id=...   → list (sidebar)
  GET     /{session_id}      → fetch single
  PATCH   /{session_id}      → rename / change stage / update artefact keys
  DELETE  /{session_id}      → soft-delete
  GET     /{session_id}/history → AgentCore Memory chat history for this session
"""

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db_helper import (
    create_design_session,
    delete_design_session,
    get_design_session,
    get_diagram_slots,
    get_project,
    list_design_sessions,
    set_session_authoring_tool,
    update_design_session,
    update_diagram_slot,
)

# Reuse the projects-router auth dependency (returns DB user row with key "id")
from .projects import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/design/sessions", tags=["design-sessions"])


# ============================================
# Request / Response models
# ============================================

class DesignSessionCreate(BaseModel):
    project_id: str
    name: Optional[str] = None
    stage: Optional[str] = "NEW"


class DesignSessionUpdate(BaseModel):
    name: Optional[str] = None
    stage: Optional[str] = None
    diagram_s3_key: Optional[str] = None
    diagram_svg_s3_key: Optional[str] = None
    sad_id: Optional[str] = None
    confluence_page_id: Optional[str] = None


class DesignSessionResponse(BaseModel):
    id: str
    project_id: str
    user_id: str
    name: str
    stage: str
    diagram_s3_key: Optional[str] = None
    diagram_svg_s3_key: Optional[str] = None
    sad_id: Optional[str] = None
    confluence_page_id: Optional[str] = None
    is_deleted: bool
    created_at: int
    last_activity_ts: int


class HistoryMessage(BaseModel):
    role: str
    content: str
    ts: Optional[int] = None


# ============================================
# Helpers
# ============================================

_AGENTCORE_REGION = os.getenv("AWS_REGION", "us-east-1")
_DESIGN_ACTOR_ID = os.getenv("DESIGN_AGENTCORE_ACTOR_ID", "design-session")
# Memory ID is environment-specific; reuse the same memory the analyst path uses.
# env_vdi exposes DEFAULT_AGENTCORE_MEMORY_ID; tolerate missing import in local mode.
try:
    from environment import DEFAULT_AGENTCORE_MEMORY_ID as _DEFAULT_MEMORY_ID
except Exception:  # pragma: no cover
    _DEFAULT_MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", "")

_AGENTCORE_MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", _DEFAULT_MEMORY_ID)


def _ensure_owns_project(project_id: str, user_id: str):
    """Raise 404 if the project doesn't exist or doesn't belong to this user."""
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.get("user_id") and project["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You don't have access to this project")


def _ensure_owns_session(session_id: str, user_id: str):
    """Raise 404 if session missing; 403 if not owned by user. Returns the row."""
    session = get_design_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You don't have access to this session")
    return session


def _next_session_name(project_id: str, user_id: str) -> str:
    """Default name for a new session: 'Session N' where N is one past the count."""
    existing = list_design_sessions(project_id, user_id)
    return f"Session {len(existing) + 1}"


# ============================================
# Endpoints
# ============================================

@router.post("/", response_model=DesignSessionResponse)
def create(
    payload: DesignSessionCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new design session under a project."""
    user_id = current_user["id"]
    _ensure_owns_project(payload.project_id, user_id)

    session_id = str(uuid.uuid4())
    name = payload.name or _next_session_name(payload.project_id, user_id)
    stage = payload.stage or "NEW"

    return create_design_session(
        session_id=session_id,
        project_id=payload.project_id,
        user_id=user_id,
        name=name,
        stage=stage,
    )


@router.get("/", response_model=List[DesignSessionResponse])
def list_for_project(
    project_id: str = Query(..., description="Project ID to filter sessions"),
    current_user: dict = Depends(get_current_user),
):
    """List all (non-deleted) sessions for a project, newest first."""
    user_id = current_user["id"]
    _ensure_owns_project(project_id, user_id)
    return list_design_sessions(project_id, user_id)


@router.get("/{session_id}", response_model=DesignSessionResponse)
def get(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch a single design_session by id."""
    return _ensure_owns_session(session_id, current_user["id"])


@router.patch("/{session_id}", response_model=DesignSessionResponse)
def patch(
    session_id: str,
    payload: DesignSessionUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update name / stage / artefact keys on a session."""
    _ensure_owns_session(session_id, current_user["id"])
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        return update_design_session(session_id=session_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{session_id}")
def delete(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete a session (artefacts in S3 are kept; row marked is_deleted=true)."""
    _ensure_owns_session(session_id, current_user["id"])
    delete_design_session(session_id)
    return {"ok": True, "session_id": session_id}


# ============================================
# Diagram-slot endpoints (SAD-redesign)
# ============================================
#
# Each session has three independent slots: logical / infrastructure /
# security. Slot persistence happens implicitly via /api/design/save-diagram
# (which sets a slot to "done"), but the redesign hub also needs to:
#   • read the current slots on session load (GET /diagram-slots)
#   • mark a slot as skipped / un-skipped (PATCH /diagram-slots/{type})
#   • record the user's preferred authoring tool (PUT /tool)
# These endpoints are thin wrappers around db_helper functions — all the
# slot-state bookkeeping lives there.

_DIAGRAM_TYPES = ("logical", "infrastructure", "security")
_USER_PATCH_FIELDS = {"status", "tool"}


class DiagramSlotsResponse(BaseModel):
    tool: Optional[str] = None  # "drawio" | "lucid" | None
    slots: Dict[str, Any]       # {logical, infrastructure, security} → slot dicts


class DiagramSlotPatch(BaseModel):
    status: Optional[str] = None  # one of pending|in_progress|done|skipped|skipped_saved|failed
    tool: Optional[str] = None    # "drawio" | "lucid"


class ToolUpdate(BaseModel):
    tool: Optional[str] = None    # "drawio" | "lucid" | None to clear


@router.get("/{session_id}/diagram-slots", response_model=DiagramSlotsResponse)
def get_slots(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the current diagram_slots + authoring tool for a session.
    The redesign hub calls this once on mount to populate per-type status."""
    _ensure_owns_session(session_id, current_user["id"])
    try:
        return get_diagram_slots(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{session_id}/diagram-slots/{diagram_type}", response_model=Dict[str, Any])
def patch_slot(
    session_id: str,
    diagram_type: str,
    payload: DiagramSlotPatch,
    current_user: dict = Depends(get_current_user),
):
    """Patch one slot. Used by the hub for skip / un-skip / mark-pending.
    Saves of actual diagram artifacts go through /api/design/save-diagram —
    this endpoint only mutates the JSONB metadata."""
    _ensure_owns_session(session_id, current_user["id"])
    if diagram_type not in _DIAGRAM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"diagram_type must be one of: {', '.join(_DIAGRAM_TYPES)}",
        )
    fields = payload.model_dump(exclude_unset=True)
    fields = {k: v for k, v in fields.items() if k in _USER_PATCH_FIELDS}
    if not fields:
        raise HTTPException(status_code=400, detail="No updatable fields supplied")
    # Defensive: don't let the user write 'done' through this endpoint —
    # done means an artifact exists, which only /save-diagram can produce.
    if fields.get("status") == "done":
        raise HTTPException(
            status_code=400,
            detail="To mark a slot Done, save the diagram via /api/design/save-diagram",
        )
    try:
        return update_diagram_slot(session_id, diagram_type, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{session_id}/tool", response_model=DiagramSlotsResponse)
def put_tool(
    session_id: str,
    payload: ToolUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Set (or clear) the session's authoring tool preference."""
    _ensure_owns_session(session_id, current_user["id"])
    try:
        set_session_authoring_tool(session_id, payload.tool)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_diagram_slots(session_id)


@router.get("/{session_id}/history", response_model=List[HistoryMessage])
def history(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Pull conversation history from AgentCore Memory for this design session."""
    _ensure_owns_session(session_id, current_user["id"])

    if not _AGENTCORE_MEMORY_ID:
        logger.warning("[DESIGN_SESSIONS] AGENTCORE_MEMORY_ID not configured; returning empty history")
        return []

    try:
        client = boto3.client("bedrock-agentcore", region_name=_AGENTCORE_REGION)
        r = client.list_events(
            memoryId=_AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=_DESIGN_ACTOR_ID,
            includePayloads=True,
            maxResults=99,
        )
        raw_events: List[Dict[str, Any]] = r.get("events", []) or []
    except Exception as e:
        logger.error(f"[DESIGN_SESSIONS] Failed to read history for {session_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to read chat history from AgentCore Memory")

    messages: List[HistoryMessage] = []
    for ev in raw_events:
        ts = None
        if ev.get("eventTimestamp"):
            try:
                ts = int(ev["eventTimestamp"].timestamp() * 1000)
            except Exception:
                ts = None
        for item in ev.get("payload", []) or []:
            conv = item.get("conversational")
            if not conv:
                continue
            text = (conv.get("content") or {}).get("text") or ""
            role = (conv.get("role") or "USER").lower()
            if not text:
                continue
            messages.append(HistoryMessage(role=role, content=text, ts=ts))

    # Order by timestamp (None timestamps fall back to original insertion).
    messages.sort(key=lambda m: m.ts if m.ts is not None else 0)
    return messages
