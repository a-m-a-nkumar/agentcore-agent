"""
Utilities shared by lambda_brd_orchestrator handlers.

Kept in a separate module so the orchestrator file stays focused on
dispatch + handler logic and so test code can mock these primitives
independently.

Five concern areas:

  1. S3 I/O for brd_structure.json with ETag-based conditional writes
     (concurrent-edit protection — section mutations are rejected with
     412 Precondition Failed if another tab wrote since the read).

  2. AgentCore Memory dual-actor reads/writes:
       - Writes go under f"{ACTOR_PREFIX}{user_id}" (per-user wall).
       - Reads merge results from BOTH the per-user actor AND the
         legacy shared actor so historical chats remain accessible.

  3. Long-term semantic-memory retrieval (retrieve_memory_records)
     scoped by namespace = "user-{user_id}:project-{project_id}".

  4. Session ownership re-verification — defense-in-depth security
     mitigation. FastAPI ALREADY checks ownership before calling the
     Lambda, but a second check inside the Lambda catches future
     auth-gap bugs at the FastAPI layer.

  5. JSON extraction from LLM output (handles markdown fences and
     prose-wrapped JSON the way SAD's _extract_json does).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


# ============================================================================
# Module config — read once at import (Lambda container reuses across calls)
# ============================================================================

AWS_REGION                    = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME                = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
KMS_KEY_ID                    = os.getenv("KMS_KEY_ID") or os.getenv("BRD_KMS_KEY_ID") or os.getenv("KMS_KEY_ARN")
AGENTCORE_MEMORY_ID           = os.getenv("AGENTCORE_MEMORY_ID", "")

BRD_AGENTCORE_ACTOR_PREFIX    = os.getenv("BRD_AGENTCORE_ACTOR_PREFIX", "user-")
BRD_AGENTCORE_LEGACY_ACTOR    = os.getenv("BRD_AGENTCORE_LEGACY_ACTOR", "analyst-session")
BRD_FACTS_NAMESPACE_TEMPLATE  = os.getenv(
    "BRD_FACTS_NAMESPACE_TEMPLATE", "user-{user_id}:project-{project_id}"
)
BRD_FACTS_TOP_K               = int(os.getenv("BRD_FACTS_TOP_K", "10"))


# ============================================================================
# AWS clients (lazy — reuse across invocations in the same container)
# ============================================================================

_s3_client = None
_memory_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def _memory():
    global _memory_client
    if _memory_client is None:
        _memory_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    return _memory_client


# ============================================================================
# Per-user actor + namespace formatters
# ============================================================================

def per_user_actor(user_id: str) -> str:
    """Compose the per-user actor_id for AgentCore Memory writes.
    Empty user_id is rejected — would silently land events under "user-".
    """
    if not user_id:
        raise ValueError("per_user_actor: user_id is required")
    return f"{BRD_AGENTCORE_ACTOR_PREFIX}{user_id}"


def facts_namespace(user_id: str, project_id: str) -> str:
    """Compose the per-(user, project) namespace for long-term memory."""
    if not user_id or not project_id:
        raise ValueError("facts_namespace: both user_id and project_id are required")
    return BRD_FACTS_NAMESPACE_TEMPLATE.format(user_id=user_id, project_id=project_id)


# ============================================================================
# S3 helpers with ETag for concurrent-edit protection
# ============================================================================

class ConcurrentEditError(Exception):
    """Raised when an If-Match conditional write returns 412.

    Carries the current_etag from the failed write attempt so the
    handler can surface a `concurrent_edit` card with the value the
    frontend should reload against.
    """
    def __init__(self, current_etag: str, your_etag: str, key: str):
        super().__init__(
            f"Concurrent edit on {key}: another writer changed the object "
            f"(your etag={your_etag}, remote head={current_etag})"
        )
        self.current_etag = current_etag
        self.your_etag = your_etag
        self.key = key


def s3_get_json_with_etag(
    key: str,
    bucket: str = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read a JSON object from S3 and return (data, etag).

    If the key doesn't exist, returns (None, None). Other errors raise.
    The etag must be passed back to s3_put_json_if_match() so the
    write is rejected if another writer beat us to it.
    """
    bucket = bucket or S3_BUCKET_NAME
    try:
        obj = _s3().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None, None
        raise

    etag = obj.get("ETag", "").strip('"')
    body = obj["Body"].read().decode("utf-8")
    return json.loads(body), etag


