"""
Jira Sync router — the "Changes to apply" forward-propagation flow.

Endpoints (all under /api/jira-sync):

  POST  /scan
        Body: {project_id}
        Resolves project_id -> workspace_key, verifies access, kicks a
        background scan. Returns {scan_run_id, status, cached}.
  GET   /pulse?project_id=
        Latest scan + pending changes for the workspace.
  GET   /changes/{change_id}
        One pending change + all proposed artifact updates.
  POST  /changes/{change_id}/decisions
        Bulk update decisions (approved / dismissed).
  POST  /changes/{change_id}/apply
        Push every approved proposal to Jira / Confluence.

All workspace-scoped data is gated by services.workspace.verify_user_has_workspace_access.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

from auth import verify_azure_token
from db_helper import (
    create_or_update_user,
    get_db_connection,
    release_db_connection,
)
from services import jira_sync_service
from services import jira_drift_service
from services.workspace import (
    get_workspace_key_for_project,
    verify_user_has_workspace_access,
)

router = APIRouter(prefix="/api/jira-sync", tags=["jira-sync"])
logger = logging.getLogger(__name__)


# ── Auth (same pattern as brd_comparison.py) ─────────────────────────────────


async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")
    try:
        return create_or_update_user(user_id, email, name)
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ── Workspace resolution helper ──────────────────────────────────────────────


def _resolve_workspace_or_403(project_id: str, user_id: str) -> str:
    ws = get_workspace_key_for_project(project_id)
    if not ws:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    if not verify_user_has_workspace_access(user_id, ws):
        raise HTTPException(status_code=403, detail="No access to this workspace")
    return ws


def _resolve_workspace_for_change_or_403(change_id: str, user_id: str) -> str:
    """Look up the workspace from a pending_changes row, then enforce access."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT workspace_key FROM pending_changes WHERE id = %s",
                (change_id,),
            )
            row = cursor.fetchone()
    finally:
        if conn:
            release_db_connection(conn)
    if not row:
        raise HTTPException(status_code=404, detail="Change not found")
    ws = row["workspace_key"]
    if not verify_user_has_workspace_access(user_id, ws):
        raise HTTPException(status_code=403, detail="No access to this workspace")
    return ws


