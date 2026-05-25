"""
BRD Orchestrator Lambda
========================

Single Lambda that powers the entire BRD chat surface for the unified
agent (features/aman). Replaces the pm_agent + analyst_agent AgentCore
Runtimes and the four overlapping BRD Lambdas with one direct-invoke
endpoint.

Action dispatch on `event["action"]`:

  turn                    -> intent router + per-intent handler (handle_turn)
  generate_from_docs      -> invoke lambda_brd_generator worker
  generate_from_history   -> invoke lambda_brd_from_history worker
  audit                   -> per-section parallel quality audit
  revert_section          -> pop the previous_versions stack on a section
  save_section            -> direct (no LLM) section save with version push
  cancel_generation       -> mark in-flight generation as discarded
  ingest_doc              -> classify doc + fold facts into long-term buffer
  ping                    -> warmup no-op (returns 200 immediately)

State lives in:
  - AgentCore Memory  (chat turns, dual-actor: writes under
    f"user-{user_id}", reads merge with legacy "analyst-session")
  - Long-term memory  (SEMANTIC strategy on the same memory store,
    namespace = "user-{user_id}:project-{project_id}", configured
    once via scripts/configure_brd_memory_strategy.py)
  - S3                (brds/{brd_id}/brd_structure.json with the
    previous_versions stack per section; ETag-protected writes)
  - RDS               (analyst_sessions row including new stage +
    use_long_term_context columns; this Lambda only READS the row,
    routers/brd.py owns the writes)

This Lambda doesn't touch RDS directly. The FastAPI router owns the
analyst_sessions row and flips stages when needed. The Lambda only
reads/writes S3 + AgentCore Memory and lets the router observe the
returned cards to decide stage transitions.

Mirrors lambda_sad_orchestrator.py structure intentionally — anyone
familiar with the SAD path can read this one without a tour.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3

# Prompt modules are imported lazily inside handlers so cold-start of
# a single action doesn't pull every prompt module's bytes.
# (lambda_sad_orchestrator.py:42-43 makes the same call.)


# ============================================
# Module config + logger
# ============================================

logger = logging.getLogger()
if not logger.handlers:
    logger.setLevel(logging.INFO)


# Env vars read at module load. Defaults match env_vdi.py / env_local.py
# so an unset var falls back to the documented behaviour.
AWS_REGION                       = os.getenv("AWS_REGION", "us-east-1")

AGENTCORE_MEMORY_ID              = os.getenv("AGENTCORE_MEMORY_ID", "")
S3_BUCKET_NAME                   = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")

# Dual-actor: NEW writes go under f"{ACTOR_PREFIX}{user_id}". Reads
# merge results from this actor AND the legacy LEGACY_ACTOR so chats
# from sessions older than the migration remain accessible.
BRD_AGENTCORE_ACTOR_PREFIX       = os.getenv("BRD_AGENTCORE_ACTOR_PREFIX", "user-")
BRD_AGENTCORE_LEGACY_ACTOR       = os.getenv("BRD_AGENTCORE_LEGACY_ACTOR", "analyst-session")

# Long-term memory namespace. Per-(user, project) so a user's facts
# about project A don't leak into their work on project B.
BRD_FACTS_NAMESPACE_TEMPLATE     = os.getenv(
    "BRD_FACTS_NAMESPACE_TEMPLATE", "user-{user_id}:project-{project_id}"
)
BRD_FACTS_TOP_K                  = int(os.getenv("BRD_FACTS_TOP_K", "10"))

# Model selection. Router uses the cheaper/faster model; handlers use
# the higher-quality one.
BRD_ROUTER_MODEL                 = os.getenv("BRD_ROUTER_MODEL",  "Claude-4.5-Sonnet")
BRD_HANDLER_MODEL                = os.getenv("BRD_HANDLER_MODEL", "Claude-4.5-Sonnet")

# Tuning knobs (matched to env_vdi defaults).
BRD_ROUTER_MAX_TOKENS            = int(os.getenv("BRD_ROUTER_MAX_TOKENS",  "400"))
BRD_ROUTER_TEMPERATURE           = float(os.getenv("BRD_ROUTER_TEMPERATURE", "0.0"))
BRD_EDIT_MAX_TOKENS              = int(os.getenv("BRD_EDIT_MAX_TOKENS",    "3000"))
BRD_SECTION_MAX_TOKENS           = int(os.getenv("BRD_SECTION_MAX_TOKENS", "4000"))
BRD_AUDIT_MAX_TOKENS             = int(os.getenv("BRD_AUDIT_MAX_TOKENS",   "1500"))
BRD_QA_MAX_TOKENS                = int(os.getenv("BRD_QA_MAX_TOKENS",      "900"))
BRD_SUGGEST_MAX_TOKENS           = int(os.getenv("BRD_SUGGEST_MAX_TOKENS", "900"))
BRD_GATHER_MAX_TOKENS            = int(os.getenv("BRD_GATHER_MAX_TOKENS",  "600"))

# Per-section revert stack depth.
BRD_PREVIOUS_VERSIONS_CAP        = int(os.getenv("BRD_PREVIOUS_VERSIONS_CAP", "5"))

# Parallel section generation budget.
BRD_SECTION_PARALLELISM          = int(os.getenv("BRD_SECTION_PARALLELISM", "5"))

# Worker Lambda names (kept Lambdas — orchestrator invokes them for
# the heavy generation paths).
BRD_GENERATOR_LAMBDA             = os.getenv("BRD_GENERATOR_LAMBDA",   "sdlc-dev-brd-generator")
BRD_FROM_HISTORY_LAMBDA          = os.getenv("BRD_FROM_HISTORY_LAMBDA", "sdlc-dev-brd-from-history")


# ============================================
# AWS clients (lazy — only constructed when first used)
# ============================================

_s3_client = None
_agentcore_client = None
_lambda_client = None


def _s3():
    """Lazy S3 client. Constructed once per Lambda container."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def _memory():
    """Lazy bedrock-agentcore client. Constructed once per container.
    Used for AgentCore Memory operations: create_event / list_events
    for short-term, retrieve_memory_records for long-term."""
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    return _agentcore_client


