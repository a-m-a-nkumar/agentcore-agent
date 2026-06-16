"""
BRD Orchestrator Lambda
========================

Single Lambda that powers the entire BRD chat surface for the unified
agent (features/aman). Replaces the pm_agent + analyst_agent AgentCore
Runtimes and the four overlapping BRD Lambdas with one direct-invoke
endpoint.

Action dispatch on `event["action"]`:

  turn                    -> intent router + per-intent handler (handle_turn)
  generate                -> invoke the single lambda_brd_generator worker
                             with a unified context (docs + chat history +
                             existing BRD + long-term facts)
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
# Exception: the cache-control helper is tiny (os + typing only) and is
# used by nearly every handler, so it's imported eagerly here.
from prompts.cache_control import cached_system_blocks


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
# How many recent chat turns Mary keeps as SHORT-TERM context. This is the
# "recent conversational flow" window only — IMPORTANT facts are never lost
# at the edge of it because the SEMANTIC strategy extracts them into LONG-TERM
# memory (get_long_term_facts), which has no turn cap. Raised 12 -> 30 so Mary
# follows longer gathering threads; bump via env if sessions get very long.
BRD_HISTORY_TURNS                = int(os.getenv("BRD_HISTORY_TURNS",      "30"))

# Per-section revert stack depth.
BRD_PREVIOUS_VERSIONS_CAP        = int(os.getenv("BRD_PREVIOUS_VERSIONS_CAP", "5"))

# Parallel section generation budget.
BRD_SECTION_PARALLELISM          = int(os.getenv("BRD_SECTION_PARALLELISM", "5"))

# Ingest-time chunking. A single uploaded/Confluence doc whose extracted text
# exceeds BRD_INGEST_MAX_CHARS is split into ~BRD_INGEST_CHUNK_CHARS pieces and
# fact-extracted per chunk (bounded parallel), then the facts are merged. This
# keeps each extraction call well within the model context and avoids
# lost-in-the-middle on very long docs. ~4 chars/token, so 40k chars ~= 10k tok.
BRD_INGEST_MAX_CHARS             = int(os.getenv("BRD_INGEST_MAX_CHARS",   "40000"))
BRD_INGEST_CHUNK_CHARS           = int(os.getenv("BRD_INGEST_CHUNK_CHARS", "40000"))
BRD_INGEST_CHUNK_OVERLAP         = int(os.getenv("BRD_INGEST_CHUNK_OVERLAP", "1000"))

# Phase 6 feature flag — controls whether generation Lambdas take the
# prime-then-fan-out parallel path with prompt caching. Default ON since
# the path has been verified end-to-end (gateway cache pass-through
# confirmed; smoke test shows cache_read=2815 tokens per section).
# Flip to "false" to roll back to the monolithic path without
# redeploying the workers.
def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

BRD_USE_PARALLEL_GENERATION      = _flag("BRD_USE_PARALLEL_GENERATION", True)

# Worker Lambda names (kept Lambdas — orchestrator invokes them for
# the heavy generation paths).
BRD_GENERATOR_LAMBDA             = os.getenv("BRD_GENERATOR_LAMBDA",   "sdlc-dev-brd-generator")


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
    # FastAPI fetches the session row before invoking us and embeds it
    # under event["session"]. We re-check the user_id match without
    # touching the DB — keeps the Lambda out of the VPC.
    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
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
    # project_id scopes the actor so the built-in SEMANTIC strategy extracts
    # this user's statements into the PROJECT's long-term namespace (not a
    # flat per-user bucket). Each session belongs to exactly one project, so
    # this is well-defined; fall back to the session row if the event omitted it.
    project_id = project_id or session.get("project_id")
    write_memory_event(session_id, user_id, "USER", message, project_id=project_id)

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
    # A handler may return a single card OR a list of cards (e.g. the
    # whole-BRD SUGGEST fans out one suggestions card per section).
    cards_out = result_card if isinstance(result_card, list) else [result_card]
    if len(cards_out) > 1:
        summary = f"[{len(cards_out)} cards]"
    else:
        summary = _summarize_card_for_memory(cards_out[0]) if cards_out else "[empty]"
    write_memory_event(session_id, user_id, "ASSISTANT", summary, project_id=project_id)

    return {
        "cards": cards_out,
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
            system_prompt=cached_system_blocks(get_router_system_prompt()),
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
    lambda_sad_orchestrator.py:778-801.

    Each file is processed by _ingest_one_doc; only the LAST gets
    auto_regen=true so the frontend triggers a single regeneration
    pass over the union of affected sections, instead of N
    regenerations cascading on top of each other. (Confirmed safer
    in SAD production; the multi-file path is the dominant ingest
    pattern when users paste several Confluence URLs at once.)

    Returns {"cards": [doc_ingested, doc_ingested, ...]}. handle_turn
    detects the cards-list shape and short-circuits the usual single-
    card return.
    """
    files = (event or {}).get("files") or []
    cards: List[Dict[str, Any]] = []
    last_idx = len(files) - 1
    for i, file_payload in enumerate(files):
        c = _ingest_one_doc(
            event=event,
            session=session,
            file_payload=file_payload,
            auto_regen=(i == last_idx),
        )
        cards.append(c)
    return {"cards": cards, "intent": "INGEST_DOC", "next_stage_hint": _next_stage_hint("INGEST_DOC", (event or {}).get("stage", "NEW"))}


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
    if intent == "GENERATE_BRD":
        return "GENERATING"
    if intent == "ADD_INFO" and current_stage in ("NEW",):
        return "GATHERING"
    if intent == "GATHER_REQUIREMENTS" and current_stage in ("NEW",):
        return "GATHERING"
    if intent in ("EDIT_SECTION", "REGENERATE_SECTION", "AUDIT") and current_stage == "DRAFTED":
        return "REFINING"
    return None


def apply_stage_transition(
    *,
    current_stage: str,
    event: str,
    prior_stage: Optional[str] = None,
) -> Optional[str]:
    """Resolve the next stage for a (current_stage, event) pair using the
    canonical BRD_STAGE_TRANSITIONS table in db_helper — the single source
    of truth the FastAPI router commits against.

    A table value of None means "revert to the prior stage" (generation
    failure / cancellation); the caller supplies prior_stage. A pair not
    in the table is a no-op (returns current_stage unchanged).

    Events: generate_accepted, generation_success, generation_failure,
    generation_cancel, generation_in_progress, first_refinement_action.
    """
    from db_helper import BRD_STAGE_TRANSITIONS
    if (current_stage, event) not in BRD_STAGE_TRANSITIONS:
        return current_stage
    to_stage = BRD_STAGE_TRANSITIONS[(current_stage, event)]
    return prior_stage if to_stage is None else to_stage


# ============================================
# Intent handler stubs (INNER dispatch level)
# Filled in across Phase 2 commits 4-10. Each takes:
#   event          — the original Lambda event dict
#   session        — verified analyst_sessions row
#   router_payload — {intent, target_section, target_title, fact,
#                     edit_instruction, regen_proposed, confidence}
# Each returns ONE card dict.
# ============================================

def _do_ask_general(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """Greetings / small talk / capabilities AND meta-questions about the
    project ("what have we covered?", "what do you know so far?").

    Grounds on this session's short-term history + the project's long-term
    facts (when the session opted into project context) so Mary references
    what was actually established instead of HALLUCINATING a generic BRD
    example. The fix for the "Smart Inventory Management System" invention:
    a context-free capabilities prompt let the LLM make up project details
    when asked "what have we covered" in a fresh session whose real content
    lives only in long-term memory.

    Stays cheap: short-term history is the current session only, long-term
    facts are top-K. The stable system prompt remains cacheable; the volatile
    context rides in the user content.
    """
    from llm_gateway import chat_completion
    from services.brd_orchestrator_utils import (
        get_facts_scoped,
        read_memory_history,
    )

    message    = (event or {}).get("message", "")
    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    use_long_term = session.get("use_long_term_context", True)

    # ── Short-term: this session's turns (so "what did we just discuss" works) ──
    history_lines: List[str] = []
    if session_id and user_id:
        try:
            for m in read_memory_history(
                session_id, user_id, max_messages=BRD_HISTORY_TURNS, project_id=project_id
            ):
                history_lines.append(
                    f"{(m.get('role') or 'assistant').upper()}: {m.get('content', '')}"
                )
        except Exception as e:
            logger.warning(f"[BRD] _do_ask_general history load failed (non-fatal): {e}")

    # ── Long-term facts, scoped by context mode ──────────────────────────────
    # Continue -> project-wide (all sessions); Start fresh -> THIS session's own
    # facts (so Mary still recalls this session beyond the short-term window).
    facts: List[str] = get_facts_scoped(
        user_id=user_id, project_id=project_id, session_id=session_id,
        query=message, use_long_term=use_long_term,
    )

    context_parts: List[str] = []
    if facts:
        context_parts.append(
            "Known project context (established across prior sessions):\n"
            + "\n".join(f"  - {f}" for f in facts)
        )
    if history_lines:
        context_parts.append("This session so far:\n" + "\n".join(history_lines))
    context_block = "\n\n".join(context_parts)

    system_prompt = (
        "You are Mary, the BRD assistant. Reply warmly and concisely (1-4 sentences).\n"
        "GROUNDING RULES — follow exactly:\n"
        "- When the user asks what has been covered / discussed / established, or "
        "anything about THEIR project, answer ONLY from the CONTEXT block in the "
        "user message (the 'Known project context' and 'This session so far' parts).\n"
        "- NEVER invent project names, systems, requirements, or details that the "
        "CONTEXT does not contain. If the CONTEXT is empty or doesn't cover what they "
        "asked, say plainly that you don't have that captured yet and offer to gather it. "
        "Do NOT fabricate an example project (e.g. an inventory system).\n"
        "- For pure greetings or 'what can you do' questions with no project content, "
        "briefly describe your capabilities: gather requirements, generate BRDs from chat "
        "or uploaded docs, edit / regenerate sections, audit quality, suggest improvements, "
        "and answer questions about an existing BRD."
    )

    user_content = (
        (f"CONTEXT:\n{context_block}\n\n" if context_block else "CONTEXT: (nothing captured yet)\n\n")
        + f"User's message:\n{message}"
    )

    try:
        text = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system_prompt,
            model=BRD_HANDLER_MODEL,
            temperature=0.3,
            max_tokens=400,
            user_id=user_id,
            token_source="lambda_brd_orchestrator:ask_general",
        )
    except Exception as e:
        logger.warning(f"[BRD] _do_ask_general LLM call failed: {e}")
        text = (
            "Hi! I help you draft BRDs — I can gather requirements, generate "
            "drafts, and edit / audit existing sections. What would you like "
            "to do?"
        )

    return card("text", text=text)


