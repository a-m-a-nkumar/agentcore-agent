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
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db_helper import (
    create_session,
    get_brd_session,
    get_project,
    get_project_sessions,
    increment_message_count,
    update_brd_session_stage,
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


# ============================================
# POST /turn — the workhorse. Frontend chat-box submits land here as
# multipart form data so the same endpoint handles plain text turns and
# single-file attachments. The orchestrator Lambda does the heavy lifting
# (intent routing -> handler -> card response). This router does:
#
#   1. Auth (session ownership) + UploadFile -> extracted_text.
#   2. Persist source file to S3 best-effort (so the user has a stable
#      reference if generation later wants to re-fetch).
#   3. Build the Lambda payload (`action: "turn"` + stage + last-card
#      context for the intent router's disambiguation rules).
#   4. Invoke orchestrator synchronously.
#   5. Normalise the response to `{cards: [...]}` (Lambda may return
#      either a single card or a cards-list envelope from the multi-file
#      bypass path).
#   6. Apply the canonical stage transition based on `next_stage_hint`
#      (orchestrator computes the hint; router writes the DB).
#   7. Increment message_count.
# ============================================

def _extract_uploaded_text(raw: bytes, filename: str) -> str:
    """Best-effort text extraction. Reuses app.extract_text when
    available -- it handles PDF / DOCX / TXT. Returns "" on failure
    so the orchestrator's ingest handler still gets a doc payload
    (with empty extracted_text -> "no extractable text" doc_ingested
    card)."""
    try:
        from app import extract_text  # late import; app may not be loaded in tests
    except Exception:
        return ""
    try:
        return extract_text(raw, filename) or ""
    except Exception as e:
        logger.warning(f"[BRD /turn] extract_text failed for {filename}: {e}")
        return ""


def _persist_source_file(
    session_id: str, filename: str, raw: bytes, content_type: Optional[str],
) -> Optional[str]:
    """Save the uploaded file to S3 under sessions/{sid}/sources/. Best
    effort -- failure logs and returns None; the chat turn proceeds
    using just the extracted text. Mirrors routers/sad.py:329-335."""
    try:
        from services.s3_service import s3_put_object
        key = f"sessions/{session_id}/sources/{filename}"
        s3_put_object(key=key, body=raw,
                      content_type=content_type or "application/octet-stream")
        return key
    except Exception as e:
        logger.warning(f"[BRD /turn] persist source file {filename} failed: {e}")
        return None


def _apply_stage_hint(session: Dict[str, Any], next_stage_hint: Optional[str]) -> None:
    """Translate the orchestrator's stage hint into a DB write. Idempotent
    when hint is None or matches current stage. Failures log -- the chat
    turn already succeeded; stage drift is repairable via the FastAPI
    session-fixup path during Phase 6 cleanup."""
    if not next_stage_hint:
        return
    current = session.get("stage")
    if current == next_stage_hint:
        return
    try:
        update_brd_session_stage(session["id"], next_stage_hint)
    except Exception as e:
        logger.warning(
            f"[BRD /turn] stage update {current!r} -> {next_stage_hint!r} "
            f"on {session.get('id')} failed (non-fatal): {e}"
        )


def _bump_message_count(session_id: str) -> None:
    """Best-effort message-count increment. Drives the project list
    sidebar's "N messages" affordance; a missed bump just understates
    the count, not a correctness bug."""
    try:
        increment_message_count(session_id)
    except Exception as e:
        logger.warning(f"[BRD /turn] message_count increment failed: {e}")


@router.post("/turn")
async def brd_turn(
    session_id: str               = Form(...),
    message: str                  = Form(""),
    project_id: Optional[str]     = Form(None),
    viewing_section: Optional[int] = Form(None),
    last_card_type: Optional[str] = Form(None),
    last_proposed_section: Optional[int] = Form(None),
    file: Optional[UploadFile]    = File(None),
    current_user: dict            = Depends(get_current_user),
):
    """One chat-box turn. Forwards to the orchestrator Lambda's `turn`
    action and returns `{cards: [...]}` (always a list, even for the
    common one-card response, so the frontend never has to branch).

    Stage transitions are derived from the orchestrator's
    `next_stage_hint` -- the Lambda computes the hint (it knows what
    intent classified), the router writes the DB (it owns the session
    row).
    """
    user_id = current_user["id"]
    session = _ensure_session_owned(session_id, user_id)

    # --- Single file upload (multi-file via Confluence URL is wired in
    #     a later commit -- file= takes priority over any future
    #     files= field) -------------------------------------------------
    file_payload: Optional[Dict[str, Any]] = None
    if file is not None:
        raw = await file.read()
        text = _extract_uploaded_text(raw, file.filename or "uploaded")
        s3_key = _persist_source_file(
            session_id, file.filename or "uploaded", raw, file.content_type
        )
        file_payload = {
            "filename": file.filename or "uploaded",
            "extracted_text": text,
        }
        if s3_key:
            file_payload["s3_key"] = s3_key

    # --- Lambda payload --------------------------------------------------
    payload = {
        "action": "turn",
        "session_id": session_id,
        "project_id": project_id or session.get("project_id"),
        "user_id": user_id,
        "message": message,
        "viewing_section": viewing_section,
        "last_card_type": last_card_type,
        "last_proposed_section": last_proposed_section,
        "stage": session.get("stage", "NEW"),
        "file": file_payload,
    }
    lambda_result = _invoke_brd_lambda(payload)

    # --- Normalise to {cards: [...]} ------------------------------------
    # Orchestrator returns either a single card dict OR
    # {cards: [...], intent: ..., next_stage_hint: ...} (the latter
    # comes from handle_turn / multi-file bypass).
    if isinstance(lambda_result, dict) and isinstance(lambda_result.get("cards"), list):
        cards = lambda_result["cards"]
        next_stage_hint = lambda_result.get("next_stage_hint")
    elif isinstance(lambda_result, dict):
        cards = [lambda_result]
        next_stage_hint = None  # single-card path doesn't carry the hint
    else:
        cards = []
        next_stage_hint = None

    # --- Apply stage transition + bump count ----------------------------
    _apply_stage_hint(session, next_stage_hint)
    _bump_message_count(session_id)

    return {"cards": cards}


# ============================================
# Write endpoints — direct (no LLM) section save + revert. The
# orchestrator does the actual write under If-Match etag protection;
# this router enforces auth, marshals the payload, and ensures any
# successful write that crossed a stage boundary (DRAFTED -> REFINING
# on first edit) is reflected on the analyst_sessions row.
# ============================================

class BRDSaveSectionRequest(BaseModel):
    session_id: str
    section_number: int
    content: List[Dict[str, Any]]  # array of content blocks


class BRDRevertSectionRequest(BaseModel):
    session_id: str
    section_number: int


def _maybe_promote_to_refining(session: Dict[str, Any], result_card: Dict[str, Any]) -> None:
    """First successful section_updated / section_regenerated /
    concurrent_edit-recovery on a DRAFTED session bumps stage to
    REFINING (canonical transition in db_helper.BRD_STAGE_TRANSITIONS).
    The orchestrator doesn't compute next_stage_hint for action-level
    write paths (it only does so for chat-intent paths) so the router
    derives it here.
    """
    if session.get("stage") != "DRAFTED":
        return
    if not isinstance(result_card, dict):
        return
    t = result_card.get("type")
    if t not in ("section_updated", "section_regenerated"):
        return
    try:
        update_brd_session_stage(session["id"], "REFINING")
    except Exception as e:
        logger.warning(f"[BRD write] DRAFTED -> REFINING failed on {session.get('id')}: {e}")


@router.post("/save-section")
def brd_save_section(
    body: BRDSaveSectionRequest,
    current_user: dict = Depends(get_current_user),
):
    """User typed/pasted section content into the editor and hit Save.
    Bypasses the LLM entirely (orchestrator's handle_save_section pushes
    the old content onto previous_versions, marks status=user_edited,
    writes under If-Match etag).

    Returns the section_updated card directly; the frontend already
    knows how to render it from the chat-card pipeline.
    """
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    result = _invoke_brd_lambda({
        "action": "save_section",
        "session_id": body.session_id,
        "user_id": user_id,
        "project_id": session.get("project_id"),
        "section_number": body.section_number,
        "content": body.content,
    })

    _maybe_promote_to_refining(session, result)
    return result


# ============================================
# Read endpoints — direct S3 / AgentCore Memory access. No Lambda
# invoke because reads don't need orchestration: the same data path
# the orchestrator uses (services.brd_orchestrator_utils) reads work
# fine from a FastAPI process. Saves a Lambda cold-start hop and
# ~200ms of latency on every page paint.
# ============================================

def _read_brd_structure(brd_id: str) -> Dict[str, Any]:
    """Best-effort fetch of brds/{brd_id}/brd_structure.json. Raises
    404 when missing -- the chat UI surfaces this as "no BRD drafted
    yet" rather than a stack trace.
    """
    from services.brd_orchestrator_utils import brd_structure_key, s3_get_json_with_etag
    try:
        structure, _etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        logger.warning(f"[BRD read] structure fetch failed for {brd_id}: {e}")
        raise HTTPException(status_code=502, detail=f"S3 read failed: {e}")
    if not structure:
        raise HTTPException(
            status_code=404,
            detail="BRD has not been generated for this session yet",
        )
    return structure


@router.get("/{session_id}/history")
def get_history(
    session_id: str,
    max_messages: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """Dump merged short-term chat history for a session.

    Uses the dual-actor read pattern (per-user actor + legacy
    'analyst-session') so historical chats from sessions older than
    the migration remain visible. Bounded by max_messages so the
    response doesn't balloon on very-long sessions; default 50 is
    enough for a chat panel's initial paint with infinite-scroll up.
    """
    _ensure_session_owned(session_id, current_user["id"])
    from services.brd_orchestrator_utils import read_memory_history
    try:
        messages = read_memory_history(
            session_id=session_id,
            user_id=current_user["id"],
            max_messages=max(1, min(max_messages, 200)),
        )
    except Exception as e:
        logger.error(f"[BRD history] read failed: {e}")
        raise HTTPException(status_code=502, detail=f"memory read failed: {e}")
    return {"messages": messages, "count": len(messages)}


@router.get("/{session_id}/sections")
def get_sections(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """First-paint endpoint -- returns one entry per section with
    metadata only (no content_json) so the frontend can render the
    table-of-contents sidebar quickly. The individual section endpoint
    below fetches the full content_json when the user navigates in.
    """
    session = _ensure_session_owned(session_id, current_user["id"])
    brd_id = session.get("brd_id")
    if not brd_id:
        raise HTTPException(
            status_code=404,
            detail="BRD has not been generated for this session yet",
        )
    structure = _read_brd_structure(brd_id)
    return {
        "brd_id": brd_id,
        "stage": session.get("stage"),
        "sections": [
            {
                "number": s.get("number"),
                "title": s.get("title"),
                "status": s.get("status"),
                "audit": s.get("audit"),
                "last_updated_ts": s.get("last_updated_ts"),
                "previous_versions_count": len(s.get("previous_versions") or []),
            }
            for s in (structure.get("sections") or [])
            if s.get("number") is not None
        ],
    }


@router.get("/{session_id}/section/{n}")
def get_section(
    session_id: str,
    n: int,
    current_user: dict = Depends(get_current_user),
):
    """Single-section refresh -- returns the full content_json for
    rendering in the section pane. Strips previous_versions from the
    wire shape (it's used by the orchestrator's revert path, never
    shown to the user directly).
    """
    session = _ensure_session_owned(session_id, current_user["id"])
    brd_id = session.get("brd_id")
    if not brd_id:
        raise HTTPException(
            status_code=404,
            detail="BRD has not been generated for this session yet",
        )
    structure = _read_brd_structure(brd_id)
    for s in structure.get("sections") or []:
        if s.get("number") == n:
            return {
                "number": s.get("number"),
                "title": s.get("title"),
                "content": s.get("content") or [],
                "status": s.get("status"),
                "audit": s.get("audit"),
                "last_updated_ts": s.get("last_updated_ts"),
                "previous_versions_count": len(s.get("previous_versions") or []),
            }
    raise HTTPException(status_code=404, detail=f"Section {n} not found")


# ============================================
# Generation endpoints — kicked off from the chat UI (via /turn) AND
# from explicit "Generate" buttons. Both paths funnel through the
# orchestrator's _start_generation body (async-invoke worker Lambda,
# return generation_starting immediately). This router additionally:
#
#   • Flips analyst_sessions.stage to GENERATING and records prior_stage
#     so cancellation knows where to revert.
#   • On generation_failed (worker invoke failed), reverts stage
#     immediately so the user can retry without being stuck.
#   • On cancel, sets stage back to prior_stage.
# ============================================

class BRDGenerateFromDocsRequest(BaseModel):
    session_id: str
    project_id: Optional[str] = None
    # Inline text (for short inputs from the chat composer) OR S3 keys
    # (preferred for any input > 1MB; matches the lambda_brd_generator
    # input contract). At least one of each pair must be present;
    # validation deferred to the Lambda.
    template: Optional[str] = None
    transcript: Optional[str] = None
    template_s3_bucket: Optional[str] = None
    template_s3_key: Optional[str] = None
    transcript_s3_bucket: Optional[str] = None
    transcript_s3_key: Optional[str] = None


class BRDGenerateFromHistoryRequest(BaseModel):
    session_id: str
    project_id: Optional[str] = None


class BRDCancelGenerationRequest(BaseModel):
    session_id: str
    brd_id: Optional[str] = None


class BRDAuditRequest(BaseModel):
    session_id: str
    project_id: Optional[str] = None
    # When set, only audit this section (cheap -- one LLM call).
    # Otherwise the full per-section parallel audit runs.
    section_number: Optional[int] = None


class BRDIngestDocRequest(BaseModel):
    session_id: str
    project_id: Optional[str] = None
    # Single-file shape OR multi-file shape — exactly one should be set.
    # Validation done in the orchestrator handler (bad_request if both
    # missing); router stays thin so a future Confluence-URL helper can
    # populate `files` server-side without touching this endpoint.
    file: Optional[Dict[str, Any]] = None
    files: Optional[List[Dict[str, Any]]] = None


def _enter_generating(session: Dict[str, Any]) -> None:
    """Stamp stage=GENERATING + record prior_stage. Idempotent --
    re-entering GENERATING from GENERATING is a no-op write so retry
    storms are safe.

    NOTE on prior_stage persistence: db_helper doesn't yet have a
    prior_stage column on analyst_sessions; for now we just bump stage
    and rely on the orchestrator's cancellation handler defaulting to
    GATHERING on revert. Phase 6e adds the prior_stage column.
    """
    if session.get("stage") == "GENERATING":
        return
    try:
        update_brd_session_stage(session["id"], "GENERATING")
    except Exception as e:
        logger.warning(f"[BRD gen] stage -> GENERATING failed on {session.get('id')}: {e}")


def _revert_generation_stage(session: Dict[str, Any], fallback: str = "GATHERING") -> None:
    """Roll the session back from GENERATING after a worker-invoke
    failure or user cancel. Defaults to GATHERING because that's the
    most-common pre-generation stage; the orchestrator's
    stage_reverted_to field overrides this when present.
    """
    target = fallback or "GATHERING"
    try:
        update_brd_session_stage(session["id"], target)
    except Exception as e:
        logger.warning(
            f"[BRD gen] stage revert to {target!r} on {session.get('id')} failed: {e}"
        )


def _generation_kickoff_response(
    session: Dict[str, Any],
    lambda_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Common post-invoke handling: flip stage based on the card the
    orchestrator returned. Returns the card verbatim so the frontend
    renders generation_starting / generation_failed identically to
    the chat-card pipeline.
    """
    if not isinstance(lambda_result, dict):
        return lambda_result
    card_type = lambda_result.get("type")
    if card_type == "generation_starting":
        _enter_generating(session)
    elif card_type == "generation_failed":
        revert_to = (lambda_result.get("payload") or {}).get("stage_reverted_to")
        _revert_generation_stage(session, fallback=revert_to or "GATHERING")
    return lambda_result


@router.post("/generate-from-docs")
def brd_generate_from_docs(
    body: BRDGenerateFromDocsRequest,
    current_user: dict = Depends(get_current_user),
):
    """Template + transcript -> full BRD. Orchestrator async-invokes
    the lambda_brd_generator worker with parallel=true (Phase 2 commit
    11) and returns generation_starting immediately.
    """
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    payload = {
        "action": "generate_from_docs",
        "session_id": body.session_id,
        "user_id": user_id,
        "project_id": body.project_id or session.get("project_id"),
        "template": body.template,
        "transcript": body.transcript,
        "template_s3_bucket": body.template_s3_bucket,
        "template_s3_key": body.template_s3_key,
        "transcript_s3_bucket": body.transcript_s3_bucket,
        "transcript_s3_key": body.transcript_s3_key,
    }
    return _generation_kickoff_response(session, _invoke_brd_lambda(payload))


@router.post("/generate-from-history")
def brd_generate_from_history(
    body: BRDGenerateFromHistoryRequest,
    current_user: dict = Depends(get_current_user),
):
    """"Generate the BRD" with no docs attached -- worker reads chat
    history from AgentCore Memory + the long-term facts buffer."""
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    payload = {
        "action": "generate_from_history",
        "session_id": body.session_id,
        "user_id": user_id,
        "project_id": body.project_id or session.get("project_id"),
    }
    return _generation_kickoff_response(session, _invoke_brd_lambda(payload))


@router.post("/cancel-generation")
def brd_cancel_generation(
    body: BRDCancelGenerationRequest,
    current_user: dict = Depends(get_current_user),
):
    """User clicked Cancel on an in-flight generation. Lambda
    acknowledges + emits generation_cancelled; router reverts stage.

    Cannot actually kill the in-flight worker (AWS Lambda has no
    stop-execution API for async invokes) -- the orchestrator's
    cancel handler relies on the worker's late-arriving result
    being discarded by the brd_id check that's added in a later
    Phase 3 commit (cancelled_brd_ids on the session row).
    """
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    result = _invoke_brd_lambda({
        "action": "cancel_generation",
        "session_id": body.session_id,
        "user_id": user_id,
        "brd_id": body.brd_id,
    })

    if isinstance(result, dict) and result.get("type") == "generation_cancelled":
        revert_to = (result.get("payload") or {}).get("stage_reverted_to")
        _revert_generation_stage(session, fallback=revert_to or "GATHERING")
    return result


@router.post("/audit")
def brd_audit(
    body: BRDAuditRequest,
    current_user: dict = Depends(get_current_user),
):
    """Per-section quality audit. Section_number set -> single-section
    cheap path (1 LLM call). Otherwise full parallel audit (16 LLM
    calls fanned out via ThreadPoolExecutor in the Lambda)."""
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    result = _invoke_brd_lambda({
        "action": "audit",
        "session_id": body.session_id,
        "user_id": user_id,
        "project_id": body.project_id or session.get("project_id"),
        "section_number": body.section_number,
    })
    # An audit IS a refinement action -- bump DRAFTED -> REFINING
    # per the canonical state machine table.
    _maybe_promote_to_refining(session, result)
    return result


@router.post("/ingest-doc")
def brd_ingest_doc(
    body: BRDIngestDocRequest,
    current_user: dict = Depends(get_current_user),
):
    """Ingest one or more docs as fact source. Single-file -> two LLM
    calls (relevance classifier + fact extraction). Multi-file -> loop
    each through the same body, only the last gets auto_regen=true."""
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    return _invoke_brd_lambda({
        "action": "ingest_doc",
        "session_id": body.session_id,
        "user_id": user_id,
        "project_id": body.project_id or session.get("project_id"),
        "file": body.file,
        "files": body.files,
    })


@router.post("/revert-section")
def brd_revert_section(
    body: BRDRevertSectionRequest,
    current_user: dict = Depends(get_current_user),
):
    """Pop the most-recent entry off the section's previous_versions
    stack and make it the current content. Used by the Revert button on
    the section diff view.

    Same DRAFTED -> REFINING promotion as save-section: a successful
    revert IS a modification to the BRD, so the session moves into the
    refining stage if it wasn't already.
    """
    user_id = current_user["id"]
    session = _ensure_session_owned(body.session_id, user_id)

    result = _invoke_brd_lambda({
        "action": "revert_section",
        "session_id": body.session_id,
        "user_id": user_id,
        "project_id": session.get("project_id"),
        "section_number": body.section_number,
    })

    _maybe_promote_to_refining(session, result)
    return result


# ============================================
# POST /turn-stream — Real SSE for two intents: GATHER_REQUIREMENTS
# (Mary follow-ups) and ASK_QUESTION (long answers). All other intents
# fall through to /turn (Lambda invoke, complete card). Replaces the
# fake-3-word-chunker hack at app.py:2099-2108 with actual LLM token
# streaming via the DLX gateway's stream=true path.
#
# Intent router runs INLINE in the FastAPI worker -- no Lambda hop for
# this path. Cheaper (one Sonnet/Haiku call instead of Sonnet+Sonnet)
# and faster (300-400ms latency floor lifted).
#
# SSE event types yielded:
#   data: {"type": "intent", "intent": "..."}          (first event)
#   data: {"type": "chunk", "text": "..."}             (during streaming)
#   data: {"type": "done", "card_summary": "..."}      (last event)
#   data: {"type": "fallback", "card": {...}}          (when intent
#                                                       isn't streamable)
#   data: {"type": "error", "message": "..."}          (on any failure)
# ============================================

# Hard-coded set so a future intent rename in the orchestrator doesn't
# silently enable streaming for a handler that doesn't support it. The
# orchestrator dispatch table is the source of truth for non-streaming
# intents; this list governs streaming eligibility.
_STREAMABLE_INTENTS = frozenset({"GATHER_REQUIREMENTS", "ASK_QUESTION"})

# Per-user SSE caps. Hard timeout bounds a runaway stream; idle timeout
# (not enforced here but documented for the frontend) bounds a client
# that connected but isn't reading.
BRD_SSE_HARD_TIMEOUT_SECONDS = int(os.getenv("BRD_SSE_HARD_TIMEOUT_SECONDS", "120"))


def _stream_sse(event_type: str, **payload: Any) -> str:
    """Format one SSE event line. Trailing \\n\\n is the framing the
    EventSource spec requires."""
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def _classify_intent_inline(
    user_message: str, stage: str, brd_exists: bool, available_sections: List[Dict[str, Any]],
    user_id: str,
) -> str:
    """Run the intent router inline (no Lambda hop). Returns just the
    intent string -- the streaming path doesn't need the full router
    payload, only the routing decision. Failures fall back to
    GATHER_REQUIREMENTS (cheapest streamable intent) so the user still
    gets a useful response.
    """
    from llm_gateway import chat_completion
    from prompts.brd_intent_router import (
        build_router_prompt, get_router_system_prompt,
    )
    from services.brd_orchestrator_utils import extract_json
    try:
        user_content = build_router_prompt(
            user_message=user_message,
            stage=stage,
            brd_exists=brd_exists,
            available_sections=available_sections,
            currently_viewing_section=None,
            file_attached=False,
            template_attached=False,
            transcript_attached=False,
            last_assistant_card_type=None,
            last_assistant_proposed_section=None,
        )
        raw = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=get_router_system_prompt(),
            model=os.getenv("BRD_ROUTER_MODEL", "Claude-4.5-Sonnet"),
            temperature=0.0,
            max_tokens=400,
            user_id=user_id,
            token_source="routers.brd:turn_stream_router",
        )
        parsed = extract_json(raw)
        return (parsed.get("intent") or "GATHER_REQUIREMENTS")
    except Exception as e:
        logger.warning(f"[BRD /turn-stream] router failed, falling back: {e}")
        return "GATHER_REQUIREMENTS"


def _stream_mary_or_qa(
    *,
    intent: str,
    user_message: str,
    session: Dict[str, Any],
    user_id: str,
):
    """Generator that yields SSE events for streamable intents. Calls
    chat_completion_stream and re-yields its data: lines through the
    event-type wrapper.

    GATHER_REQUIREMENTS uses Mary's persona + recent history; ASK_QUESTION
    uses the QA prompt + the loaded BRD structure.
    """
    from llm_gateway import chat_completion_stream

    yield _stream_sse("intent", intent=intent)

    if intent == "GATHER_REQUIREMENTS":
        yield from _stream_gather(
            user_message=user_message, session=session, user_id=user_id,
        )
    elif intent == "ASK_QUESTION":
        yield from _stream_ask_question(
            user_message=user_message, session=session, user_id=user_id,
        )
    else:  # safety net -- caller should have filtered already
        yield _stream_sse("error", message=f"intent {intent!r} is not streamable")

    yield _stream_sse("done")


def _stream_gather(*, user_message: str, session: Dict[str, Any], user_id: str):
    """Yield SSE chunks for Mary's follow-up."""
    from llm_gateway import chat_completion_stream
    from prompts.requirements_gathering_prompts import (
        MARY_REQUIREMENTS_PROMPT,
        get_requirements_gathering_prompt,
    )
    from services.brd_orchestrator_utils import get_long_term_facts, read_memory_history

    # Build the same context the Lambda's _do_gather would.
    history_lines: List[str] = []
    try:
        for m in read_memory_history(session["id"], user_id, max_messages=12):
            history_lines.append(f"{(m.get('role') or 'assistant').upper()}: {m.get('content', '')}")
    except Exception as e:
        logger.warning(f"[BRD /turn-stream gather] history load failed: {e}")

    facts_block = ""
    if session.get("use_long_term_context", True):
        try:
            facts = get_long_term_facts(
                user_id=user_id, project_id=session.get("project_id"), query=user_message,
            )
            if facts:
                facts_block = ("\n\nKNOWN PROJECT CONTEXT:\n" +
                               "\n".join(f"  - {f}" for f in facts))
        except Exception as e:
            logger.warning(f"[BRD /turn-stream gather] facts load failed: {e}")

    conversation_context = ("Conversation so far:\n" +
                            ("\n".join(history_lines) if history_lines else "(this is the first message)") +
                            facts_block)
    prompt = get_requirements_gathering_prompt(conversation_context, user_message)

    yield from chat_completion_stream(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=MARY_REQUIREMENTS_PROMPT,
        model=os.getenv("BRD_HANDLER_MODEL", "Claude-4.5-Sonnet"),
        temperature=0.6,
        max_tokens=int(os.getenv("BRD_GATHER_MAX_TOKENS", "600")),
        user_id=user_id,
        token_source="routers.brd:turn_stream_gather",
    )


def _stream_ask_question(*, user_message: str, session: Dict[str, Any], user_id: str):
    """Yield SSE chunks for a Q&A answer grounded in the BRD."""
    from llm_gateway import chat_completion_stream
    from prompts.brd_qa_prompts import QA_SYSTEM_PROMPT, build_qa_prompt

    brd_id = session.get("brd_id")
    relevant_sections: List[Dict[str, Any]] = []
    if brd_id:
        try:
            structure = _read_brd_structure(brd_id)
            relevant_sections = list(structure.get("sections") or [])
        except HTTPException:
            # No BRD yet -- still stream a graceful "no BRD to query" reply.
            pass

    if not relevant_sections:
        # Yield a single canned chunk + done so the client closes cleanly
        # instead of an empty stream.
        yield _stream_sse("chunk", text=(
            "There's no BRD draft to answer questions about yet. "
            "Want to start gathering requirements?"
        ))
        return

    user_content = build_qa_prompt(
        question=user_message,
        relevant_sections=relevant_sections,
        known_facts=[],
    )
    yield from chat_completion_stream(
        messages=[{"role": "user", "content": user_content}],
        system_prompt=QA_SYSTEM_PROMPT,
        model=os.getenv("BRD_HANDLER_MODEL", "Claude-4.5-Sonnet"),
        temperature=0.3,
        max_tokens=int(os.getenv("BRD_QA_MAX_TOKENS", "900")),
        user_id=user_id,
        token_source="routers.brd:turn_stream_qa",
    )


@router.post("/turn-stream")
async def brd_turn_stream(
    session_id: str               = Form(...),
    message: str                  = Form(""),
    project_id: Optional[str]     = Form(None),
    current_user: dict            = Depends(get_current_user),
):
    """SSE endpoint for streamable intents. Client opens an EventSource
    and reads `data:` lines until `{"type": "done"}` arrives.

    Routing flow:
      1. Auth + load session.
      2. Inline intent classification (no Lambda hop -- saves 200-400ms).
      3. If intent is streamable (GATHER_REQUIREMENTS / ASK_QUESTION):
         yield SSE events directly from chat_completion_stream.
      4. If intent is NOT streamable: yield one "fallback" event with
         a hint pointing the client at /turn for this turn. The client
         then closes the stream and resubmits via /turn.

    Why a fallback event instead of just running the non-streamable
    handler here: a single endpoint that's "sometimes streaming,
    sometimes not" is hostile to clients. The frontend chat box only
    opens an EventSource when it WANTS streaming; if the router decides
    "actually, this needs full orchestration", asking the client to
    resubmit via /turn keeps both endpoints semantically clean.
    """
    user_id = current_user["id"]
    session = _ensure_session_owned(session_id, user_id)

    # Pre-load section list for the router so its disambiguation rules
    # can apply (e.g. ASK_QUESTION needs target_section guess).
    brd_id = session.get("brd_id")
    available_sections: List[Dict[str, Any]] = []
    brd_exists = False
    if brd_id:
        try:
            structure = _read_brd_structure(brd_id)
            brd_exists = True
            available_sections = [
                {"number": s.get("number"), "title": s.get("title") or "(untitled)"}
                for s in (structure.get("sections") or [])
                if s.get("number") is not None
            ]
        except HTTPException:
            brd_exists = False

    intent = _classify_intent_inline(
        user_message=message,
        stage=session.get("stage", "NEW"),
        brd_exists=brd_exists,
        available_sections=available_sections,
        user_id=user_id,
    )

    if intent not in _STREAMABLE_INTENTS:
        # Fallback: emit a single SSE event pointing the client at /turn.
        def fallback_gen():
            yield _stream_sse("intent", intent=intent)
            yield _stream_sse(
                "fallback",
                hint=f"Intent {intent!r} is not streamable; resubmit via POST /api/brd/turn.",
            )
            yield _stream_sse("done")
        return StreamingResponse(fallback_gen(), media_type="text/event-stream")

    return StreamingResponse(
        _stream_mary_or_qa(
            intent=intent,
            user_message=message,
            session=session,
            user_id=user_id,
        ),
        media_type="text/event-stream",
    )
