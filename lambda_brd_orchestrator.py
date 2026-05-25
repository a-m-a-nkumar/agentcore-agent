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

    handler = ACTION_HANDLER_MAP.get(action)
    if handler is None:
        return _error_response(
            400,
            f"unknown action: {action!r}",
            allowed=sorted(ACTION_HANDLER_MAP.keys()),
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
    """Unified chat-box entry. Pipeline:

      1. Defense-in-depth: re-verify session ownership in the Lambda
         (FastAPI already checked, but a future bug there must not
         leak data here).
      2. If stage == GENERATING: return generation_in_progress card
         WITHOUT running the router. The user can chat with other
         sessions but this one is occupied.
      3. If files attached (multi-file): bypass router, loop ingest.
      4. Persist USER event to AgentCore Memory under per-user actor.
      5. Load BRD structure (if exists) for section grounding.
      6. Call intent router (LLM + prompts/brd_intent_router).
      7. Stage-gate the returned intent — downgrade to safe fallback
         if the router picked something not valid at current stage.
      8. Dispatch to INTENT_TO_HANDLER_MAP[intent].
      9. Persist ASSISTANT event with a one-line card summary.
     10. Return {"cards": [result_card]} envelope the FastAPI router
         unwraps into its own response.
    """
    from services.brd_orchestrator_utils import (
        ConcurrentEditError,  # noqa: F401 (re-export for handler imports)
        brd_structure_key,
        extract_json,
        per_user_actor,  # noqa: F401
        read_memory_history,
        s3_get_json_with_etag,
        verify_session_owned,
        write_memory_event,
    )
    from prompts.brd_intent_router import (
        BRD_INTENTS,
        INTENT_VALID_STAGES,
        build_router_prompt,
        get_router_system_prompt,
    )

    # ── 1. Extract inputs ────────────────────────────────────────────
    session_id = (event or {}).get("session_id")
    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id")
    message    = (event or {}).get("message", "")
    stage      = (event or {}).get("stage", "NEW")
    file_payload  = (event or {}).get("file")
    files_payload = (event or {}).get("files") or []
    last_card_type        = (event or {}).get("last_card_type")
    last_proposed_section = (event or {}).get("last_proposed_section")
    currently_viewing_section = (event or {}).get("viewing_section")

    if not session_id or not user_id:
        return card(
            "error",
            code="bad_request",
            message="session_id and user_id are required",
            retryable=False,
        )

    # ── 2. Session ownership re-verify (mitigation #1) ───────────────
    try:
        session = verify_session_owned(session_id, user_id)
    except LookupError:
        return card("error", code="session_not_found", message=f"session {session_id} not found", retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="you don't own this session", retryable=False)

    # ── 3. GENERATING short-circuit ──────────────────────────────────
    if stage == "GENERATING":
        prior_stage = session.get("prior_stage") or "GATHERING"
        return card(
            "generation_in_progress",
            since_ts=session.get("generation_started_at"),
            brd_id=session.get("brd_id"),
            prior_stage=prior_stage,
        )

    # ── 4. Multi-file bypass — skip router, loop ingest ──────────────
    if files_payload:
        return _handle_multi_file_ingest(event, session)

    # ── 5. Persist USER event ────────────────────────────────────────
    write_memory_event(session_id, user_id, "USER", message)

    # ── 6. Load BRD structure for section grounding ──────────────────
    brd_id = session.get("brd_id")
    brd_exists = False
    available_sections: List[Dict[str, Any]] = []
    if brd_id:
        try:
            structure, _etag = s3_get_json_with_etag(brd_structure_key(brd_id))
        except Exception as e:
            logger.warning(f"[BRD] couldn't load brd_structure for {brd_id}: {e}")
            structure = None
        if structure and isinstance(structure.get("sections"), list):
            brd_exists = True
            available_sections = [
                {"number": s.get("number"), "title": s.get("title") or "(untitled)"}
                for s in structure["sections"]
                if isinstance(s, dict) and s.get("number") is not None
            ]

    # ── 7. Call the intent router ────────────────────────────────────
    router_payload = _call_intent_router(
        user_message=message,
        stage=stage,
        brd_exists=brd_exists,
        available_sections=available_sections,
        currently_viewing_section=currently_viewing_section,
        file_attached=bool(file_payload),
        template_attached=bool((event or {}).get("template")),
        transcript_attached=bool((event or {}).get("transcript")),
        last_assistant_card_type=last_card_type,
        last_assistant_proposed_section=last_proposed_section,
        user_id=user_id,
    )
    intent = router_payload.get("intent", "")
    if intent not in BRD_INTENTS:
        logger.warning(f"[BRD] router returned unknown intent {intent!r}; falling back to ADD_INFO")
        intent = "ADD_INFO" if brd_exists else "GATHER_REQUIREMENTS"
        router_payload["intent"] = intent
        router_payload["confidence"] = 0.0

    # ── 8. Stage-gate the intent — downgrade if invalid for stage ────
    valid_stages = INTENT_VALID_STAGES.get(intent, frozenset())
    if stage not in valid_stages:
        downgraded = "GATHER_REQUIREMENTS" if not brd_exists else "ASK_GENERAL"
        logger.info(
            f"[BRD] router intent {intent!r} not valid in stage {stage!r}; "
            f"downgrading to {downgraded!r}"
        )
        intent = downgraded
        router_payload["intent"] = downgraded
        router_payload["downgraded_from"] = router_payload.get("intent")

    # ── 9. Dispatch to intent handler ────────────────────────────────
    handler = INTENT_TO_HANDLER_MAP.get(intent)
    if handler is None:
        return card(
            "error",
            code="no_handler",
            message=f"no handler registered for intent {intent!r}",
            retryable=False,
        )

    try:
        result_card = handler(event, session, router_payload)
    except NotImplementedError as e:
        # Allow staged handler rollout: handler stubs return a
        # placeholder text card rather than 501 so the user sees
        # something coherent during dual-ship.
        logger.warning(f"[BRD] handler for {intent} not implemented: {e}")
        result_card = card(
            "text",
            text=f"(Coming soon) The {intent} handler is still under construction.",
            kind="warning",
        )

    # ── 10. Persist ASSISTANT event + return cards envelope ──────────
    summary = _summarize_card_for_memory(result_card)
    write_memory_event(session_id, user_id, "ASSISTANT", summary)

    return {
        "cards": [result_card],
        "intent": intent,
        "next_stage_hint": _next_stage_hint(intent, stage),
    }


# ============================================
# handle_turn support — router invocation, multi-file path,
# card-summary-for-memory, stage hint inference.
# ============================================

def _call_intent_router(
    *,
    user_message: str,
    stage: str,
    brd_exists: bool,
    available_sections: List[Dict[str, Any]],
    currently_viewing_section: Optional[int],
    file_attached: bool,
    template_attached: bool,
    transcript_attached: bool,
    last_assistant_card_type: Optional[str],
    last_assistant_proposed_section: Optional[int],
    user_id: Optional[str],
) -> Dict[str, Any]:
    """One LLM call to the configured router model. Returns the
    parsed JSON payload (intent + target_section + ...). On any
    failure (LLM error, JSON parse error) we fall back to a safe
    ADD_INFO / GATHER_REQUIREMENTS classification — never crash the
    user's turn over a router hiccup.
    """
    from services.brd_orchestrator_utils import extract_json
    from prompts.brd_intent_router import (
        build_router_prompt,
        get_router_system_prompt,
    )

    user_content = build_router_prompt(
        user_message=user_message,
        stage=stage,
        brd_exists=brd_exists,
        available_sections=available_sections,
        currently_viewing_section=currently_viewing_section,
        file_attached=file_attached,
        template_attached=template_attached,
        transcript_attached=transcript_attached,
        last_assistant_card_type=last_assistant_card_type,
        last_assistant_proposed_section=last_assistant_proposed_section,
    )

    try:
        # Lazy import to keep cold-start light for ping etc.
        from llm_gateway import chat_completion
        raw = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=get_router_system_prompt(),
            model=BRD_ROUTER_MODEL,
            temperature=BRD_ROUTER_TEMPERATURE,
            max_tokens=BRD_ROUTER_MAX_TOKENS,
            user_id=user_id,
            token_source="lambda_brd_orchestrator:router",
        )
        return extract_json(raw)
    except Exception as e:
        logger.warning(f"[BRD] router call failed: {e}; falling back to safety default")
        return {
            "intent": "ADD_INFO" if brd_exists else "GATHER_REQUIREMENTS",
            "target_section": None,
            "target_title": "",
            "fact": user_message[:500],
            "edit_instruction": "",
            "regen_proposed": False,
            "confidence": 0.0,
            "router_error": str(e),
        }


