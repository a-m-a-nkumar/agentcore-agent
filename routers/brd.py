"""
FastAPI router for the unified BRD agent (features/aman).

All endpoints below are scoped by `session_id` (from `analyst_sessions`).
Heavy work (intent routing, generation, audit, edit) is delegated to the
`sdlc-dev-brd-orchestrator` Lambda via boto3. Light reads (sections,
history, facts) stay in this router and read directly from S3 or
AgentCore Memory.

Endpoints (all under /api/brd) — scaffolded across Phase 3 commits:
  POST    /warmup                       → fire-and-forget ping to warm
                                          orchestrator + worker Lambdas
                                          (commit 1)
  POST    /sessions                     → create session with
                                          use_long_term_context flag
                                          (commit 2)
  GET     /projects/{pid}/context-preview
                                        → top-K facts that "Continue"
                                          mode would load (commit 2)
  GET     /sessions/{sid}/loaded-facts  → facts actually loaded for a
                                          session (commit 2)
  POST    /turn                         → unified chat box (commit 3)
  POST    /turn-stream                  → SSE for GATHER / ASK_QUESTION
                                          (commit 7)
  POST    /save-section                 → direct (no LLM) save (commit 4)
  POST    /revert-section               → pop previous_versions entry
                                          (commit 4)
  POST    /generate-from-docs           → template + transcript path
                                          (commit 5)
  POST    /generate-from-history        → chat-history path (commit 5)
  POST    /cancel-generation            → ack + record cancelled brd_id
                                          (commit 5)
  POST    /audit                        → per-section parallel audit
                                          (commit 5)
  POST    /ingest-doc                   → single or multi-file ingest
                                          (commit 5)
  GET     /{sid}/history                → AgentCore memory dump (commit 6)
  GET     /{sid}/sections               → brd_structure first paint
                                          (commit 6)
  GET     /{sid}/section/{n}            → single-section refresh
                                          (commit 6)

Mirror of routers/sad.py:1-19 — anyone familiar with the SAD router can
read this one without a tour. Differences:
  • Uses analyst_sessions instead of design_sessions (BRD session table).
  • Adds /warmup, /sessions, /context-preview, /loaded-facts that don't
    exist in SAD (per-session context-mode UX, plan Resolved Q#5).
  • Uses get_brd_session (returns stage + use_long_term_context) instead
    of get_design_session.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import boto3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db_helper import (
    create_session,
    get_brd_session,
    get_project,
    get_project_sessions,
)

# Reuse the projects-router auth dependency (DB user row keyed by "id").
from .projects import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brd", tags=["brd"])


# ============================================
# Module config
# ============================================

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Lambda function names — env-tunable so per-environment deploys pick up
# the right artifact without code changes. Defaults match deploy_lambdas
# naming convention.
BRD_ORCHESTRATOR_LAMBDA  = os.getenv("BRD_ORCHESTRATOR_LAMBDA",  "sdlc-dev-brd-orchestrator")
BRD_GENERATOR_LAMBDA     = os.getenv("BRD_GENERATOR_LAMBDA",     "sdlc-dev-brd-generator")
BRD_FROM_HISTORY_LAMBDA  = os.getenv("BRD_FROM_HISTORY_LAMBDA",  "sdlc-dev-brd-from-history")


# ============================================
# AWS clients (lazy — one per FastAPI worker process)
# ============================================

_lambda_client = None


def _lambda():
    """Lazy boto3 lambda client. 300s read_timeout matches SAD; generation
    requests may legitimately take 60+ seconds. retries=1 because Lambda
    has its own at-least-once delivery guarantee for sync invokes and a
    retry storm makes generation cost balloon."""
    global _lambda_client
    if _lambda_client is None:
        from botocore.config import Config as BotoConfig
        _lambda_client = boto3.client(
            "lambda",
            region_name=AWS_REGION,
            config=BotoConfig(
                read_timeout=300, connect_timeout=20,
                retries={"max_attempts": 1},
            ),
        )
    return _lambda_client


# ============================================
# Helpers shared across endpoints
# ============================================

def _ensure_session_owned(session_id: str, user_id: str) -> Dict[str, Any]:
    """Guard every BRD endpoint that takes session_id. Lambda re-verifies
    on its side (defense-in-depth #1) but this first check gives the
    user a clean 403/404 before we burn a Lambda invocation.
    """
    s = get_brd_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You don't have access to this session")
    return s


def _invoke_brd_lambda(
    payload: Dict[str, Any],
    *,
    invocation_type: str = "RequestResponse",
    function_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync-invoke the orchestrator (or a named worker) Lambda. Unwraps
    the {statusCode, body} envelope that lambda_handler returns so
    callers see the handler's actual return value.

    For async (fire-and-forget) invokes, pass invocation_type="Event"
    — return value is empty (Lambda returns 202 with no body).
    """
    fn = function_name or BRD_ORCHESTRATOR_LAMBDA
    resp = _lambda().invoke(
        FunctionName=fn,
        InvocationType=invocation_type,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    if invocation_type == "Event":
        # Fire-and-forget — body is empty by definition.
        return {"accepted": True, "status_code": resp.get("StatusCode")}

    body_bytes = resp["Payload"].read()
    if "FunctionError" in resp:
        raise HTTPException(
            status_code=502,
            detail=f"BRD Lambda error: {body_bytes.decode('utf-8', errors='replace')[:500]}",
        )
    try:
        outer = json.loads(body_bytes)
        if isinstance(outer, dict) and "body" in outer and isinstance(outer["body"], str):
            return json.loads(outer["body"])
        return outer
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"BRD Lambda response parse error: {e}")


# ============================================
# /warmup — frontend calls this on chat-panel mount so the user's first
# real submit lands on already-warm containers. Fire-and-forget against
# orchestrator + both worker Lambdas. Returns 202 immediately — the
# user-blocking path never waits on the warmup.
# ============================================

class WarmupResponse(BaseModel):
    accepted: bool
    targets: List[str]


@router.post("/warmup", response_model=WarmupResponse)
def warmup(current_user: dict = Depends(get_current_user)):
    """Pre-warm orchestrator + worker Lambdas so the user's first real
    submit doesn't pay cold-start cost. Idempotent: safe to call on
    every chat-panel mount.

    Fires three async {"action": "ping"} invocations in parallel —
    the orchestrator and both workers warm up independently. Per-
    Lambda invoke failures are LOGGED but don't fail the response —
    the user still benefits from the warm Lambdas that did succeed,
    and warmup is enrichment, not a hard guarantee.
    """
    targets = [
        BRD_ORCHESTRATOR_LAMBDA,
        BRD_GENERATOR_LAMBDA,
        BRD_FROM_HISTORY_LAMBDA,
    ]
    ping_payload = {"action": "ping", "request_id": uuid.uuid4().hex[:8]}
    for fn in targets:
        try:
            _lambda().invoke(
                FunctionName=fn,
                InvocationType="Event",
                Payload=json.dumps(ping_payload).encode("utf-8"),
            )
        except Exception as e:
            logger.warning(f"[BRD warmup] async invoke of {fn} failed (non-fatal): {e}")
    return WarmupResponse(accepted=True, targets=targets)


# ============================================
# Session lifecycle — POST /sessions + the two GETs that power the
# session-creation modal's "Continue with project context" preview and
# the persistent header badge's "what facts loaded" side panel.
# Plan ref: hazy-gliding-hammock.md "Per-session context mode" section.
# ============================================

# How many facts to surface in the modal preview. Capped at 5 so the
# modal stays scannable; the loaded-facts endpoint returns the full set.
BRD_CONTEXT_PREVIEW_TOP_K = int(os.getenv("BRD_CONTEXT_PREVIEW_TOP_K", "5"))


def _facts_namespace_user_project(user_id: str, project_id: str) -> str:
    """Compose the per-(user, project) long-term namespace. Mirrors
    services.brd_orchestrator_utils.facts_namespace -- repeated here so
    the router has no import-time dependency on the orchestrator's
    Lambda module (which has its own boto3 init cost on load)."""
    template = os.getenv("BRD_FACTS_NAMESPACE_TEMPLATE",
                         "user-{user_id}:project-{project_id}")
    return template.format(user_id=user_id, project_id=project_id)


def _load_long_term_facts_for_router(
    user_id: str, project_id: str, query: str = "", top_k: int = 10,
) -> List[str]:
    """Best-effort fact retrieval used by /context-preview and
    /loaded-facts. Wraps services.brd_orchestrator_utils.get_long_term_facts
    so the router doesn't have to know the AgentCore Memory SDK shape.
    Failures return [] so the UI degrades gracefully -- the
    session-creation modal still works (just shows "no facts yet")
    when the memory store is misconfigured or down.
    """
    try:
        from services.brd_orchestrator_utils import get_long_term_facts
        return get_long_term_facts(
            user_id=user_id,
            project_id=project_id,
            query=query,
            top_k=top_k,
        )
    except Exception as e:
        logger.warning(f"[BRD facts] load failed for user={user_id} project={project_id}: {e}")
        return []


# --- Request / response models ---------------------------------------------

class BRDSessionCreate(BaseModel):
    project_id: str
    title: Optional[str] = "New BRD session"
    # Per-session toggle: True = retrieve long-term project context;
    # False = start fresh (skip retrieval). Writes still feed long-term
    # memory regardless. Plan Resolved Q#5.
    use_long_term_context: bool = True
    # Frontend MAY supply its own session_id to support optimistic UI;
    # otherwise we mint one. Must be >= 33 chars when supplied (AgentCore
    # Memory requirement).
    session_id: Optional[str] = Field(default=None, min_length=33, max_length=128)


class BRDSessionStarted(BaseModel):
    """Mirrors the `session_started` card type in the plan's catalogue
    so the frontend can render this response with the same code path
    the chat surface uses for in-conversation card events."""
    session_id: str
    stage: str
    use_long_term_context: bool
    loaded_facts_count: int


class BRDContextPreview(BaseModel):
    """Mirrors `session_context_preview` card. Drives the modal's
    "Continue" toggle disabled-state and the top-5 collapsible preview."""
    available_facts_count: int
    prior_sessions_count: int
    top_facts: List[Dict[str, Any]]
    has_any_context: bool


class BRDLoadedFacts(BaseModel):
    """Body shape for GET /sessions/{sid}/loaded-facts. Used by the
    persistent header badge's side panel ("🧠 Using context from M
    prior sessions (N facts) -> click for details").
    """
    facts: List[str]
    count: int
    use_long_term_context: bool


# --- POST /sessions --------------------------------------------------------

@router.post("/sessions", response_model=BRDSessionStarted, status_code=201)
def create_brd_session(
    body: BRDSessionCreate,
    current_user: dict = Depends(get_current_user),
) -> BRDSessionStarted:
    """Create a new BRD session with the per-session context-mode
    toggle. Returns the session_started shape the frontend renders as
    the persistent header badge.

    Verifies the user owns the project (defense in depth -- the project
    auth check is the only reason we couldn't just take a project_id
    and trust it).
    """
    user_id = current_user["id"]

    project = get_project(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project["user_id"] != user_id:
        raise HTTPException(status_code=403,
                            detail="Not authorized to create session in this project")

    # AgentCore Memory requires sessionId >= 33 chars; UUID4 hex is 32,
    # so prefix with "brd-" to land at 36.
    session_id = body.session_id or f"brd-{uuid.uuid4().hex}"

    try:
        session = create_session(
            session_id=session_id,
            project_id=body.project_id,
            user_id=user_id,
            title=body.title or "New BRD session",
            stage="NEW",
            use_long_term_context=body.use_long_term_context,
        )
    except Exception as e:
        logger.error(f"[BRD create_session] failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")

    # Count what WAS loaded for this session so the frontend can render
    # the badge text without a second call. Only meaningful when the
    # user opted in -- "Fresh" mode always shows 0.
    loaded = []
    if body.use_long_term_context:
        loaded = _load_long_term_facts_for_router(
            user_id=user_id, project_id=body.project_id, query="",
            top_k=int(os.getenv("BRD_FACTS_TOP_K", "10")),
        )

    return BRDSessionStarted(
        session_id=session["id"] if isinstance(session.get("id"), str) else str(session["id"]),
        stage=session.get("stage", "NEW"),
        use_long_term_context=bool(session.get("use_long_term_context", True)),
        loaded_facts_count=len(loaded),
    )


# --- GET /projects/{project_id}/context-preview ----------------------------

@router.get("/projects/{project_id}/context-preview", response_model=BRDContextPreview)
def get_context_preview(
    project_id: str,
    current_user: dict = Depends(get_current_user),
) -> BRDContextPreview:
    """Powers the session-creation modal preview. Returns enough
    information for the modal to:

      1. Show "{N} facts available from {M} prior sessions".
      2. Display a collapsible top-5 facts list.
      3. Auto-disable the "Continue" radio when has_any_context=False.

    Per-project, per-user namespace. The auth dependency restricts to
    sessions the current user owns.
    """
    user_id = current_user["id"]

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project["user_id"] != user_id:
        raise HTTPException(status_code=403,
                            detail="Not authorized to view this project")

    # Prior sessions count = how many analyst_sessions rows this user
    # already has on the project. Includes the current "ongoing" set;
    # the modal hint reads "M prior sessions" which matches.
    try:
        prior_sessions = get_project_sessions(project_id, user_id=user_id, include_deleted=False)
        prior_sessions_count = len(prior_sessions)
    except Exception as e:
        logger.warning(f"[BRD context-preview] prior_sessions count failed: {e}")
        prior_sessions_count = 0

    # Top-K facts surfaced to the user verbatim. Empty query string -> the
    # memory store returns most-recent / most-relevant generally.
    top_facts_raw = _load_long_term_facts_for_router(
        user_id=user_id, project_id=project_id, query="",
        top_k=BRD_CONTEXT_PREVIEW_TOP_K,
    )
    top_facts = [{"category": _guess_fact_category(f), "text": f} for f in top_facts_raw]

    # available_facts_count = total facts in the namespace, capped at
    # what we retrieved. The memory store doesn't expose a cheap COUNT
    # so we surface what we loaded; the frontend treats this as a
    # lower bound ("at least N facts available"). When we hit the cap,
    # the displayed copy can read "5+ facts" to communicate the cap.
    available_facts_count = len(top_facts_raw)

    return BRDContextPreview(
        available_facts_count=available_facts_count,
        prior_sessions_count=prior_sessions_count,
        top_facts=top_facts,
        has_any_context=available_facts_count > 0,
    )


def _guess_fact_category(formatted_fact: str) -> str:
    """Map a formatted-fact line (as produced by
    services.brd_orchestrator_utils._format_structured_fact) back to a
    coarse category for the modal's category chip.

    The formatted lines start with a prefix like "stakeholder:" /
    "NFR/scale:" / "constraint/deadline:" — we just slice the prefix.
    Best-effort; "other" is fine when the line doesn't match a known
    prefix.
    """
    if not formatted_fact:
        return "other"
    head = formatted_fact.split(":", 1)[0].lower()
    if head.startswith("stakeholder"):    return "stakeholder"
    if head.startswith("nfr"):            return "non_functional_req"
    if head.startswith("constraint"):     return "constraint"
    if head.startswith("integration"):    return "integration"
    if head.startswith("assumption"):     return "assumption"
    if head.startswith("open question"):  return "open_question"
    return "other"


# --- GET /sessions/{session_id}/loaded-facts -------------------------------

@router.get("/sessions/{session_id}/loaded-facts", response_model=BRDLoadedFacts)
def get_loaded_facts(
    session_id: str,
    current_user: dict = Depends(get_current_user),
) -> BRDLoadedFacts:
    """Powers the persistent header badge's side panel.

    Returns the facts that ARE loaded for this session (i.e. the
    superset the handlers see when they call get_long_term_facts).
    When the session is in "Fresh" mode (use_long_term_context=False)
    returns an empty list immediately -- no point burning a memory
    retrieval call.
    """
    session = _ensure_session_owned(session_id, current_user["id"])

    if not session.get("use_long_term_context", True):
        return BRDLoadedFacts(facts=[], count=0, use_long_term_context=False)

    facts = _load_long_term_facts_for_router(
        user_id=session["user_id"],
        project_id=session["project_id"],
        query="",
        top_k=int(os.getenv("BRD_FACTS_TOP_K", "10")),
    )
    return BRDLoadedFacts(
        facts=facts,
        count=len(facts),
        use_long_term_context=True,
    )