def _resolve_workspace_for_drift_or_403(drift_id: str, user_id: str) -> str:
    """Look up the workspace from a jira_drift_items row, then enforce access."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT workspace_key FROM jira_drift_items WHERE id = %s",
                (drift_id,),
            )
            row = cursor.fetchone()
    finally:
        if conn:
            release_db_connection(conn)
    if not row:
        raise HTTPException(status_code=404, detail="Drift item not found")
    ws = row["workspace_key"]
    if not verify_user_has_workspace_access(user_id, ws):
        raise HTTPException(status_code=403, detail="No access to this workspace")
    return ws


# ── Models ───────────────────────────────────────────────────────────────────


class ScanRequest(BaseModel):
    project_id: str
    force: bool = False  # when True, bypass the dedup cache and always run a fresh scan


class ScanResponse(BaseModel):
    scan_run_id: str
    status: str
    cached: bool


class DecisionEntry(BaseModel):
    update_id: str
    decision: str = Field(..., pattern="^(approved|dismissed|edited|pending)$")


class DecisionsRequest(BaseModel):
    decisions: List[DecisionEntry]


class DecisionsResponse(BaseModel):
    updated: int


class ApplyResponse(BaseModel):
    applied: int
    failed: List[dict]


class DriftResolveRequest(BaseModel):
    resolution: str = Field(
        ..., pattern="^(brd-outdated|story-wrong|intentional|update-story)$"
    )
    note: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/scan", response_model=ScanResponse)
def trigger_scan(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """
    Resolve project_id -> workspace_key, verify access, then either return a
    cached recent scan or kick a background BackgroundTask that runs the full
    scan pipeline.
    """
    workspace_key = _resolve_workspace_or_403(request.project_id, current_user["id"])

    # In-flight check: even with force=true, never start a second concurrent
    # scan for the same workspace. Concurrent scans race-supersede each
    # other's output (each flips the other's still-'pending' rows at the end
    # of its pipeline), wiping the workspace's results entirely. If a scan
    # is already running, return its id so the client polls the running one.
    running_id = jira_sync_service.find_running_scan(workspace_key)
    if running_id:
        return ScanResponse(scan_run_id=running_id, status="running", cached=False)

    # Cheap dedup check up-front so we can answer 'cached' without spinning up
    # the background task. Skipped when the client explicitly asks for a
    # forced fresh scan (useful for debugging / when the user wants to watch
    # the agents actually run).
    if not request.force:
        cached_id = jira_sync_service._find_recent_complete_scan(workspace_key)  # type: ignore[attr-defined]
        if cached_id:
            return ScanResponse(scan_run_id=cached_id, status="complete", cached=True)

    confluence = jira_sync_service.build_confluence_for_user(current_user["id"])
    if confluence is None:
        raise HTTPException(status_code=400, detail="Atlassian account not linked.")
    # Jira client for the reverse drift pass (optional — drift is skipped if absent).
    jira = jira_sync_service.build_jira_for_user(current_user["id"])

    # Pre-create the run row so the frontend can poll immediately with the id.
    # Returns None if another scan won the race (atomic guard) — reuse it.
    run_id = jira_sync_service._create_run_row(  # type: ignore[attr-defined]
        workspace_key=workspace_key,
        user_id=current_user["id"],
        project_id=request.project_id,
    )
    if run_id is None:
        existing = jira_sync_service.find_running_scan(workspace_key)
        return ScanResponse(scan_run_id=existing or "", status="running", cached=False)

    def _run():
        try:
            jira_sync_service._run_scan_pipeline(  # type: ignore[attr-defined]
                run_id=run_id,
                workspace_key=workspace_key,
                user_id=current_user["id"],
                confluence=confluence,
                jira=jira,
            )
            jira_sync_service._mark_run(run_id, status="complete")  # type: ignore[attr-defined]
        except Exception as e:
            logger.exception(f"[jira-sync] background scan failed run_id={run_id}")
            jira_sync_service._mark_run(  # type: ignore[attr-defined]
                run_id, status="failed", message=str(e)[:500]
            )

    background_tasks.add_task(_run)
    return ScanResponse(scan_run_id=run_id, status="running", cached=False)


@router.get("/pulse")
def get_pulse(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    workspace_key = _resolve_workspace_or_403(project_id, current_user["id"])
    return jira_sync_service.get_pulse(workspace_key)


@router.get("/changes/{change_id}")
def get_change(
    change_id: str,
    current_user: dict = Depends(get_current_user),
):
    _resolve_workspace_for_change_or_403(change_id, current_user["id"])
    detail = jira_sync_service.get_change_detail(change_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Change not found")
    return detail


@router.post("/changes/{change_id}/decisions", response_model=DecisionsResponse)
def post_decisions(
    change_id: str,
    request: DecisionsRequest,
    current_user: dict = Depends(get_current_user),
):
    _resolve_workspace_for_change_or_403(change_id, current_user["id"])
    updated = jira_sync_service.set_decisions(
        pending_change_id=change_id,
        decisions=[d.model_dump() for d in request.decisions],
        decided_by_user_id=current_user["id"],
    )
    return DecisionsResponse(updated=updated)


@router.post("/changes/{change_id}/apply", response_model=ApplyResponse)
def post_apply(
    change_id: str,
    current_user: dict = Depends(get_current_user),
):
    _resolve_workspace_for_change_or_403(change_id, current_user["id"])
    confluence = jira_sync_service.build_confluence_for_user(current_user["id"])
    jira = jira_sync_service.build_jira_for_user(current_user["id"])
    if confluence is None or jira is None:
        raise HTTPException(status_code=400, detail="Atlassian account not linked.")
    result = jira_sync_service.apply_change(
        pending_change_id=change_id,
        applied_by_user_id=current_user["id"],
        confluence=confluence,
        jira=jira,
    )
    return ApplyResponse(applied=result["applied"], failed=result["failed"])


# ── Drift to resolve (reverse direction) ─────────────────────────────────────


@router.get("/drift/{drift_id}")
def get_drift(
    drift_id: str,
    current_user: dict = Depends(get_current_user),
):
    _resolve_workspace_for_drift_or_403(drift_id, current_user["id"])
    detail = jira_drift_service.get_drift_detail(drift_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Drift item not found")
    return detail


@router.post("/drift/{drift_id}/resolve")
def post_resolve_drift(
    drift_id: str,
    request: DriftResolveRequest,
    current_user: dict = Depends(get_current_user),
):
    _resolve_workspace_for_drift_or_403(drift_id, current_user["id"])
    # The story-wrong path needs a Jira client to push the revert; the other
    # two don't, so a missing Atlassian link only fails that branch (in-service).
    jira = jira_sync_service.build_jira_for_user(current_user["id"])
    result = jira_drift_service.resolve_drift(
        drift_id=drift_id,
        resolution=request.resolution,
        note=request.note,
        user_id=current_user["id"],
        jira=jira,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Resolve failed"))
    return result