def _do_show_section(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """View a section verbatim (or the full TOC when target_section is
    None / 'list all'). No LLM call — pure S3 read.

    Resolution order for the section:
      1. router.target_section (int) — exact number, preferred.
      2. router.target_title (str) — fuzzy-match against current titles.
      3. Neither — return the full TOC (every section, content omitted).
    """
    from services.brd_orchestrator_utils import brd_structure_key, s3_get_json_with_etag

    brd_id = session.get("brd_id")
    if not brd_id:
        return card(
            "error",
            code="no_brd",
            message="There is no BRD draft to show yet.",
            retryable=False,
        )

    try:
        structure, _etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        logger.error(f"[BRD] _do_show_section S3 read failed for {brd_id}: {e}")
        return card("error", code="s3_read_failed", message=str(e), retryable=True)

    if not structure or not isinstance(structure.get("sections"), list):
        return card(
            "error",
            code="malformed_brd",
            message="BRD structure on S3 is malformed or empty.",
            retryable=False,
        )

    sections: List[Dict[str, Any]] = structure["sections"]
    target_number = router.get("target_section")
    target_title = (router.get("target_title") or "").strip()

    # 1. Number match
    if target_number is not None:
        for s in sections:
            if s.get("number") == target_number:
                return _build_section_view_card(s)
        return card(
            "error",
            code="section_not_found",
            message=f"Section #{target_number} not found in this BRD.",
            retryable=False,
        )

    # 2. Title fuzzy match (case-insensitive substring as a cheap
    #    proxy for RapidFuzz; the FastAPI router does the strict
    #    fuzzy match before invoking, this is the fallback).
    if target_title:
        needle = target_title.lower()
        matches = [s for s in sections if needle in (s.get("title") or "").lower()]
        if len(matches) == 1:
            return _build_section_view_card(matches[0])
        if len(matches) > 1:
            return card(
                "clarification",
                candidates=[
                    {"number": s.get("number"), "title": s.get("title")}
                    for s in matches
                ],
                original_intent="SHOW_SECTION",
            )

    # 3. No target — return TOC.
    return card(
        "section_view",
        section_number=None,
        title="Table of contents",
        is_toc=True,
        toc=[
            {"number": s.get("number"), "title": s.get("title") or "(untitled)"}
            for s in sections
        ],
    )


def _build_section_view_card(section: Dict[str, Any]) -> Dict[str, Any]:
    """Shape one section dict into a section_view card. Strips the
    previous_versions stack from the wire shape — it's only used by
    the revert handler, never shown to the user."""
    return card(
        "section_view",
        section_number=section.get("number"),
        title=section.get("title"),
        content_json=section.get("content") or [],
        last_updated_ts=section.get("last_updated_ts"),
        status=section.get("status"),
    )


def _do_ask_question(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """Q&A grounded in the existing BRD. One LLM call with the brd_qa
    prompt; the user-content block carries the matching sections plus
    optional long-term facts when the session opted-in.
    """
    from services.brd_orchestrator_utils import (
        brd_structure_key,
        extract_json,
        get_facts_scoped,
        read_memory_history,
        s3_get_json_with_etag,
    )
    from prompts.brd_qa_prompts import QA_SYSTEM_PROMPT, build_qa_prompt
    from llm_gateway import chat_completion

    user_id = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    question = (event or {}).get("message", "")
    brd_id = session.get("brd_id")

    if not brd_id:
        return card(
            "text",
            text="There's no BRD draft to query yet. Want to start gathering requirements?",
            kind="warning",
        )

    # Load the structure to ground the answer.
    try:
        structure, _etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        logger.error(f"[BRD] _do_ask_question S3 read failed: {e}")
        return card("error", code="s3_read_failed", message=str(e), retryable=True)

    if not structure or not isinstance(structure.get("sections"), list):
        return card(
            "text",
            text="The BRD draft is empty or malformed. Try regenerating it.",
            kind="warning",
        )

    # Heuristic relevant-section pick. router may have set a target;
    # if not, send every section (cheap because each is small JSON).
    relevant_sections: List[Dict[str, Any]] = []
    target_number = router.get("target_section")
    if target_number is not None:
        for s in structure["sections"]:
            if s.get("number") == target_number:
                relevant_sections = [s]
                break
    if not relevant_sections:
        relevant_sections = list(structure["sections"])

    # Long-term facts, scoped by context mode: project-wide when "Continue",
    # this session's own facts when "Start fresh".
    known_facts: List[str] = get_facts_scoped(
        user_id=user_id, project_id=project_id, session_id=session_id,
        query=question, use_long_term=session.get("use_long_term_context", True),
    )

    # Short-term history: lets Q&A resolve conversational follow-ups
    # ("what about the second risk?", "and its mitigation?") against the
    # turns just exchanged in THIS session.
    recent_history: List[str] = []
    if session_id and user_id:
        try:
            for m in read_memory_history(
                session_id, user_id, max_messages=BRD_HISTORY_TURNS, project_id=project_id
            ):
                recent_history.append(
                    f"{(m.get('role') or 'assistant').upper()}: {m.get('content', '')}"
                )
        except Exception as e:
            logger.warning(f"[BRD] _do_ask_question history load failed (non-fatal): {e}")

    user_content = build_qa_prompt(
        question=question,
        relevant_sections=relevant_sections,
        known_facts=known_facts,
        recent_history=recent_history,
    )

    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=cached_system_blocks(QA_SYSTEM_PROMPT),
            model=BRD_HANDLER_MODEL,
            temperature=0.3,
            max_tokens=BRD_QA_MAX_TOKENS,
            user_id=user_id,
            token_source="lambda_brd_orchestrator:qa",
        )
        parsed = extract_json(raw)
    except Exception as e:
        logger.warning(f"[BRD] _do_ask_question LLM/parse failed: {e}")
        return card(
            "text",
            text=(
                "Sorry, I couldn't answer that one — the model returned an "
                "unexpected response. Try rephrasing or asking about a "
                "specific section."
            ),
            kind="warning",
        )

    answer = parsed.get("answer") or ""
    citations = parsed.get("citations") or []
    # Front-end uses the first citation's section_number to render the
    # "go to §N" chip; bare text is fine if no citations came back.
    cited_section = None
    if citations and isinstance(citations[0], dict):
        cited_section = citations[0].get("section_number")

    return card(
        "text",
        text=answer,
        cited_section=cited_section,
        citations=citations,
    )


def _suggest_one_section(
    section: Dict[str, Any],
    *,
    user_id: Optional[str],
    project_id: Optional[str],
    use_long_term: bool,
) -> Dict[str, Any]:
    """Build a `suggestions` card for ONE section. One LLM call with the
    brd_suggest prompt; reuses any prior audit findings on the section and
    optionally seeds long-term project facts. Reused by the single-section
    path and the whole-BRD parallel fan-out. Never raises — on failure it
    returns a suggestions card with an empty items list."""
    from prompts.brd_suggest_prompts import SUGGEST_SYSTEM_PROMPT, build_suggest_prompt
    from services.brd_orchestrator_utils import extract_json, get_long_term_facts
    from llm_gateway import chat_completion

    section_number = section.get("number")
    audit_issues = (section.get("audit") or {}).get("issues") or []

    known_facts: List[str] = []
    if use_long_term:
        try:
            known_facts = get_long_term_facts(
                user_id=user_id,
                project_id=project_id,
                query=section.get("title") or f"section {section_number}",
            )
        except Exception as e:
            logger.warning(f"[BRD] suggest §{section_number} facts load failed (non-fatal): {e}")

    user_content = build_suggest_prompt(
        section_number=section_number,
        section_title=section.get("title") or "",
        current_content=section.get("content") or [],
        audit_issues=audit_issues,
        known_facts=known_facts,
    )

    items: List[Dict[str, Any]] = []
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=cached_system_blocks(SUGGEST_SYSTEM_PROMPT),
            model=BRD_HANDLER_MODEL,
            temperature=0.4,
            max_tokens=BRD_SUGGEST_MAX_TOKENS,
            user_id=user_id,
            token_source=f"lambda_brd_orchestrator:suggest_{section_number}",
        )
        parsed = extract_json(raw)
        got = parsed.get("items") if isinstance(parsed, dict) else []
        if isinstance(got, list):
            items = got
    except Exception as e:
        logger.warning(f"[BRD] _suggest_one_section §{section_number} failed: {e}")

    for i, it in enumerate(items):
        if isinstance(it, dict) and "id" not in it:
            it["id"] = f"sugg-{section_number}-{i}"

    return card(
        "suggestions",
        target_section=section_number,
        title=section.get("title"),
        items=items,
    )