def s3_put_json_if_match(
    key: str,
    data: Dict[str, Any],
    if_match_etag: Optional[str],
    bucket: str = None,
) -> str:
    """Write JSON back to S3 with optional If-Match conditional write.

    When if_match_etag is provided and the remote object's etag has
    changed since the read, S3 returns 412 — we translate that to
    ConcurrentEditError so the handler can emit a concurrent_edit card.

    When if_match_etag is None (initial create), the write is
    unconditional. Caller responsibility: pass None ONLY for fresh
    creates, never for mutations.

    Returns the new etag of the just-written object.
    """
    bucket = bucket or S3_BUCKET_NAME
    if not KMS_KEY_ID:
        raise RuntimeError(
            "KMS_KEY_ID env var not set — BRD bucket policy denies non-KMS puts."
        )

    body = json.dumps(data, indent=2).encode("utf-8")
    put_args: Dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": "application/json",
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": KMS_KEY_ID,
    }
    if if_match_etag is not None:
        # boto3 accepts the etag verbatim (with or without quotes) — S3
        # canonicalises it before comparing. We pass without quotes so
        # downstream tools that compare against the value we stash on
        # the wire don't get confused.
        put_args["IfMatch"] = if_match_etag

    try:
        resp = _s3().put_object(**put_args)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "PreconditionFailed":
            # Pull the current etag for the concurrent_edit card.
            current_etag = "<unknown>"
            try:
                head = _s3().head_object(Bucket=bucket, Key=key)
                current_etag = head.get("ETag", "").strip('"')
            except Exception:
                pass
            raise ConcurrentEditError(
                current_etag=current_etag,
                your_etag=if_match_etag or "",
                key=key,
            ) from e
        raise

    new_etag = resp.get("ETag", "").strip('"')
    return new_etag


def brd_structure_key(brd_id: str) -> str:
    """The canonical S3 key for a BRD's structure JSON. Single source
    of truth for the path so handlers don't drift."""
    if not brd_id:
        raise ValueError("brd_structure_key: brd_id is required")
    return f"brds/{brd_id}/brd_structure.json"


# ============================================================================
# AgentCore Memory — dual-actor I/O for short-term chat history
# ============================================================================

def write_memory_event(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
) -> None:
    """Append a single user/assistant event to AgentCore Memory under
    the PER-USER actor.

    Failures are logged and swallowed — memory is best-effort context,
    not a primary data store. A failed write should not crash a chat
    turn for the user.
    """
    if not AGENTCORE_MEMORY_ID:
        logger.warning("[brd_utils] AGENTCORE_MEMORY_ID unset; skipping memory write")
        return
    if not session_id or not user_id:
        logger.warning("[brd_utils] write_memory_event: session_id + user_id required")
        return

    role_upper = role.upper()
    if role_upper not in ("USER", "ASSISTANT", "TOOL", "OTHER"):
        role_upper = "ASSISTANT"

    try:
        _memory().create_event(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=per_user_actor(user_id),
            eventTimestamp=int(time.time()),
            payload=[{"conversational": {"role": role_upper, "content": {"text": content}}}],
        )
    except Exception as e:
        logger.warning(f"[brd_utils] write_memory_event failed session={session_id}: {e}")