def _lambda():
    """Lazy AWS Lambda client. Used by generation handlers to invoke
    the worker Lambdas (brd-generator, brd-from-history)."""
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    return _lambda_client


# ============================================
# Card helper — orchestrator -> frontend response shape
# ============================================

def card(card_type: str, **payload: Any) -> Dict[str, Any]:
    """Wrap a handler return value in the {type, payload} envelope the
    frontend expects. Identical shape to SAD's card() so the same
    rendering code on the frontend can handle BRD cards too.
    """
    return {"type": card_type, "payload": payload}


# ============================================
# Action dispatch
# ============================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Single entry point. Dispatches on `event["action"]`. Returns
    `{statusCode, body}` envelope matching AWS Lambda conventions; the
    body is a JSON string the FastAPI router unwraps."""
    action = (event or {}).get("action") or "turn"
    request_id = (event or {}).get("request_id") or str(uuid.uuid4())[:8]
    logger.info(f"[BRD] action={action} request_id={request_id}")

    handler = INTENT_HANDLER_MAP.get(action)
    if handler is None:
        return _error_response(
            400,
            f"unknown action: {action!r}",
            allowed=sorted(INTENT_HANDLER_MAP.keys()),
        )

    try:
        result = handler(event)
        return {"statusCode": 200, "body": json.dumps(result)}
    except NotImplementedError as e:
        # Allow staged rollout — actions can land before their handlers.
        logger.warning(f"[BRD] action={action} not yet implemented: {e}")
        return _error_response(501, f"{action} not yet implemented")
    except Exception as e:
        logger.exception(f"[BRD] handler {action} failed")
        return _error_response(500, str(e))


def _error_response(status_code: int, message: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {"error": message}
    body.update(extra)
    return {"statusCode": status_code, "body": json.dumps(body)}


# ============================================
# Handlers — stubs filled in progressively across Phase 2 commits.
# Each commit lights up one or more of these without touching the
# dispatch table. NotImplementedError keeps the surface honest until
# real code lands.
# ============================================

def handle_ping(event: Dict[str, Any]) -> Dict[str, Any]:
    """Warmup no-op invoked by `POST /api/brd/warmup`. Touches the lazy
    clients so subsequent real calls land on already-warm boto3 pools.

    Returns a card with the container's request_id for observability."""
    # Touch the SDK clients so cold-start cost is paid here instead of
    # on the user's first real turn.
    _s3()
    _memory()
    _lambda()
    return card(
        "ping_response",
        request_id=str(uuid.uuid4())[:8],
        warm=True,
        timestamp=int(time.time()),
    )


def handle_turn(event: Dict[str, Any]) -> Dict[str, Any]:
    """Unified chat-box entry: run intent router, dispatch to a per-
    intent handler, persist USER+ASSISTANT events to AgentCore Memory.

    Body filled in by a subsequent Phase 2 commit.
    """
    raise NotImplementedError("handle_turn lands in a later Phase 2 commit")


def handle_generate_from_docs(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_generate_from_docs lands in a later Phase 2 commit")


def handle_generate_from_history(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_generate_from_history lands in a later Phase 2 commit")


def handle_audit(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_audit lands in a later Phase 2 commit")


def handle_revert_section(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_revert_section lands in a later Phase 2 commit")


def handle_save_section(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_save_section lands in a later Phase 2 commit")


def handle_cancel_generation(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_cancel_generation lands in a later Phase 2 commit")


def handle_ingest_doc(event: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("handle_ingest_doc lands in a later Phase 2 commit")


# ============================================
# Dispatch table — exported so tests/test_dispatch_coverage.py can
# assert every BRD_INTENT has a handler once handle_turn is wired up.
# Action keys here are Lambda actions (the dispatch level); the intent
# enum lives in prompts/brd_intent_router.py and maps to handlers
# INSIDE handle_turn.
# ============================================

INTENT_HANDLER_MAP: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "ping":                  handle_ping,
    "turn":                  handle_turn,
    "generate_from_docs":    handle_generate_from_docs,
    "generate_from_history": handle_generate_from_history,
    "audit":                 handle_audit,
    "revert_section":        handle_revert_section,
    "save_section":          handle_save_section,
    "cancel_generation":     handle_cancel_generation,
    "ingest_doc":            handle_ingest_doc,
}


# ============================================
# Card type catalogue — VALID_CARD_TYPES is the test surface
# tests/test_dispatch_coverage.py uses to verify every handler returns
# a recognised card type. Plan reference: hazy-gliding-hammock.md
# "Card type catalogue".
# ============================================

VALID_CARD_TYPES: frozenset = frozenset({
    # Chat / generic
    "text",
    "error",
    # Facts / ingest
    "fact_saved",
    "doc_ingested",
    # Section view + mutations
    "section_view",
    "section_updated",
    "section_regenerated",
    # Suggestions + audit
    "suggestions",
    "audit",
    # Generation lifecycle
    "generation_starting",
    "generation_in_progress",
    "generation_progress",
    "brd_generated",
    "generation_failed",
    "generation_cancelled",
    # Concurrency / disambiguation
    "concurrent_edit",
    "clarification",
    # Session-creation modal helpers (returned by FastAPI not by
    # the Lambda, but we list them so the catalogue is complete).
    "session_context_preview",
    "session_started",
    # Warmup
    "ping_response",
})