def _do_suggest(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
):
    """Concrete improvements per section.

    Target resolution:
      1. router.target_section / target_title → suggestions for THAT section
         (single `suggestions` card).
      2. No specific section ("Suggest improvements for this BRD") → run
         suggest for EVERY section in bounded parallel and return one
         `suggestions` card per section (a list of cards), preceded by a
         short intro text card. This is the whole-BRD, section-wise mode.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from services.brd_orchestrator_utils import brd_structure_key, s3_get_json_with_etag

    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    brd_id     = session.get("brd_id")
    if not brd_id:
        return card("text", text="There's no BRD to suggest improvements for yet.",
                    kind="warning")

    try:
        structure, _etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        return card("error", code="s3_read_failed", message=str(e), retryable=True)
    if not structure or not isinstance(structure.get("sections"), list):
        return card("error", code="malformed_brd",
                    message="BRD structure malformed", retryable=False)

    sections = structure["sections"]
    use_long_term = session.get("use_long_term_context", True)
    section, section_number = _resolve_target_section(
        sections=sections,
        target_number=router.get("target_section"),
        target_title=router.get("target_title"),
    )
    if section == "ambiguous":
        return card(
            "clarification",
            candidates=[{"number": s.get("number"), "title": s.get("title")} for s in section_number],
            original_intent="SUGGEST",
        )

    # Single-section path — explicit "what's missing from §7".
    if section is not None:
        logger.info(f"[BRD] suggest single-section §{section_number} brd={brd_id}")
        return _suggest_one_section(
            section, user_id=user_id, project_id=project_id, use_long_term=use_long_term,
        )

    # Whole-BRD path — suggestions for EVERY section, bounded parallel so we
    # don't overwhelm the gateway (mirrors the audit fan-out).
    targets = [s for s in sections if s.get("number") is not None]
    logger.info(
        f"[BRD] suggest whole-BRD fan-out: {len(targets)} sections, "
        f"parallelism={BRD_SECTION_PARALLELISM} brd={brd_id}"
    )
    results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=BRD_SECTION_PARALLELISM) as ex:
        futs = {
            ex.submit(
                _suggest_one_section,
                s, user_id=user_id, project_id=project_id, use_long_term=use_long_term,
            ): s.get("number")
            for s in targets
        }
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                results[n] = fut.result()
            except Exception as e:
                logger.warning(f"[BRD] whole-BRD suggest §{n} failed: {e}")

    ordered = [results[s.get("number")] for s in targets if s.get("number") in results]
    logger.info(
        f"[BRD] suggest whole-BRD ✓ {len(ordered)}/{len(targets)} section cards produced"
    )
    intro = card(
        "text",
        text="Here are suggestions for each section — apply or edit any item "
             "from its section card below.",
        kind="info",
    )
    return [intro, *ordered]


def _do_add_info(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """User volunteers a fact, no edit verb.

    Pipeline:
      1. Persist the fact to AgentCore Memory under the per-user actor
         (USER event; the long-term SEMANTIC strategy picks it up
         asynchronously and extracts structured facts into the
         project namespace — writes always happen, even when the
         current session opted out of READING long-term context).
      2. Generate a Mary-style follow-up using the requirements-
         gathering prompt so the conversation keeps moving instead of
         dead-ending on a fact ack.
      3. Return a fact_saved card. If router.regen_proposed is true
         AND the fact maps to a known section, the frontend renders
         a "Regenerate §N?" chip.

    NOTE on memory write order: write_memory_event for the original
    USER message already happened in handle_turn before dispatch;
    here we only call gather to build the follow-up and return the
    card. The follow-up text is what handle_turn writes back as the
    ASSISTANT event.
    """
    from prompts.requirements_gathering_prompts import (
        MARY_REQUIREMENTS_PROMPT,
        get_requirements_gathering_prompt,
    )
    from services.brd_orchestrator_utils import get_long_term_facts, read_memory_history
    from llm_gateway import chat_completion

    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    fact_text  = (router.get("fact") or (event or {}).get("message") or "").strip()
    target_section = router.get("target_section")
    regen_proposed = bool(router.get("regen_proposed"))

    if not fact_text:
        return card("text", text="(empty input)", kind="warning")

    follow_up = _build_mary_followup(
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
        user_message=fact_text,
        use_long_term=session.get("use_long_term_context", True),
        token_source="lambda_brd_orchestrator:add_info_followup",
        brd_id=session.get("brd_id"),
    )

    return card(
        "fact_saved",
        fact_id=f"fact-{uuid.uuid4().hex[:8]}",
        text=fact_text,
        suggested_section=target_section,
        regen_proposed=regen_proposed,
        follow_up=follow_up,
    )


def _build_mary_followup(
    *,
    user_id: Optional[str],
    project_id: Optional[str],
    session_id: Optional[str],
    user_message: str,
    use_long_term: bool,
    token_source: str,
    brd_id: Optional[str] = None,
) -> str:
    """Shared Mary follow-up builder used by ADD_INFO and GATHER.

    Returns the LLM's text response, or "" on any failure (the caller
    decides how to handle a missing follow-up — ADD_INFO surfaces an
    empty follow_up gracefully; GATHER falls back to a generic prompt).

    Cache structure (two ephemeral breakpoints):
      [system: MARY_REQUIREMENTS_PROMPT, cache_control]        <- breakpoint #1
      [user content[0]: <current_brd>...</current_brd>, cache_control]  <- breakpoint #2 (only when brd_id given)
      [user content[1]: conversation history + current user message]    <- volatile
    The BRD block stays byte-stable as long as the underlying
    brd_structure.json doesn't change, so within an active 5-minute
    Mary conversation every turn after the first reads both prefixes
    from cache instead of paying full input price.
    """
    from prompts.requirements_gathering_prompts import (
        MARY_REQUIREMENTS_PROMPT,
        get_requirements_gathering_prompt,
    )
    from prompts.cache_control import cache_control_value
    from services.brd_orchestrator_utils import (
        get_facts_scoped,
        read_memory_history,
    )
    from llm_gateway import chat_completion

    logger.info(
        f"[BRD-mary] follow-up start session={session_id} brd_id={brd_id} "
        f"use_long_term={use_long_term} user_msg_len={len(user_message)} "
        f"token_source={token_source}"
    )

    # ── 1. Short-term chat history (last 12 turns from AgentCore Memory) ───
    history_lines: List[str] = []
    if session_id and user_id:
        try:
            history = read_memory_history(session_id, user_id, max_messages=BRD_HISTORY_TURNS, project_id=project_id)
            for m in history[-12:]:
                role = (m.get("role") or "assistant").upper()
                history_lines.append(f"{role}: {m.get('content', '')}")
            logger.info(f"[BRD-mary] history loaded turns={len(history_lines)}")
        except Exception as e:
            logger.warning(f"[BRD-mary] history load failed (non-fatal): {e}")

    # ── 2. Long-term facts, scoped by context mode ────────────────────────
    # Continue -> project-wide (all sessions); Start fresh -> THIS session's
    # own facts (so Mary recalls this session beyond the short-term window).
    facts_block = ""
    facts = get_facts_scoped(
        user_id=user_id, project_id=project_id, session_id=session_id,
        query=user_message, use_long_term=use_long_term,
    )
    if facts:
        facts_block = (
            "\n\nKNOWN PROJECT CONTEXT (do not contradict; cite when relevant):\n"
            + "\n".join(f"  - {f}" for f in facts)
        )
    logger.info(
        f"[BRD-mary] long-term facts loaded count={len(facts)} "
        f"scope={'project' if use_long_term else 'session'}"
    )

    conversation_context = "Conversation so far:\n" + (
        "\n".join(history_lines) if history_lines else "(this is the first message)"
    ) + facts_block

    # ── 3. Current BRD state (Fix 1 — the in-session redundancy fix) ──────
    brd_block_text = ""
    if brd_id:
        try:
            from services.brd_orchestrator_utils import (
                brd_structure_key,
                s3_get_json_with_etag,
                render_brd_for_chat,
            )
            structure, _etag = s3_get_json_with_etag(brd_structure_key(brd_id))
            if structure and isinstance(structure.get("sections"), list):
                brd_text, gaps = render_brd_for_chat(structure)
                parts = [f"<current_brd>\n{brd_text}\n</current_brd>"]
                if gaps:
                    parts.append(
                        "<brd_gaps>\n"
                        + "\n".join(f"  - {g}" for g in gaps[:20])
                        + "\n</brd_gaps>"
                    )
                brd_block_text = "\n\n".join(parts)
                logger.info(
                    f"[BRD-mary] BRD context loaded brd_id={brd_id} "
                    f"sections={len(structure.get('sections') or [])} "
                    f"gaps={len(gaps)} chars={len(brd_block_text)}"
                )
            else:
                logger.info(f"[BRD-mary] BRD structure empty/malformed for brd_id={brd_id}")
        except Exception as e:
            logger.warning(f"[BRD-mary] BRD load failed (non-fatal): {e}")
    else:
        logger.info("[BRD-mary] no brd_id — skipping BRD context injection")

    # ── 4. Build messages with caching-friendly structure ─────────────────
    if brd_block_text:
        # Two content blocks: cached BRD prefix + volatile tail. cache_control
        # on the FIRST block means the prefix [system, BRD] caches together.
        user_content: Any = [
            {
                "type": "text",
                "text": brd_block_text,
                "cache_control": cache_control_value(),
            },
            {
                "type": "text",
                "text": (
                    conversation_context
                    + "\n\nUser's latest message: "
                    + user_message
                    + "\n\nRespond as Mary. Acknowledge their response and ask a "
                      "relevant follow-up question. Reference BRD sections by "
                      "number when content is already documented."
                ),
            },
        ]
        logger.info(
            f"[BRD-mary] LLM call WITH BRD cache block "
            f"brd_chars={len(brd_block_text)} ctx_chars={len(conversation_context)}"
        )
    else:
        # No BRD yet — fall back to today's single-string format (no second
        # cache breakpoint; only the system prefix caches).
        user_content = get_requirements_gathering_prompt(conversation_context, user_message)
        logger.info(
            f"[BRD-mary] LLM call WITHOUT BRD (no brd_id) "
            f"prompt_chars={len(user_content)}"
        )

    try:
        text = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=cached_system_blocks(MARY_REQUIREMENTS_PROMPT),
            model=BRD_HANDLER_MODEL,
            temperature=0.6,
            max_tokens=BRD_GATHER_MAX_TOKENS,
            user_id=user_id,
            token_source=token_source,
        ).strip()
        logger.info(f"[BRD-mary] LLM response len={len(text)}")
        return text
    except Exception as e:
        logger.warning(f"[BRD-mary] follow-up LLM failed (non-fatal): {e}")
        return ""


def _do_edit_section(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """LLM-based section edit. Pipeline:

      1. Resolve target section from router output (number first, then
         title fuzzy match). Return clarification card if ambiguous.
      2. Load BRD with etag (concurrent-edit protection).
      3. Build the edit prompt via prompts.brd_edit_prompts and call
         the configured handler model.
      4. Parse the returned JSON (model occasionally wraps in fences;
         extract_json handles it).
      5. Push old content onto previous_versions (cap from env).
      6. Write back with If-Match etag — surface concurrent_edit card
         if another writer beat us to it.
    """
    from services.brd_orchestrator_utils import (
        ConcurrentEditError,
        brd_structure_key,
        extract_json,
        s3_get_json_with_etag,
        s3_put_json_if_match,
    )
    from prompts.brd_edit_prompts import EDIT_SYSTEM_PROMPT, build_edit_prompt
    from llm_gateway import chat_completion

    user_id          = (event or {}).get("user_id")
    edit_instruction = router.get("edit_instruction") or (event or {}).get("message", "")
    brd_id           = session.get("brd_id")

    if not brd_id:
        return card("text", text="There's no BRD to edit yet.", kind="warning")

    try:
        structure, etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        return card("error", code="s3_read_failed", message=str(e), retryable=True)
    if not structure or not isinstance(structure.get("sections"), list):
        return card("error", code="malformed_brd",
                    message="BRD structure malformed", retryable=False)

    # Resolve target section.
    section, section_number = _resolve_target_section(
        sections=structure["sections"],
        target_number=router.get("target_section"),
        target_title=router.get("target_title"),
    )
    if section == "ambiguous":
        return card(
            "clarification",
            candidates=[
                {"number": s.get("number"), "title": s.get("title")}
                for s in section_number  # type: ignore[arg-type]
            ],
            original_intent="EDIT_SECTION",
        )
    if section is None:
        return card("text",
                    text=(f"I couldn't tell which section you meant. "
                          f"Try specifying a section number (1-{len(structure['sections'])})."),
                    kind="warning")

    # Build the variable user-content block.
    numbered_view = _render_section_numbered(section.get("content") or [])
    user_content = build_edit_prompt(
        section_number=section_number,
        section_title=section.get("title") or "",
        current_section_content_numbered=numbered_view,
        section_json=section,
        user_instruction=edit_instruction,
    )

    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=cached_system_blocks(EDIT_SYSTEM_PROMPT),
            model=BRD_HANDLER_MODEL,
            temperature=0.2,
            max_tokens=BRD_EDIT_MAX_TOKENS,
            user_id=user_id,
            token_source=f"lambda_brd_orchestrator:edit_section_{section_number}",
        )
        parsed = extract_json(raw)
    except Exception as e:
        logger.warning(f"[BRD] _do_edit_section LLM/parse failed: {e}")
        return card("text",
                    text="Sorry, the edit failed — model returned an unexpected response.",
                    kind="warning")

    new_content = parsed.get("content")
    if not isinstance(new_content, list):
        return card("text",
                    text="The model returned the section without a content array. "
                         "Try rephrasing the instruction.",
                    kind="warning")

    # Push the OLD content onto the revert stack, then overwrite.
    _push_previous_version(section, reason="edit_section")
    section["content"] = new_content
    section["status"] = "llm_edited"
    section["last_updated_ts"] = _now_iso()
    # The edit prompt is supposed to preserve the title verbatim; if
    # the model returned a different title we trust the original.
    if parsed.get("title"):
        section["title"] = parsed["title"]

    try:
        s3_put_json_if_match(brd_structure_key(brd_id), structure, etag)
    except ConcurrentEditError as ce:
        return card("concurrent_edit",
                    section_number=section_number,
                    current_etag=ce.current_etag,
                    your_etag=ce.your_etag)
    except Exception as e:
        return card("error", code="s3_write_failed", message=str(e), retryable=True)

    return card(
        "section_updated",
        section_number=section_number,
        title=section["title"],
        content_json=section["content"],
        diff_summary=_short_summary(edit_instruction),
        previous_versions_count=len(section["previous_versions"]),
        status=section["status"],
    )


# ============================================
# Shared helpers used by edit / save / revert / regenerate.
# Kept private with the leading underscore; nothing outside the
# orchestrator should reach in here.
# ============================================

def _find_section(sections: List[Dict[str, Any]], section_number: int) -> Optional[Dict[str, Any]]:
    """Return the section dict whose 'number' matches, or None."""
    for s in sections or []:
        if isinstance(s, dict) and s.get("number") == section_number:
            return s
    return None


def _resolve_target_section(
    *,
    sections: List[Dict[str, Any]],
    target_number: Optional[int],
    target_title: Optional[str],
) -> Tuple[Any, Any]:
    """Pick one section dict from the BRD using router output.

    Returns either:
      (section_dict, section_number)  — single match found
      ("ambiguous", [candidates])     — multiple title matches; caller
                                        should emit a clarification card
      (None, None)                    — no match
    """
    if target_number is not None:
        s = _find_section(sections, target_number)
        if s is not None:
            return s, target_number
        # number set but not found -> let caller decide
        return None, None

    if target_title:
        needle = target_title.strip().lower()
        if not needle:
            return None, None
        matches = [
            s for s in sections
            if isinstance(s, dict) and needle in (s.get("title") or "").lower()
        ]
        if len(matches) == 1:
            return matches[0], matches[0].get("number")
        if len(matches) > 1:
            return "ambiguous", matches

    return None, None


def _push_previous_version(section: Dict[str, Any], *, reason: str) -> None:
    """Push the section's CURRENT content onto previous_versions before
    a write. Caps depth at BRD_PREVIOUS_VERSIONS_CAP (FIFO eviction)."""
    if not isinstance(section.get("previous_versions"), list):
        section["previous_versions"] = []
    section["previous_versions"].append({
        "ts": _now_iso(),
        "reason": reason,
        "content": section.get("content") or [],
    })
    # FIFO trim — keep the most recent N
    cap = BRD_PREVIOUS_VERSIONS_CAP
    if cap > 0 and len(section["previous_versions"]) > cap:
        section["previous_versions"] = section["previous_versions"][-cap:]


def _render_section_numbered(content: List[Dict[str, Any]]) -> str:
    """Render section content blocks with [ITEM N] / [ROW N] tags so
    the edit prompt can refer to items by global unique IDs."""
    lines: List[str] = []
    item_counter = 1
    for block in content or []:
        t = (block or {}).get("type")
        if t == "paragraph":
            text = (block.get("text") or "")[:200]
            lines.append(f"PARAGRAPH: {text}")
        elif t in ("bullet", "bullet_list", "ordered_list"):
            items = block.get("items") or []
            kind = "BULLET LIST" if t in ("bullet", "bullet_list") else "ORDERED LIST"
            lines.append(f"{kind} ({len(items)} items):")
            for it in items:
                preview = (str(it) or "")[:150]
                lines.append(f"  [ITEM {item_counter}] {preview}")
                item_counter += 1
        elif t == "table":
            rows = block.get("rows") or []
            lines.append(f"TABLE ({len(rows)} rows):")
            if rows:
                header = " | ".join(str(c)[:30] for c in rows[0][:5])
                lines.append(f"  [HEADER] {header}")
                for i, r in enumerate(rows[1:], start=1):
                    cells = " | ".join(str(c)[:30] for c in r[:5])
                    lines.append(f"  [ROW {i}] {cells}")
        elif t == "heading":
            lines.append(f"HEADING (level {block.get('level', 3)}): {block.get('text', '')[:100]}")
    return "\n".join(lines) if lines else "(empty section)"


def _short_summary(text: str, max_len: int = 80) -> str:
    """Shorten a user instruction for the diff_summary card field."""
    if not text:
        return ""
    s = text.strip().split("\n")[0]
    return s[:max_len] + ("..." if len(s) > max_len else "")


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for `last_updated_ts` / `previous_versions[*].ts`."""
    import datetime as _dt
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _do_gather(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """Mary follow-up. Open-ended discovery: probes for missing
    context, suggests directions, surfaces patterns. Differs from
    ADD_INFO in that there's no fact to save — this intent fires when
    the router classifies a message as elaborative / hesitant /
    discovery-oriented ("tell me more about scale", "I'm not sure
    about the security model").

    Valid in EVERY stage (per INTENT_VALID_STAGES) — Mary can keep
    probing even after the BRD is drafted.

    Returns a plain text card. Long-term facts seeded only when the
    session opted in (Resolved Q#5).
    """
    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    message    = ((event or {}).get("message") or "").strip()

    if not message:
        return card(
            "text",
            text="Let's start simple — what problem are you trying to solve, "
                 "or what triggered this idea?",
        )

    text = _build_mary_followup(
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
        user_message=message,
        use_long_term=session.get("use_long_term_context", True),
        token_source="lambda_brd_orchestrator:gather",
        brd_id=session.get("brd_id"),
    )

    if not text:
        text = (
            "Tell me a bit more about that — what's the most important outcome "
            "you're trying to drive, and who feels the pain today?"
        )

    return card("text", text=text)


def _build_generation_context(
    event: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """Assemble the unified generation context the single generator
    Lambda consumes. Every generation considers ALL available sources;
    the user no longer chooses "from docs" vs "from history".

      - chat_session_id : always set — the worker reads dual-actor chat
                          history for this session.
      - transcript/template (inline or S3 keys) : only when a doc was
                          uploaded this turn.
      - existing_brd_id : set when the session already has a BRD, so a
                          re-generation merges with prior content (incl.
                          user edits) instead of overwriting it.
      - long_term_facts : resolved here (the orchestrator has working
                          memory access + knows project_id) and passed
                          inline, gated on the session's opt-in flag.
    """
    user_id    = (event or {}).get("user_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")

    context: Dict[str, Any] = {"chat_session_id": session_id}

    # Uploaded docs (if any this turn). FastAPI offloads large text to S3
    # and passes keys; small inline text passes through verbatim.
    for k in (
        "template", "template_text", "transcript", "transcript_text",
        "template_s3_bucket", "template_s3_key",
        "transcript_s3_bucket", "transcript_s3_key",
    ):
        v = (event or {}).get(k)
        if v:
            context[k] = v

    # Existing BRD → regeneration merge.
    existing_brd_id = session.get("brd_id") or (event or {}).get("brd_id")
    if existing_brd_id:
        context["existing_brd_id"] = existing_brd_id

    # Long-term facts for generation, scoped by context mode: "Continue" pulls
    # project-wide facts (all sessions); "Start fresh" pulls only THIS session's
    # own gathered facts so a fresh-angle BRD isn't polluted by other sessions.
    if user_id and project_id:
        try:
            from services.brd_orchestrator_utils import get_facts_scoped
            query = ((event or {}).get("message") or session.get("title") or "").strip()
            facts = get_facts_scoped(
                user_id=user_id, project_id=project_id, session_id=session_id,
                query=query, use_long_term=session.get("use_long_term_context", True),
            )
            if facts:
                context["long_term_facts"] = facts
        except Exception as e:
            logger.warning(f"[BRD] long-term facts for generation failed (continuing): {e}")

    return context


def _do_generate_brd(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """Unified full-BRD generation. Replaces the old from-docs / from-
    history split — one generator Lambda accepts any combination of
    (uploaded docs, chat history, existing BRD, long-term facts).

    Fires lambda_brd_generator ASYNCHRONOUSLY (InvocationType='Event');
    the user-blocking path returns a generation_starting card immediately
    while the worker writes per-section partials + brd_structure.json to
    S3. The FastAPI router bumps stage to GENERATING via _next_stage_hint.
    """
    return _start_generation(
        event=event,
        session=session,
        worker_lambda=BRD_GENERATOR_LAMBDA,
        worker_payload_extras={
            "context": _build_generation_context(event, session),
            # Phase 6: prime-then-fan-out with prompt caching. Env flag
            # BRD_USE_PARALLEL_GENERATION gates the parallel path so
            # operators can roll back without redeploying the worker.
            "parallel": BRD_USE_PARALLEL_GENERATION,
        },
        expected_seconds=40 if BRD_USE_PARALLEL_GENERATION else 90,
        source="unified",
    )


# ============================================
# Shared generation kickoff. Both _do_generate_* funnel through here
# so the brd_id minting, async-invoke pattern, and generation_starting
# card shape stay in one place.
# ============================================

def _start_generation(
    *,
    event: Dict[str, Any],
    session: Dict[str, Any],
    worker_lambda: str,
    worker_payload_extras: Dict[str, Any],
    expected_seconds: int,
    source: str,
) -> Dict[str, Any]:
    """Mint brd_id if needed, fire worker Lambda async, return
    generation_starting card.

    Failures invoking the worker -> generation_failed card with a
    retryable flag so the frontend can offer a "Retry" button. The
    FastAPI router observes the returned card type to decide whether
    to advance to GENERATING or stay at the prior stage.
    """
    user_id    = (event or {}).get("user_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")

    brd_id = session.get("brd_id") or (event or {}).get("brd_id") or str(uuid.uuid4())

    payload: Dict[str, Any] = {
        "brd_id":    brd_id,
        "user_id":   user_id,
        "project_id": project_id,
        "session_id": session_id,
    }
    payload.update({k: v for k, v in (worker_payload_extras or {}).items() if v is not None})

    try:
        _lambda().invoke(
            FunctionName=worker_lambda,
            InvocationType="Event",  # async fire-and-forget
            Payload=json.dumps(payload).encode("utf-8"),
        )
    except Exception as e:
        logger.exception(f"[BRD] generation worker invoke failed: lambda={worker_lambda} brd_id={brd_id}")
        return card(
            "generation_failed",
            code="worker_invoke_failed",
            message=f"Couldn't start generation: {e}",
            retryable=True,
            stage_reverted_to=session.get("stage") or "GATHERING",
            brd_id=brd_id,
            source=source,
        )

    # Pre-flight RAG estimate for the UI. The generator makes the
    # authoritative decision (logged as `[BRD-gen RAG]` in CloudWatch); this
    # is what the user sees on the progress card so they know whether their
    # inputs triggered per-section RAG. We can only measure the inline
    # transcript here — chat history is hydrated by the generator from
    # memory — so this is an estimate, not the final decision.
    ctx_bundle = (worker_payload_extras or {}).get("context") or {}
    transcript_text = (
        ctx_bundle.get("transcript")
        or ctx_bundle.get("transcript_text")
        or ""
    )
    transcript_chars = len(transcript_text) if isinstance(transcript_text, str) else 0
    rag_threshold_chars = 120000  # mirror lambda_brd_generator.BRD_RAG_MIN_CHARS default (~30k tokens)
    rag_mode = "rag" if transcript_chars >= rag_threshold_chars else "inline"

    return card(
        "generation_starting",
        session_id=session_id,
        brd_id=brd_id,
        expected_seconds=expected_seconds,
        source=source,
        rag_mode=rag_mode,
        corpus_chars=transcript_chars,
        rag_threshold_chars=rag_threshold_chars,
    )


def _do_audit(event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]) -> Dict[str, Any]:
    """Per-section parallel audit. Intent-level wrapper around the shared
    audit body. Honours router.target_section when the user asked to
    audit just one section ("audit §4"); otherwise audits everything.
    """
    target_section = router.get("target_section")
    return _run_audit_and_persist(
        session=session,
        user_id=(event or {}).get("user_id"),
        project_id=(event or {}).get("project_id") or session.get("project_id"),
        target_section=target_section,
    )


def _do_regenerate_section(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """Rewrite ONE section from scratch using its current content +
    accumulated facts as context. Differs from _do_edit_section in
    that there's no surgical "change X to Y" instruction — the model
    is told to produce a fresh, improved version.

    Pipeline:
      1. Resolve target section (number > title fuzzy match).
      2. Load BRD with etag.
      3. Build a regenerate prompt that includes:
           - section title + number
           - current content (so the LLM can preserve hard-won facts)
           - known long-term facts (when opted in)
           - the user's regen reason (router.edit_instruction or the
             original message — often "redo §4 with the new info")
      4. Parse JSON, push old content onto previous_versions, write
         back conditionally.
      5. Return section_regenerated card.
    """
    from services.brd_orchestrator_utils import (
        ConcurrentEditError,
        brd_structure_key,
        extract_json,
        get_facts_scoped,
        s3_get_json_with_etag,
        s3_put_json_if_match,
    )
    from llm_gateway import chat_completion

    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    brd_id     = session.get("brd_id")
    regen_reason = (router.get("edit_instruction") or (event or {}).get("message") or "").strip()

    if not brd_id:
        return card("text", text="There's no BRD to regenerate yet.", kind="warning")

    try:
        structure, etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        return card("error", code="s3_read_failed", message=str(e), retryable=True)
    if not structure or not isinstance(structure.get("sections"), list):
        return card("error", code="malformed_brd",
                    message="BRD structure malformed", retryable=False)

    section, section_number = _resolve_target_section(
        sections=structure["sections"],
        target_number=router.get("target_section"),
        target_title=router.get("target_title"),
    )
    if section == "ambiguous":
        return card(
            "clarification",
            candidates=[{"number": s.get("number"), "title": s.get("title")} for s in section_number],
            original_intent="REGENERATE_SECTION",
        )
    if section is None:
        return card("text",
                    text=("Which section should I regenerate? "
                          f"Try specifying a section number (1-{len(structure['sections'])})."),
                    kind="warning")

    known_facts: List[str] = get_facts_scoped(
        user_id=user_id, project_id=project_id, session_id=session_id,
        query=section.get("title") or f"section {section_number}",
        use_long_term=session.get("use_long_term_context", True),
    )

    logger.info(
        f"[BRD] regenerate §{section_number} brd={brd_id} "
        f"facts={len(known_facts)} reason={regen_reason[:60]!r}"
    )
    ok = _regenerate_section_in_memory(
        section,
        section_number=section_number,
        known_facts=known_facts,
        regen_reason=regen_reason,
        user_id=user_id,
    )
    if not ok:
        return card("text",
                    text="Sorry, the regeneration failed — model returned an unexpected response.",
                    kind="warning")
    logger.info(f"[BRD] regenerate §{section_number} ✓ {len(section['content'])} block(s) written")

    try:
        s3_put_json_if_match(brd_structure_key(brd_id), structure, etag)
    except ConcurrentEditError as ce:
        return card("concurrent_edit",
                    section_number=section_number,
                    current_etag=ce.current_etag,
                    your_etag=ce.your_etag)
    except Exception as e:
        return card("error", code="s3_write_failed", message=str(e), retryable=True)

    return card(
        "section_regenerated",
        section_number=section_number,
        title=section["title"],
        content_json=section["content"],
        previous_versions_count=len(section["previous_versions"]),
        regen_reason=_short_summary(regen_reason) if regen_reason else "(no specific instruction)",
        status=section["status"],
    )


# ============================================
# REGENERATE_SECTION prompt — inline because the proper per-section
# builder lives in lambda_brd_generator's prompt module, which gets
# refactored into reusable builders in Phase 2 commit 11. Keep this
# prompt minimal and structurally compatible so the future migration
# is a drop-in replacement.
# ============================================

_REGENERATE_SYSTEM_PROMPT = """\
You rewrite ONE section of a Business Requirements Document (BRD).

You receive:
  • The section's current title and number.
  • The section's CURRENT content (JSON content blocks) — preserve
    every concrete fact already in there. Only rephrase, restructure,
    or fill gaps.
  • Optional "known project context" — long-term facts established
    across prior sessions. Treat as authoritative; do not contradict.
  • An optional regeneration instruction from the user
    (e.g. "redo this with the new latency NFR"). If present, prioritise
    incorporating that change.

Output ONLY a JSON object:

  {
    "title": "<preserved section title>",
    "content": [<content blocks>]
  }

Content block schema (use these types only):
  paragraph    {"type": "paragraph",    "text": "..."}
  heading      {"type": "heading", "level": 2-4, "text": "..."}
  bullet_list  {"type": "bullet_list",  "items": ["...", ...]}
  ordered_list {"type": "ordered_list", "items": ["...", ...]}
  table        {"type": "table", "headers": [...], "rows": [[...], ...]}

Rules:
  • Do not invent facts. If something is unknown, omit it or call it
    out as "(to be confirmed)" — never fabricate.
  • Preserve every measurable / numeric value from the current content.
  • If the section is already strong, return it largely unchanged
    rather than rewriting for the sake of rewriting.

CONTENT SOURCING & PROVENANCE (must match the generator's markers):
  • PRESERVE existing provenance markers. If the current content already
    tags an item "[AI-ASSUMPTION]", "[from prior session]", or
    "[Awaiting input]", keep that tag — UNLESS the regeneration request
    supplies a real fact that resolves it, in which case drop the tag.
  • If you ADD any content that is inferred (not stated in the current
    content, the known project context, or the regeneration request),
    tag it inline with "[AI-ASSUMPTION]" so the reader can distinguish
    inferred content from grounded content.
  • If you ADD content sourced ONLY from the known project context
    (carried across sessions, not in this section's current content),
    tag it "[from prior session]".
  • Content the user explicitly provided in the regeneration request, or
    already-grounded current content, needs NO marker.
  • Place the marker at the end of the affected field/sentence/cell, e.g.
    a table cell "VP of Product [AI-ASSUMPTION]" or a bullet ending
    "... within 2 weeks [AI-ASSUMPTION]". Never fabricate just to fill a
    table; prefer fewer rows or "(to be confirmed)".
"""


def _build_regenerate_prompt(
    *,
    section_number: int,
    section_title: str,
    current_content: List[Dict[str, Any]],
    known_facts: List[str],
    regen_reason: str,
    new_source_material: str = "",
) -> str:
    """Compose the user-content block for the regenerate call.

    new_source_material: text from a just-uploaded document (Fix #3). It is
    GROUNDED, user-provided fact — content folded in from here needs NO
    [AI-ASSUMPTION] marker.
    """
    facts_block = (
        "\n".join(f"  - {f}" for f in known_facts)
        if known_facts else "(no project context loaded for this session)"
    )
    source_block = ""
    if new_source_material:
        source_block = (
            "NEW SOURCE DOCUMENT (just uploaded by the user — treat as GROUNDED, "
            "authoritative fact; fold every relevant item into this section. "
            "Content sourced from here is user-provided and needs NO "
            "[AI-ASSUMPTION] marker):\n"
            f"{new_source_material}\n\n"
        )
    instruction_block = (
        f"User's regeneration request:\n  {regen_reason}\n\n"
        if regen_reason else
        "User asked for a regeneration with no specific instruction — "
        "improve the section's clarity, structure, and completeness "
        "without changing intent.\n\n"
    )
    return (
        f"Section {section_number}: {section_title}\n\n"
        f"Current content (JSON):\n"
        f"```json\n{json.dumps(current_content, indent=2)}\n```\n\n"
        f"Known project context:\n{facts_block}\n\n"
        f"{source_block}"
        f"{instruction_block}"
        f"Return regenerated section JSON."
    )


def _regenerate_section_in_memory(
    section: Dict[str, Any],
    *,
    section_number: int,
    known_facts: List[str],
    regen_reason: str,
    user_id: Optional[str],
    new_source_material: str = "",
) -> bool:
    """Regenerate ONE section's content IN PLACE (no S3 write). Shared by
    _do_regenerate_section (single write) and the doc-ingest auto-regen
    (Fix #3, batched write across multiple sections). Returns True on success.
    """
    from llm_gateway import chat_completion
    from services.brd_orchestrator_utils import extract_json

    user_content = _build_regenerate_prompt(
        section_number=section_number,
        section_title=section.get("title") or "",
        current_content=section.get("content") or [],
        known_facts=known_facts,
        regen_reason=regen_reason,
        new_source_material=new_source_material,
    )
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=cached_system_blocks(_REGENERATE_SYSTEM_PROMPT),
            model=BRD_HANDLER_MODEL,
            temperature=0.3,
            max_tokens=BRD_SECTION_MAX_TOKENS,
            user_id=user_id,
            token_source=f"lambda_brd_orchestrator:regenerate_section_{section_number}",
        )
        parsed = extract_json(raw)
    except Exception as e:
        logger.warning(f"[BRD] regen-in-memory §{section_number} LLM/parse failed: {e}")
        return False

    new_content = parsed.get("content") if isinstance(parsed, dict) else None
    if not isinstance(new_content, list):
        logger.warning(f"[BRD] regen-in-memory §{section_number}: no content array")
        return False

    _push_previous_version(section, reason="regenerate_section")
    section["content"] = new_content
    section["status"] = "llm_regenerated"
    section["last_updated_ts"] = _now_iso()
    if isinstance(parsed.get("title"), str) and parsed["title"].strip():
        section["title"] = parsed["title"].strip()
    return True


def _do_ingest_doc(
    event: Dict[str, Any], session: Dict[str, Any], router: Dict[str, Any]
) -> Dict[str, Any]:
    """User attached a doc mid-conversation. Two LLM calls:

      1. Relevance classifier (T=0.0, ~200 tokens) -- which BRD
         sections does this doc inform?
      2. Fact extraction       (T=0.3, ~800 tokens) -- pull
         stakeholders / NFRs / constraints / integrations /
         assumptions / open-questions and persist as a single
         memory event so the long-term SEMANTIC strategy folds them
         into the project's fact namespace.

    Returns a doc_ingested card. When auto_regen kwarg is true
    (used by the multi-file bypass path for ONLY the last file in a
    batch), the frontend automatically dispatches a regenerate of
    the affected sections.
    """
    file_payload = (event or {}).get("file") or {}
    return _ingest_one_doc(
        event=event,
        session=session,
        file_payload=file_payload,
        auto_regen=False,
    )


def _chunk_text(text: str, size: int, overlap: int = 1000) -> List[str]:
    """Split text into ~`size`-char chunks, preferring to break on a
    paragraph / line / word boundary near the cut so facts aren't sliced
    mid-sentence. `overlap` chars are repeated between adjacent chunks to
    keep context that straddles a boundary. No external deps."""
    if len(text) <= size:
        return [text]
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window_lo = max(start + size - 2000, start + 1)
            cut = text.rfind("\n\n", window_lo, end)
            if cut == -1:
                cut = text.rfind("\n", window_lo, end)
            if cut == -1:
                cut = text.rfind(" ", window_lo, end)
            if cut > start:
                end = cut
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _merge_fact_dicts(dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge per-chunk fact dicts: concatenate list-valued keys (order-
    preserving dedupe) and keep the first non-empty scalar per key."""
    merged: Dict[str, Any] = {}
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            if isinstance(v, list):
                bucket = merged.setdefault(k, [])
                for item in v:
                    if item not in bucket:
                        bucket.append(item)
            elif k not in merged and v:
                merged[k] = v
    return merged


def _ingest_one_doc(
    *,
    event: Dict[str, Any],
    session: Dict[str, Any],
    file_payload: Dict[str, Any],
    auto_regen: bool,
) -> Dict[str, Any]:
    """Shared body for both single-file (intent) and multi-file
    (router-bypass) doc ingest.

    Returns a doc_ingested card on success, or an error card if the
    payload is unusable.
    """
    from prompts.brd_doc_relevance_prompts import (
        DOC_RELEVANCE_SYSTEM_PROMPT,
        DOC_FACTS_SYSTEM_PROMPT,
        build_doc_relevance_prompt,
        build_doc_facts_prompt,
    )
    from services.brd_orchestrator_utils import (
        brd_structure_key,
        extract_json,
        s3_get_json_with_etag,
        write_memory_event,
    )
    from llm_gateway import chat_completion

    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id") or session.get("project_id")
    session_id = (event or {}).get("session_id") or session.get("session_id")
    filename   = (file_payload or {}).get("filename") or "(unnamed)"
    doc_text   = ((file_payload or {}).get("extracted_text") or "").strip()

    if not doc_text:
        return card(
            "doc_ingested",
            fact_id=f"doc-{uuid.uuid4().hex[:8]}",
            filename=filename,
            suggested_sections=[],
            summary="(no extractable text in this document)",
            auto_regen=False,
        )

    # Load current sections (if any) so the relevance classifier can
    # score against them. If there's no BRD yet, suggested_sections
    # will simply be empty and the frontend treats this as
    # "fact accumulated, no section pinning yet". We KEEP the structure +
    # etag (Fix #3) so we can fold the doc into the affected sections.
    available_sections: List[Dict[str, Any]] = []
    brd_structure: Optional[Dict[str, Any]] = None
    brd_etag: Optional[str] = None
    brd_id = session.get("brd_id")
    if brd_id:
        try:
            brd_structure, brd_etag = s3_get_json_with_etag(brd_structure_key(brd_id))
            if brd_structure and isinstance(brd_structure.get("sections"), list):
                available_sections = [
                    {"number": s.get("number"), "title": s.get("title") or "(untitled)"}
                    for s in brd_structure["sections"]
                    if s.get("number") is not None
                ]
            else:
                brd_structure = None
        except Exception as e:
            logger.warning(f"[BRD] ingest: structure load failed (non-fatal): {e}")

    # Relevance is a coarse "which sections does this inform" classification —
    # the first slice of a very long doc is plenty and avoids overflowing the
    # classifier. Fact extraction (below) sees the WHOLE doc via chunking.
    rel_text = doc_text if len(doc_text) <= BRD_INGEST_MAX_CHARS else doc_text[:BRD_INGEST_MAX_CHARS]

    # 1) Relevance classifier ------------------------------------------------
    suggested_sections: List[int] = []
    summary = ""
    if available_sections:
        try:
            rel_prompt = build_doc_relevance_prompt(
                filename=filename, doc_text=rel_text, available_sections=available_sections,
            )
            raw = chat_completion(
                messages=[{"role": "user", "content": rel_prompt}],
                system_prompt=cached_system_blocks(DOC_RELEVANCE_SYSTEM_PROMPT),
                model=BRD_HANDLER_MODEL,
                temperature=0.0,
                max_tokens=200,
                user_id=user_id,
                token_source="lambda_brd_orchestrator:doc_relevance",
            )
            parsed = extract_json(raw)
            if isinstance(parsed, dict):
                raw_sections = parsed.get("suggested_sections") or []
                summary = parsed.get("summary") or ""
                for n in raw_sections:
                    try:
                        n_int = int(n)
                        if any(s["number"] == n_int for s in available_sections) and n_int not in suggested_sections:
                            suggested_sections.append(n_int)
                    except (TypeError, ValueError):
                        continue
                suggested_sections = suggested_sections[:5]
        except Exception as e:
            logger.warning(f"[BRD] ingest relevance classify failed (non-fatal): {e}")

    # 2) Fact extraction -----------------------------------------------------
    # Large docs are chunked so each extraction call stays within the model
    # context (and dodges lost-in-the-middle). Each chunk is extracted, then
    # the per-chunk fact dicts are merged into one before persisting.
    facts_extracted = 0

    def _extract_facts_from(chunk_text: str, label: str) -> Dict[str, Any]:
        fact_prompt = build_doc_facts_prompt(filename=label, doc_text=chunk_text)
        raw_facts = chat_completion(
            messages=[{"role": "user", "content": fact_prompt}],
            system_prompt=cached_system_blocks(DOC_FACTS_SYSTEM_PROMPT),
            model=BRD_HANDLER_MODEL,
            temperature=0.3,
            max_tokens=800,
            user_id=user_id,
            token_source="lambda_brd_orchestrator:doc_facts",
        )
        parsed = extract_json(raw_facts)
        return parsed if isinstance(parsed, dict) else {}

    try:
        if len(doc_text) <= BRD_INGEST_MAX_CHARS:
            parsed_facts = _extract_facts_from(doc_text, filename)
        else:
            from concurrent.futures import ThreadPoolExecutor

            chunks = _chunk_text(
                doc_text, BRD_INGEST_CHUNK_CHARS, BRD_INGEST_CHUNK_OVERLAP
            )
            logger.info(
                f"[BRD] ingest: {filename} is {len(doc_text)} chars > "
                f"{BRD_INGEST_MAX_CHARS} — chunking into {len(chunks)} pieces "
                f"for fact extraction"
            )
            per_chunk: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=BRD_SECTION_PARALLELISM) as ex:
                futures = [
                    ex.submit(_extract_facts_from, ch, f"{filename} (part {i + 1}/{len(chunks)})")
                    for i, ch in enumerate(chunks)
                ]
                for fut in futures:
                    try:
                        per_chunk.append(fut.result())
                    except Exception as ce:
                        logger.warning(f"[BRD] ingest chunk extraction failed (non-fatal): {ce}")
            parsed_facts = _merge_fact_dicts(per_chunk)

        if isinstance(parsed_facts, dict):
            facts_extracted = sum(
                len(v) if isinstance(v, list) else 0
                for v in parsed_facts.values()
            )
            # Write a single ingest event into memory so the SEMANTIC
            # strategy picks it up. Payload is the structured JSON
            # the strategy's override prompt is trained against.
            if session_id and user_id and facts_extracted:
                write_memory_event(
                    session_id=session_id,
                    user_id=user_id,
                    role="OTHER",
                    content=(
                        f"[INGESTED DOC: {filename}] "
                        f"{json.dumps(parsed_facts, ensure_ascii=False)}"
                    ),
                    project_id=project_id,
                )
    except Exception as e:
        logger.warning(f"[BRD] ingest fact extraction failed (non-fatal): {e}")

    # ── Fix #3: fold the uploaded doc INTO the BRD sections it affects ────────
    # When a BRD already exists and the doc maps to sections, regenerate those
    # sections IN PLACE with the doc as GROUNDED source material, write the
    # updated structure back once, and persist the regenerated content to the
    # project's long-term memory (USER events -> SEMANTIC extraction). This is
    # what makes the card's "folding in now" promise real: the doc isn't stored
    # as a separate long-term entry — the UPDATED BRD is.
    regenerated_sections: List[int] = []
    if brd_structure is not None and suggested_sections and parsed_facts and brd_id:
        try:
            from services.brd_orchestrator_utils import (
                get_facts_scoped,
                s3_put_json_if_match,
                ConcurrentEditError,
            )
            new_source_material = (
                f"[Document: {filename}]\n"
                + json.dumps(parsed_facts, ensure_ascii=False, indent=2)
            )[:BRD_INGEST_MAX_CHARS]
            known_facts = get_facts_scoped(
                user_id=user_id, project_id=project_id, session_id=session_id,
                query=filename, use_long_term=session.get("use_long_term_context", True),
            )
            sections_by_num = {
                s.get("number"): s
                for s in brd_structure["sections"] if isinstance(s, dict)
            }
            for n in suggested_sections:
                sec = sections_by_num.get(n)
                if sec and _regenerate_section_in_memory(
                    sec, section_number=n, known_facts=known_facts,
                    regen_reason=f"Incorporate the newly uploaded document '{filename}'.",
                    user_id=user_id, new_source_material=new_source_material,
                ):
                    regenerated_sections.append(n)
            if regenerated_sections:
                try:
                    s3_put_json_if_match(brd_structure_key(brd_id), brd_structure, brd_etag)
                except ConcurrentEditError as ce:
                    logger.warning(f"[BRD] ingest auto-regen concurrent edit — write skipped: {ce}")
                    regenerated_sections = []
                else:
                    # Persist updated sections to PROJECT long-term as USER events
                    # (session-scoped actor -> SEMANTIC extraction -> retrievable).
                    for n in regenerated_sections:
                        sec = sections_by_num.get(n) or {}
                        title = sec.get("title") or f"section {n}"
                        body = _render_section_numbered(sec.get("content") or [])[:1500]
                        write_memory_event(
                            session_id=session_id, user_id=user_id, role="USER",
                            content=f"BRD §{n} {title} (updated from {filename}): {body}",
                            project_id=project_id,
                        )
                    logger.info(
                        f"[BRD] ingest auto-regen: folded {filename} into "
                        f"§{regenerated_sections} + pushed to long-term"
                    )
        except Exception as e:
            logger.warning(f"[BRD] ingest auto-regen failed (non-fatal): {e}")

    if not summary:
        summary = (
            f"Ingested {filename}: {facts_extracted} facts extracted, "
            f"{len(suggested_sections)} section(s) affected."
            if facts_extracted else
            f"Ingested {filename}: no structured facts extracted."
        )
    if regenerated_sections:
        summary = (
            f"Folded {filename} into §{', §'.join(str(n) for n in regenerated_sections)} "
            f"and saved the update to project memory."
        )

    return card(
        "doc_ingested",
        fact_id=f"doc-{uuid.uuid4().hex[:8]}",
        filename=filename,
        suggested_sections=suggested_sections,
        regenerated_sections=regenerated_sections,
        summary=summary,
        facts_extracted=facts_extracted,
        # auto_regen now reflects what ACTUALLY happened server-side (sections
        # were regenerated + written back), not a frontend-dispatch hint.
        auto_regen=bool(regenerated_sections),
    )


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
    "GENERATE_BRD":          _do_generate_brd,
    "AUDIT":                 _do_audit,
    "REGENERATE_SECTION":    _do_regenerate_section,
    "INGEST_DOC":            _do_ingest_doc,
}


def handle_generate(event: Dict[str, Any]) -> Dict[str, Any]:
    """Action-level entry for `POST /api/brd/generate`.

    Verifies session ownership, then delegates to the same unified
    generation body the intent path uses (one generator Lambda, all
    sources). Returns the same generation_starting / generation_failed
    card the chat path returns so the frontend renders both identically.
    """
    from services.brd_orchestrator_utils import verify_session_owned

    session_id = (event or {}).get("session_id")
    user_id    = (event or {}).get("user_id")
    if not session_id or not user_id:
        return card("error", code="bad_request",
                    message="session_id and user_id are required", retryable=False)

    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
    except LookupError:
        return card("error", code="session_not_found", message=session_id, retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="not your session", retryable=False)

    return _do_generate_brd(event, session, router={})


def handle_audit(event: Dict[str, Any]) -> Dict[str, Any]:
    """Action-level entry for `POST /api/brd/audit`. Verifies session
    ownership then delegates to the shared audit body. Returns an
    `audit` card (same shape the intent-level handler returns) so the
    frontend renders both paths identically.
    """
    from services.brd_orchestrator_utils import verify_session_owned

    session_id = (event or {}).get("session_id")
    user_id    = (event or {}).get("user_id")
    project_id = (event or {}).get("project_id")
    target_section = (event or {}).get("section_number")

    if not session_id or not user_id:
        return card("error", code="bad_request",
                    message="session_id and user_id are required",
                    retryable=False)

    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
    except LookupError:
        return card("error", code="session_not_found", message=session_id, retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="not your session", retryable=False)

    return _run_audit_and_persist(
        session=session,
        user_id=user_id,
        project_id=project_id or session.get("project_id"),
        target_section=target_section,
    )


# ============================================
# AUDIT — shared body. Loads brd_structure with ETag, runs one LLM call
# per section in parallel via ThreadPoolExecutor(max_workers=N), decorates
# the structure with audit results, writes back conditionally. Returns a
# ready-to-render `audit` card.
# ============================================

_AUDIT_RETRY_SUFFIX = (
    "\n\nIMPORTANT — your previous response could not be parsed. Reply "
    "with ONLY a single JSON object, no prose before or after, no "
    "markdown fences. The object MUST have exactly two keys: "
    '"score" (integer 0-100) and "issues" (array of {"code", "msg"} '
    "objects).\n"
)


def _normalize_audit_payload(parsed: Any) -> Dict[str, Any]:
    """Coerce common LLM output shapes into the canonical
    {score: int, issues: [{code, msg}]} dict. Mirrors SAD's normaliser
    so handlers downstream can trust the shape.

    Recovers from:
      • A bare list of issues   → {score: derived, issues: list}
        (score = 100 - 10 * min(len(list), 5))
      • An object that nests under "audit" / "result" / "data".
    """
    if isinstance(parsed, dict):
        for k in ("audit", "result", "data"):
            inner = parsed.get(k)
            if isinstance(inner, dict) and ("score" in inner or "issues" in inner):
                parsed = inner
                break

    if isinstance(parsed, list):
        issues = [it for it in parsed if isinstance(it, dict) and "code" in it]
        score = max(0, 100 - 10 * min(len(issues), 5))
        return {"score": score, "issues": issues[:5]}

    if isinstance(parsed, dict):
        try:
            score = int(parsed.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        return {
            "score": max(0, min(100, score)),
            "issues": parsed.get("issues") or [],
        }

    raise ValueError(f"audit returned unrecognized shape: {type(parsed).__name__}")


def _score_to_status(score: int) -> str:
    """Map a 0-100 audit score to the {OK, NEEDS_REVIEW, FAILED} badge
    vocabulary the plan defines. Threshold rationale: 90+ is "ship it",
    60-89 is "look before you ship", <60 is "fix before ship"."""
    if score >= 90:
        return "OK"
    if score >= 60:
        return "NEEDS_REVIEW"
    return "FAILED"


def _audit_one_section(
    *,
    section: Dict[str, Any],
    known_facts: List[str],
    user_id: Optional[str],
) -> Tuple[int, Dict[str, Any]]:
    """Run the audit prompt for ONE section. Returns (section_number,
    normalized_payload). One retry on parse failure, then a final
    sentinel payload so the parallel loop never explodes the whole audit.
    """
    from prompts.brd_audit_prompts import AUDIT_SYSTEM_PROMPT, build_audit_prompt
    from services.brd_orchestrator_utils import extract_json
    from llm_gateway import chat_completion

    n = section.get("number")
    prompt = build_audit_prompt(
        section_number=n,
        section_title=section.get("title") or "",
        section_content=section.get("content") or [],
        known_facts=known_facts,
    )

    last_raw = ""
    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            raw = chat_completion(
                messages=[{"role": "user", "content": prompt + (_AUDIT_RETRY_SUFFIX if attempt == 2 else "")}],
                system_prompt=AUDIT_SYSTEM_PROMPT,
                model=BRD_HANDLER_MODEL,
                temperature=0.0,
                max_tokens=BRD_AUDIT_MAX_TOKENS,
                user_id=user_id,
                token_source=f"lambda_brd_orchestrator:audit{n}",
            )
            last_raw = raw
            parsed = extract_json(raw)
            normalized = _normalize_audit_payload(parsed)
            if attempt == 2:
                logger.info(f"[BRD] audit section {n} recovered on retry")
            return n, normalized
        except Exception as e:
            last_err = e
            logger.warning(
                f"[BRD] audit section {n} attempt {attempt} failed: {e} "
                f"raw[:300]={(last_raw or '')[:300]!r}"
            )

    snippet = (last_raw or "").strip()[:300].replace("\n", " ")
    logger.error(
        f"[BRD] audit section {n} unrecoverable: {last_err} raw_snippet={snippet!r}"
    )
    return n, {
        "score": 0,
        "issues": [{
            "code": "AUDIT_PARSE_FAILED",
            "msg": f"Auditor returned malformed output after retry: {last_err}".strip(),
        }],
    }


def _run_audit_and_persist(
    *,
    session: Dict[str, Any],
    user_id: Optional[str],
    project_id: Optional[str],
    target_section: Optional[int],
) -> Dict[str, Any]:
    """Shared audit body for both action and intent entry points.

    Loads the BRD structure, runs per-section audits in parallel,
    decorates the structure with each section's audit result, writes
    back under If-Match (concurrent-edit safe), and returns an
    `audit` card with overall_score / badges / details.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from services.brd_orchestrator_utils import (
        ConcurrentEditError,
        brd_structure_key,
        get_long_term_facts,
        s3_get_json_with_etag,
        s3_put_json_if_match,
    )

    brd_id = session.get("brd_id")
    if not brd_id:
        return card("text",
                    text="There's no BRD to audit yet.",
                    kind="warning")

    try:
        structure, etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        logger.error(f"[BRD] audit S3 read failed for {brd_id}: {e}")
        return card("error", code="s3_read_failed", message=str(e), retryable=True)
    if not structure or not isinstance(structure.get("sections"), list):
        return card("error", code="malformed_brd",
                    message="BRD structure malformed", retryable=False)

    sections_all: List[Dict[str, Any]] = structure["sections"]

    # Filter to one section if the caller asked for a targeted audit.
    if target_section is not None:
        sections_to_audit = [s for s in sections_all if s.get("number") == target_section]
        if not sections_to_audit:
            return card("error", code="section_not_found",
                        message=f"Section #{target_section} not found",
                        retryable=False)
    else:
        sections_to_audit = [s for s in sections_all if s.get("number") is not None]

    # Long-term facts shape TRACEABILITY_GAP findings; only loaded when
    # the session opted in (Resolved Q#5). Cheap to skip when off.
    known_facts: List[str] = []
    if session.get("use_long_term_context", True):
        try:
            known_facts = get_long_term_facts(
                user_id=user_id,
                project_id=project_id,
                query="audit project context",
            )
        except Exception as e:
            logger.warning(f"[BRD] audit long-term facts load failed (non-fatal): {e}")

    # Parallel per-section audit — bounded by BRD_SECTION_PARALLELISM
    # to keep gateway concurrency under control. Mirrors SAD's pattern
    # at lambda_sad_orchestrator.py:1663-1671.
    results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=BRD_SECTION_PARALLELISM) as ex:
        futures = [
            ex.submit(_audit_one_section,
                      section=s, known_facts=known_facts, user_id=user_id)
            for s in sections_to_audit
        ]
        for fut in as_completed(futures):
            try:
                n, payload = fut.result()
                results[n] = payload
            except Exception as e:
                logger.error(f"[BRD] audit worker exploded: {e}")

    # Decorate structure with per-section results. Sections we didn't
    # audit this pass keep their previous audit (if any) for the
    # rendered card so partial-audit UX stays coherent.
    badges: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    score_sum = 0
    score_count = 0
    for s in sections_all:
        n = s.get("number")
        if n in results:
            r = results[n]
            s["audit"] = r
        elif "audit" in s:
            r = s["audit"]
        else:
            continue
        score = int(r.get("score", 0))
        status = _score_to_status(score)
        badges.append({
            "section_number": n,
            "title": s.get("title"),
            "score": score,
            "status": status,
        })
        for issue in r.get("issues", []) or []:
            details.append({
                "section_number": n,
                "code": issue.get("code", "UNKNOWN"),
                "message": issue.get("msg", "") or issue.get("message", ""),
                "severity": "high" if status == "FAILED" else "medium",
            })
        score_sum += score
        score_count += 1

    overall_score = (score_sum // score_count) if score_count else 0

    # Persist the decorated structure. ETag conflict → tell the user to
    # reload; we don't auto-retry because the in-memory `s` may now be
    # stale relative to whoever else wrote.
    try:
        s3_put_json_if_match(brd_structure_key(brd_id), structure, etag)
    except ConcurrentEditError as ce:
        return card("concurrent_edit",
                    section_number=target_section,
                    current_etag=ce.current_etag,
                    your_etag=ce.your_etag)
    except Exception as e:
        logger.error(f"[BRD] audit S3 write failed for {brd_id}: {e}")
        return card("error", code="s3_write_failed", message=str(e), retryable=True)

    return card(
        "audit",
        overall_score=overall_score,
        badges=badges,
        details=details,
    )


def handle_revert_section(event: Dict[str, Any]) -> Dict[str, Any]:
    """Action-level handler. Pops the last entry from a section's
    `previous_versions` stack and makes it the current content. Used
    by the frontend's Revert button on the section diff view.

    Event shape:
      session_id, user_id, project_id, section_number

    Returns a section_updated card (same shape as _do_edit_section so
    the frontend can reuse the diff renderer).
    """
    from services.brd_orchestrator_utils import (
        ConcurrentEditError,
        brd_structure_key,
        s3_get_json_with_etag,
        s3_put_json_if_match,
        verify_session_owned,
    )

    session_id     = (event or {}).get("session_id")
    user_id        = (event or {}).get("user_id")
    section_number = (event or {}).get("section_number")

    if not session_id or not user_id or section_number is None:
        return card("error", code="bad_request",
                    message="session_id, user_id, section_number required",
                    retryable=False)

    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
    except LookupError:
        return card("error", code="session_not_found", message=str(section_number), retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="not your session", retryable=False)

    brd_id = session.get("brd_id")
    if not brd_id:
        return card("error", code="no_brd", message="no BRD to revert", retryable=False)

    try:
        structure, etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        logger.error(f"[BRD] handle_revert_section S3 read failed: {e}")
        return card("error", code="s3_read_failed", message=str(e), retryable=True)

    if not structure or not isinstance(structure.get("sections"), list):
        return card("error", code="malformed_brd",
                    message="BRD structure malformed", retryable=False)

    section = _find_section(structure["sections"], section_number)
    if section is None:
        return card("error", code="section_not_found",
                    message=f"section {section_number}", retryable=False)

    prev_versions = section.get("previous_versions") or []
    if not prev_versions:
        return card("error", code="nothing_to_revert",
                    message="this section has no previous version on file",
                    retryable=False)

    # Pop the most recent previous version.
    last_version = prev_versions.pop()
    old_current = section.get("content") or []
    section["content"] = last_version.get("content") or []
    section["status"] = "user_edited"
    section["last_updated_ts"] = _now_iso()

    # The popped version's content was the previous content; the
    # CURRENT content (what we just replaced) goes... nowhere. Revert
    # is destructive on the way back — the user explicitly asked
    # "undo my last change", so we don't push another version onto
    # the stack. (Mirrors SAD's revert behaviour.)
    _ = old_current  # explicit no-op

    try:
        s3_put_json_if_match(brd_structure_key(brd_id), structure, etag)
    except ConcurrentEditError as ce:
        return card("concurrent_edit",
                    section_number=section_number,
                    current_etag=ce.current_etag,
                    your_etag=ce.your_etag)
    except Exception as e:
        logger.error(f"[BRD] handle_revert_section S3 write failed: {e}")
        return card("error", code="s3_write_failed", message=str(e), retryable=True)

    return card(
        "section_updated",
        section_number=section_number,
        title=section.get("title"),
        content_json=section["content"],
        diff_summary="(reverted to previous version)",
        previous_versions_count=len(section["previous_versions"]),
        status=section["status"],
    )


def handle_save_section(event: Dict[str, Any]) -> Dict[str, Any]:
    """Action-level handler. Direct (no LLM) section save. The user
    has typed/pasted new content into the section editor and hit Save.
    Pushes the old content onto previous_versions, writes the new
    content under If-Match etag.

    Event shape:
      session_id, user_id, project_id, section_number, content (list of blocks)
    """
    from services.brd_orchestrator_utils import (
        ConcurrentEditError,
        brd_structure_key,
        s3_get_json_with_etag,
        s3_put_json_if_match,
        verify_session_owned,
    )

    session_id     = (event or {}).get("session_id")
    user_id        = (event or {}).get("user_id")
    section_number = (event or {}).get("section_number")
    new_content    = (event or {}).get("content")

    if not session_id or not user_id or section_number is None or new_content is None:
        return card("error", code="bad_request",
                    message="session_id, user_id, section_number, content required",
                    retryable=False)
    if not isinstance(new_content, list):
        return card("error", code="bad_request",
                    message="content must be an array of blocks",
                    retryable=False)

    # Validate block shapes — same set the SAD save handler accepts.
    valid_block_types = {"paragraph", "heading", "bullet", "bullet_list",
                         "ordered_list", "table", "diagram"}
    for i, block in enumerate(new_content):
        if not isinstance(block, dict):
            return card("error", code="bad_block",
                        message=f"block[{i}] is not an object", retryable=False)
        if block.get("type") not in valid_block_types:
            return card("error", code="bad_block",
                        message=f"block[{i}] has unknown type {block.get('type')!r}",
                        retryable=False)

    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
    except LookupError:
        return card("error", code="session_not_found", message=str(section_number), retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="not your session", retryable=False)

    brd_id = session.get("brd_id")
    if not brd_id:
        return card("error", code="no_brd", message="no BRD to save into", retryable=False)

    try:
        structure, etag = s3_get_json_with_etag(brd_structure_key(brd_id))
    except Exception as e:
        return card("error", code="s3_read_failed", message=str(e), retryable=True)

    if not structure or not isinstance(structure.get("sections"), list):
        return card("error", code="malformed_brd",
                    message="BRD structure malformed", retryable=False)

    section = _find_section(structure["sections"], section_number)
    if section is None:
        return card("error", code="section_not_found",
                    message=f"section {section_number}", retryable=False)

    _push_previous_version(section, reason="save_section")
    section["content"] = new_content
    section["status"] = "user_edited"
    section["last_updated_ts"] = _now_iso()

    try:
        s3_put_json_if_match(brd_structure_key(brd_id), structure, etag)
    except ConcurrentEditError as ce:
        return card("concurrent_edit",
                    section_number=section_number,
                    current_etag=ce.current_etag,
                    your_etag=ce.your_etag)
    except Exception as e:
        return card("error", code="s3_write_failed", message=str(e), retryable=True)

    return card(
        "section_updated",
        section_number=section_number,
        title=section.get("title"),
        content_json=section["content"],
        diff_summary="(user-edited)",
        previous_versions_count=len(section["previous_versions"]),
        status=section["status"],
    )


def handle_cancel_generation(event: Dict[str, Any]) -> Dict[str, Any]:
    """Action-level entry for `POST /api/brd/cancel-generation`.

    The Lambda does NOT try to kill the in-flight worker -- AWS Lambda
    has no stop-execution API for async invokes. Instead this is a
    pure acknowledgement: the FastAPI router records the cancelled
    brd_id in the analyst_sessions row, flips stage back to prior,
    and discards any late-arriving worker result by brd_id check.

    The Lambda's job is to verify session ownership (defense in depth)
    and return the generation_cancelled card the frontend renders.
    """
    from services.brd_orchestrator_utils import verify_session_owned

    session_id = (event or {}).get("session_id")
    user_id    = (event or {}).get("user_id")
    brd_id     = (event or {}).get("brd_id")

    if not session_id or not user_id:
        return card("error", code="bad_request",
                    message="session_id and user_id are required", retryable=False)

    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
    except LookupError:
        return card("error", code="session_not_found", message=session_id, retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="not your session", retryable=False)

    return card(
        "generation_cancelled",
        brd_id=brd_id or session.get("brd_id"),
        stage_reverted_to=session.get("prior_stage") or "GATHERING",
    )


def handle_ingest_doc(event: Dict[str, Any]) -> Dict[str, Any]:
    """Action-level entry for `POST /api/brd/ingest-doc`.

    Verifies session, then delegates to _ingest_one_doc. Supports
    BOTH a single-file payload (event["file"]) and a multi-file
    payload (event["files"]) so the frontend can use this endpoint
    interchangeably with the chat-attached flow.
    """
    from services.brd_orchestrator_utils import verify_session_owned

    session_id = (event or {}).get("session_id")
    user_id    = (event or {}).get("user_id")
    if not session_id or not user_id:
        return card("error", code="bad_request",
                    message="session_id and user_id are required", retryable=False)

    try:
        session = verify_session_owned(
            session_id,
            user_id,
            session_from_event=(event or {}).get("session"),
        )
    except LookupError:
        return card("error", code="session_not_found", message=session_id, retryable=False)
    except PermissionError:
        return card("error", code="forbidden", message="not your session", retryable=False)

    files = (event or {}).get("files") or []
    if files:
        return _handle_multi_file_ingest(event, session)

    file_payload = (event or {}).get("file") or {}
    if not file_payload:
        return card("error", code="bad_request",
                    message="either `file` or `files` is required", retryable=False)
    return _ingest_one_doc(
        event=event,
        session=session,
        file_payload=file_payload,
        auto_regen=False,
    )


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
    "generate":              handle_generate,
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
