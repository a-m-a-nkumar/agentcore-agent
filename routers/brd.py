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
from pydantic import BaseModel

from db_helper import get_brd_session

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