def _handle_multi_file_ingest(event: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
    """Bypass the router for multi-file uploads. Mirrors
    lambda_sad_orchestrator.py:778-801. Each file is processed by
    _do_ingest_doc; only the last gets auto_regen=true to avoid
    N regeneration cascades. Body fills in commit 10."""
    raise NotImplementedError(
        "multi-file ingest bypass lands with _do_ingest_doc in Phase 2 commit 10"
    )


def _summarize_card_for_memory(c: Dict[str, Any]) -> str:
    """One-line human-readable summary of a card for the ASSISTANT
    memory event. Mirrors lambda_sad_orchestrator.text_summary_for_memory."""
    t = c.get("type", "text")
    p = c.get("payload", {}) or {}
    if t == "text":
        return (p.get("text") or "")[:500]
    if t == "fact_saved":
        return f"[fact saved] {(p.get('text') or '')[:200]}"
    if t == "doc_ingested":
        return f"[doc ingested] {p.get('filename', '?')}"
    if t == "section_view":
        return f"[shown section {p.get('section_number', '?')}]"
    if t == "section_updated":
        return f"[section {p.get('section_number', '?')} updated]"
    if t == "section_regenerated":
        return f"[section {p.get('section_number', '?')} regenerated]"
    if t == "audit":
        return f"[audit completed: {len(p.get('badges', []))} sections]"
    if t == "suggestions":
        return f"[{len(p.get('items', []))} suggestions returned]"
    if t == "generation_starting":
        return "[generation starting]"
    if t == "brd_generated":
        return f"[BRD generated: {p.get('section_count', '?')} sections]"
    if t == "generation_failed":
        return f"[generation failed: {p.get('code', '?')}]"
    if t == "concurrent_edit":
        return f"[concurrent edit on section {p.get('section_number', '?')} — reload prompted]"
    if t == "clarification":
        return "[clarification requested]"
    return f"[{t}]"


def _next_stage_hint(intent: str, current_stage: str) -> Optional[str]:
    """Tell the FastAPI router whether this turn should bump the
    session stage. None means "leave stage alone"; the actual flip
    happens in routers/brd.py (Phase 3) since the Lambda doesn't
    touch RDS."""
    if intent in ("GENERATE_FROM_DOCS", "GENERATE_FROM_HISTORY"):
        return "GENERATING"
    if intent == "ADD_INFO" and current_stage in ("NEW",):
        return "GATHERING"
    if intent == "GATHER_REQUIREMENTS" and current_stage in ("NEW",):
        return "GATHERING"
    if intent in ("EDIT_SECTION", "REGENERATE_SECTION", "AUDIT") and current_stage == "DRAFTED":
        return "REFINING"
    return None


# ============================================
# Intent handler stubs (INNER dispatch level)
# Filled in across Phase 2 commits 4-10. Each takes:
#   event          — the original Lambda event dict
#   session        — verified analyst_sessions row
#   router_payload — {intent, target_section, target_title, fact,
#                     edit_instruction, regen_proposed, confidence}
# Each returns ONE card dict.
# ============================================

def _do_ask_general(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_ask_general lands in Phase 2 commit 4")


def _do_ask_question(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_ask_question lands in Phase 2 commit 4")


def _do_show_section(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_show_section lands in Phase 2 commit 4")


def _do_suggest(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_suggest lands in Phase 2 commit 7")


def _do_add_info(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_add_info lands in Phase 2 commit 8")


def _do_edit_section(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_edit_section lands in Phase 2 commit 5")


def _do_gather(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_gather lands in Phase 2 commit 8")


def _do_generate_from_docs(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_generate_from_docs lands in Phase 2 commit 9")


def _do_generate_from_history(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_generate_from_history lands in Phase 2 commit 9")


def _do_audit(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_audit lands in Phase 2 commit 6")


def _do_regenerate_section(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_regenerate_section lands in Phase 2 commit 7")


def _do_ingest_doc(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("_do_ingest_doc lands in Phase 2 commit 10")


# Map router-classified intent → handler. tests/test_dispatch_coverage
# asserts every BRD_INTENT has an entry here.
INTENT_TO_HANDLER_MAP: Dict[str, Callable[..., Dict[str, Any]]] = {
    "ASK_GENERAL":           _do_ask_general,
    "ASK_QUESTION":          _do_ask_question,
    "SHOW_SECTION":          _do_show_section,
    "SUGGEST":               _do_suggest,
    "ADD_INFO":              _do_add_info,
    "EDIT_SECTION":          _do_edit_section,
    "GATHER_REQUIREMENTS":   _do_gather,
    "GENERATE_FROM_DOCS":    _do_generate_from_docs,
    "GENERATE_FROM_HISTORY": _do_generate_from_history,
    "AUDIT":                 _do_audit,
    "REGENERATE_SECTION":    _do_regenerate_section,
    "INGEST_DOC":            _do_ingest_doc,
}


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
# Action-level dispatch — Lambda `event["action"]` → handler. This is
# the OUTER routing level. The INNER router (intent classifier output
# -> per-intent handler) lives inside handle_turn as INTENT_TO_HANDLER_MAP
# and is built in a later Phase 2 commit; tests/test_dispatch_coverage.py
# asserts the INNER map covers every BRD_INTENT once handle_turn lands.
# ============================================

ACTION_HANDLER_MAP: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
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