def read_memory_history(
    session_id: str,
    user_id: str,
    max_messages: int = 30,
) -> List[Dict[str, str]]:
    """Read short-term chat history for a session, MERGING events from
    the per-user actor and the legacy shared actor.

    Merged order: by event timestamp (ascending). Duplicate-timestamp
    events keep stable order based on (actor, original list order).

    Returns a list of {role, content} dicts, oldest first, capped at
    `max_messages` total. The cap is applied AFTER merging so users
    don't see a fragmented view when one actor has more events than
    the other.
    """
    if not AGENTCORE_MEMORY_ID or not session_id:
        return []

    actors_to_query = [per_user_actor(user_id), BRD_AGENTCORE_LEGACY_ACTOR] if user_id \
        else [BRD_AGENTCORE_LEGACY_ACTOR]

    merged: List[Tuple[int, str, Dict[str, str]]] = []
    # Each tuple: (event_ts, actor_priority, message). Sorted ascending
    # by ts; actor_priority breaks ties so the order across calls is
    # deterministic.
    for priority, actor_id in enumerate(actors_to_query):
        try:
            resp = _memory().list_events(
                memoryId=AGENTCORE_MEMORY_ID,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=max_messages,
            )
        except Exception as e:
            logger.warning(
                f"[brd_utils] read_memory_history actor={actor_id} session={session_id}: {e}"
            )
            continue

        for ev in resp.get("events", []) or []:
            # boto3 returns eventTimestamp as datetime.datetime (NOT an int).
            # Convert to epoch ms for stable cross-actor ordering.
            raw_ts = ev.get("eventTimestamp")
            if raw_ts is None:
                ts = 0
            elif hasattr(raw_ts, "timestamp"):
                ts = int(raw_ts.timestamp() * 1000)
            else:
                try:
                    ts = int(raw_ts)
                except (TypeError, ValueError):
                    ts = 0
            for item in ev.get("payload", []) or []:
                conv = item.get("conversational")
                if not conv:
                    continue
                text = (conv.get("content") or {}).get("text") or ""
                role = (conv.get("role") or "ASSISTANT").lower()
                if text:
                    merged.append((ts, str(priority), {"role": role, "content": text}))

    merged.sort(key=lambda t: (t[0], t[1]))
    return [m[2] for m in merged[-max_messages:]]


# ============================================================================
# Long-term semantic memory — retrieve_memory_records
# ============================================================================

def get_long_term_facts(
    user_id: str,
    project_id: str,
    query: str,
    top_k: int = None,
) -> List[str]:
    """Retrieve top-K relevant long-term facts for this (user, project).

    Used by handlers to seed prompts with "KNOWN PROJECT CONTEXT".
    Returns a flat list of fact strings (formatted, not raw JSON) so
    callers can render them directly in prompts.

    Failures are logged and return [] — long-term context is enrichment,
    not a hard dependency, so a failed retrieval should NOT crash the
    user's turn. They just lose the "Mary remembers" boost.
    """
    if not AGENTCORE_MEMORY_ID:
        return []
    if not user_id or not project_id:
        return []

    # boto3 enforces searchQuery min length 1. Callers (e.g. the
    # context-preview endpoint that prefetches facts before a session
    # exists) sometimes pass query="" — in that case there's no semantic
    # signal to search on, so just return [] without burning an API call.
    safe_query = (query or "").strip()
    if not safe_query:
        return []

    namespace = facts_namespace(user_id, project_id)
    k = top_k or BRD_FACTS_TOP_K

    # boto3 bedrock-agentcore parameter shape: searchCriteria (structured
    # object containing the actual searchQuery) + maxResults. Older drafts
    # of this code used `searchQuery=query, topK=k` which the current SDK
    # rejects with ParamValidationError — surfaced as a noisy warning AND
    # silently returned [] every call, so long-term memory enrichment was
    # quietly disabled in production.
    try:
        resp = _memory().retrieve_memory_records(
            memoryId=AGENTCORE_MEMORY_ID,
            namespace=namespace,
            searchCriteria={"searchQuery": safe_query},
            maxResults=k,
        )
    except Exception as e:
        logger.warning(
            f"[brd_utils] get_long_term_facts namespace={namespace}: {e}"
        )
        return []

    facts: List[str] = []
    for rec in resp.get("memoryRecords", []) or []:
        # Records arrive as either a raw text content or a structured
        # JSON object with our 6-category schema. Format both shapes
        # into a single sentence per fact.
        content = rec.get("content") or rec.get("text") or ""
        if isinstance(content, dict):
            facts.extend(_format_structured_fact(content))
        elif isinstance(content, str):
            if content.strip():
                facts.append(content.strip())
    return facts[:k]


def _format_structured_fact(obj: Dict[str, Any]) -> List[str]:
    """Convert one structured-fact JSON object to one or more human-
    readable lines. Schema mirrors prompts/brd_facts_extraction_prompt.
    """
    out: List[str] = []
    for cat, entries in (obj or {}).items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            if cat == "stakeholders":
                name = (e.get("name") or "").strip()
                role = (e.get("role") or "").strip()
                team = (e.get("team") or "").strip()
                if not name:
                    continue
                extras = ", ".join(x for x in (role, team) if x)
                if extras:
                    out.append(f"stakeholder: {name} ({extras})")
                else:
                    out.append(f"stakeholder: {name}")
            elif cat == "non_functional_reqs":
                out.append(f"NFR/{e.get('category', '?')}: {e.get('value', '')}")
            elif cat == "constraints":
                out.append(f"constraint/{e.get('type', '?')}: {e.get('value', '')}")
            elif cat == "integrations":
                out.append(f"integration: {e.get('system', '?')} — {e.get('interaction', '')}")
            elif cat == "assumptions":
                out.append(f"assumption: {e.get('statement', '')}")
            elif cat == "open_questions":
                q = e.get("question", "")
                blocks = e.get("blocks_section", "")
                out.append(f"open question: {q}" + (f" (blocks: {blocks})" if blocks else ""))
    return [line for line in out if line.strip()]


# ============================================================================
# Session ownership re-verification (defense in depth)
# ============================================================================

def verify_session_owned(
    session_id: str,
    user_id: str,
    *,
    session_from_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Lambda-side re-verification that this user owns this session.

    Two modes:
      1. **Trusted-payload mode** (preferred): the FastAPI router fetches
         the session row, validates ownership, and passes the row to the
         Lambda in `event["session"]`. The Lambda re-checks the user_id
         match as a sanity assertion. No DB connection needed — keeps the
         Lambda out of the VPC, eliminates cold-start ENI penalty.

      2. **DB-fallback mode**: when no session is on the event (legacy
         payload, audit invocation, etc.), import db_helper and read the
         row directly. Requires the Lambda to be attached to the VPC and
         have RDS reachability.

    Returns the session dict on success. Raises PermissionError if the
    session exists but is owned by a different user. Raises LookupError
    if the session doesn't exist.
    """
    if session_from_event is not None:
        # Trusted-payload mode. The FastAPI router did the auth; we just
        # double-check the user_id matches so a future router bug can't
        # silently let a wrong-user payload through.
        if session_from_event.get("user_id") != user_id:
            raise PermissionError(
                f"session payload owned by {session_from_event.get('user_id')!r}, "
                f"not {user_id!r}"
            )
        return session_from_event

    # Fallback: read from DB. Requires VPC reachability.
    from db_helper import get_brd_session  # lazy import

    sess = get_brd_session(session_id)
    if not sess:
        raise LookupError(f"session not found: {session_id}")
    if sess.get("user_id") != user_id:
        raise PermissionError(
            f"session {session_id} owned by {sess.get('user_id')!r}, not {user_id!r}"
        )
    return sess


# ============================================================================
# JSON extraction from LLM output (router & handler responses)
# ============================================================================

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Best-effort JSON parse of an LLM response.

    Tries in order:
      1. Plain JSON (the well-behaved case the prompts ask for).
      2. Markdown-fenced JSON (```json ... ``` or ``` ... ```).
      3. Prose-wrapped JSON ("Here's the audit: { ... }. Cheers.")
         via greedy outer-brace match.

    Raises json.JSONDecodeError if none of the above produce valid JSON.
    """
    if not text:
        raise json.JSONDecodeError("empty response", "", 0)
    s = text.strip()

    # 1. Plain
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 2. Fenced
    m = _FENCED_JSON_RE.search(s)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Greedy outer-brace match
    m = _JSON_OBJ_RE.search(s)
    if m:
        return json.loads(m.group(0))

    raise json.JSONDecodeError("no JSON object found in LLM output", s[:200], 0)
