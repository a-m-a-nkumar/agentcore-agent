import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import boto3
# Environment-specific LLM and S3 (local: direct Bedrock + plain S3 | VDI: Gateway + KMS S3)
from environment import (
    chat_completion,
    s3_put_object,
    DEFAULT_AGENTCORE_MEMORY_ID,
    DEFAULT_AGENTCORE_ACTOR_ID,
)

# Phase 6 imports — cacheable per-section prompts. The single source of
# truth for the 16-section structure lives in brd_section_definitions.
from prompts.brd_section_definitions import (
    BRD_SECTIONS,
    SECTION_FORMATS,
    SECTION_RAG_QUERIES,
    section_title as _section_title,
)
from prompts.brd_section_prompts import (
    build_cached_system_blocks,
    build_section_user_message,
)

# Import prompt templates from separate module
from prompts.brd_generator_prompts import get_full_brd_generation_prompt, PromptConfig

# Configure logging for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_REGION = os.environ.get("BEDROCK_REGION") or os.environ["AWS_REGION"]
BEDROCK_GUARDRAIL_ARN = os.getenv("BEDROCK_GUARDRAIL_ARN", "")
BEDROCK_GUARDRAIL_VERSION = os.getenv("BEDROCK_GUARDRAIL_VERSION", "1")
MAX_TOKENS = int(os.environ["BEDROCK_MAX_TOKENS"])
TEMPERATURE = float(os.environ["BEDROCK_TEMPERATURE"])

# Parallel section generation tunables (mirror SAD's pattern). Env-tunable
# so prod and dev can bound gateway concurrency independently. Per-section
# token budget is tighter than the monolithic path's BEDROCK_MAX_TOKENS
# because each call only produces one section's content.
# Concurrency lowered 5→3: the DLX gateway returns 502 "Unexpected error
# calling model" under burst load (verified — a single section call succeeds
# but 5 concurrent + the embedding burst overwhelms it). 3 eases pressure;
# the per-section backoff retry below handles the residual transient 502s.
BRD_SECTION_PARALLELISM    = int(os.getenv("BRD_SECTION_PARALLELISM", "3"))
BRD_SECTION_MAX_TOKENS     = int(os.getenv("BRD_SECTION_MAX_TOKENS", "4000"))
BRD_SECTION_TEMPERATURE    = float(os.getenv("BRD_SECTION_TEMPERATURE", "0.3"))
# Per-section attempts. Transient gateway errors (502/429/5xx/timeout) get an
# exponential backoff between attempts so the gateway can recover; parse/format
# errors get an immediate "JSON-only" nudge retry instead.
BRD_SECTION_MAX_ATTEMPTS   = int(os.getenv("BRD_SECTION_MAX_ATTEMPTS", "4"))

# Phase 1 RAG: per-section retrieval so each section call gets only its
# relevant chunks instead of the whole (possibly 450K-token) corpus — the
# fix for the gateway 502s on large transcripts. Ephemeral + isolated: the
# index lives in this Lambda's memory for one generation, never the shared
# pgvector store.
BRD_USE_RAG_CONTEXT        = os.getenv("BRD_USE_RAG_CONTEXT", "true").strip().lower() in ("1", "true", "yes")
BRD_RAG_TOP_K              = int(os.getenv("BRD_RAG_TOP_K", "10"))
# Gap-heavy sections (stakeholders, ROI, assumptions, KPIs, timeline, risks,
# glossary/dependencies) get more chunks because the coverage audit showed
# embedding-similarity retrieval undercounts these. Surface facts (dates,
# names, vendors, metrics) flow through the facts-ledger path, not RAG.
BRD_RAG_TOP_K_HIGH         = int(os.getenv("BRD_RAG_TOP_K_HIGH", "15"))
HIGH_K_SECTIONS: set = {4, 6, 10, 12, 13, 14, 16}
# Below this corpus size, skip chunk/embed and inline the source material as
# before — no point indexing a small transcript (and it keeps tiny inputs fast).
# Default ~120k chars ≈ 30k tokens (~4 chars/tok): inputs above this fire
# per-section RAG so each section call stays narrow; smaller inputs are
# inlined into the cached prefix and reused across all 16 section calls.
# Both paths use the parallel 16-section worker fan-out — the difference is
# only what each worker sees in its user message (RAG chunks vs. the full
# inlined transcript in the cached prefix).
BRD_RAG_MIN_CHARS          = int(os.getenv("BRD_RAG_MIN_CHARS", "120000"))
# Facts-ledger extraction (RAG path only): regex pass always runs; spaCy NER
# (en_core_web_sm, bundled in the Lambda zip) runs when BRD_USE_FACT_EXTRACTION
# is true to catch PERSON/ORG/DATE entities the regex layer misses. Routed to
# gap-heavy sections to compensate for embedding-similarity's blind spot on
# surface facts (dates, names, vendors, metrics, status assertions).
BRD_USE_FACTS_LEDGER       = os.getenv("BRD_USE_FACTS_LEDGER", "true").strip().lower() in ("1", "true", "yes")
BRD_USE_FACT_EXTRACTION    = os.getenv("BRD_USE_FACT_EXTRACTION", "true").strip().lower() in ("1", "true", "yes")


# Deluxe BRD template section list — kept in lock-step with
# prompts/requirements_gathering_prompts.py "BRD COVERAGE REFERENCE".
# Stays a module-level constant so the parallel path doesn't need to
# re-derive titles from the template text on every invocation.
BRD_SECTION_TITLES: List[Tuple[int, str]] = [
    (1,  "Document Overview"),
    (2,  "Purpose"),
    (3,  "Background / Context"),
    (4,  "Stakeholders"),
    (5,  "Scope"),
    (6,  "Business Objectives & ROI"),
    (7,  "Functional Requirements"),
    (8,  "Non-Functional Requirements"),
    (9,  "User Stories / Use Cases"),
    (10, "Assumptions"),
    (11, "Constraints"),
    (12, "Acceptance Criteria / KPIs"),
    (13, "Timeline / Milestones"),
    (14, "Risks and Dependencies"),
    (15, "Approval & Review"),
    (16, "Glossary & Appendix"),
]

# ============================================================================
# Unified context-bundle sources.
#
# The single generation path accepts content from three optional sources,
# any combination of which feeds one cached context bundle:
#   - transcript      (uploaded doc, via inline text or S3 key)
#   - chat history    (the gathering conversation, via chat_session_id)
#   - existing BRD     (for regeneration, via existing_brd_id — each
#                       section's current content is passed to its worker
#                       as a "current draft" so regen merges, not overwrites)
#   - long-term facts (resolved by the orchestrator, passed inline)
#
# Chat history is fetched here (not passed inline) so a long gathering
# conversation never bloats the 1MB async-invoke payload. The memory ID
# comes from environment.DEFAULT_AGENTCORE_MEMORY_ID (the same source the
# legacy from-history Lambda used) so this works without an extra env var.
# ============================================================================

AGENTCORE_MEMORY_ID = DEFAULT_AGENTCORE_MEMORY_ID
AGENTCORE_ACTOR_ID = DEFAULT_AGENTCORE_ACTOR_ID

_memory_client = None


def _get_memory_client():
    global _memory_client
    if _memory_client is None:
        _memory_client = boto3.client("bedrock-agentcore", region_name=BEDROCK_REGION)
    return _memory_client


def get_conversation_history(
    session_id: str,
    max_messages: int = 99,
    user_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Read chat history from AgentCore Memory using the DUAL-ACTOR read
    pattern (per-user actor + legacy 'analyst-session' actor).

    The orchestrator writes USER/ASSISTANT events under `user-{user_id}`;
    older sessions have events under the legacy shared actor. Reading only
    one actor misses events written under the other. Returns events from
    both actors merged by eventTimestamp ascending.
    """
    if not AGENTCORE_MEMORY_ID or not session_id:
        return []

    client = _get_memory_client()

    actors: List[str] = []
    if user_id:
        actor_prefix = os.getenv("BRD_AGENTCORE_ACTOR_PREFIX", "user-")
        actors.append(f"{actor_prefix}{user_id}")
    legacy_actor = os.getenv("BRD_AGENTCORE_LEGACY_ACTOR", AGENTCORE_ACTOR_ID)
    if legacy_actor not in actors:
        actors.append(legacy_actor)

    logger.info(
        f"[BRD-gen] fetching chat history session={session_id} "
        f"actors={actors} max_messages={max_messages}"
    )

    merged: List[Tuple[int, int, Dict[str, str]]] = []
    for priority, actor in enumerate(actors):
        try:
            response = client.list_events(
                memoryId=AGENTCORE_MEMORY_ID,
                sessionId=session_id,
                actorId=actor,
                includePayloads=True,
                maxResults=min(max_messages, 99),
            )
        except Exception as e:
            logger.warning(f"[BRD-gen] list_events failed actor={actor} session={session_id}: {e}")
            continue

        for event in response.get("events", []):
            ts_raw = event.get("eventTimestamp")
            if ts_raw is None:
                ts_ms = 0
            elif hasattr(ts_raw, "timestamp"):
                ts_ms = int(ts_raw.timestamp() * 1000)
            else:
                try:
                    ts_ms = int(ts_raw)
                except (TypeError, ValueError):
                    ts_ms = 0
            for payload_item in event.get("payload", []):
                conv_data = payload_item.get("conversational")
                if not conv_data:
                    continue
                text_content = conv_data.get("content", {}).get("text")
                if not text_content:
                    continue
                role = conv_data.get("role", "assistant").lower()
                merged.append((ts_ms, priority, {"role": role, "content": text_content}))

    merged.sort(key=lambda t: (t[0], t[1]))
    messages = [m[2] for m in merged[-max_messages:]]
    logger.info(f"[BRD-gen] retrieved {len(messages)} chat messages (merged from {len(actors)} actors)")
    return messages


def format_conversation(messages: List[Dict[str, str]]) -> str:
    """Format chat history as readable transcript text."""
    lines = []
    for msg in messages:
        role = "USER" if msg.get("role") == "user" else "ANALYST"
        lines.append(f"{role}: {msg.get('content', '')}")
    return "\n\n".join(lines)


def _read_existing_brd_sections(existing_brd_id: str) -> Dict[int, List[Dict[str, Any]]]:
    """Read brds/{existing_brd_id}/brd_structure.json and return a map of
    {section_number: content_blocks} for regeneration merge. Each section
    worker receives its current draft so regen carries forward prior
    content (incl. user edits) instead of overwriting it.

    Best-effort: failures return {} (regen behaves like a fresh generate).
    """
    if not existing_brd_id:
        return {}
    key = f"brds/{existing_brd_id}/brd_structure.json"
    bucket = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
    try:
        s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"[BRD-gen] could not read existing BRD {existing_brd_id} for regen-merge: {e}")
        return {}

    out: Dict[int, List[Dict[str, Any]]] = {}
    for sec in data.get("sections", []) or []:
        n = sec.get("number") or sec.get("section_number")
        content = sec.get("content")
        if isinstance(n, int) and isinstance(content, list) and content:
            out[n] = content
    logger.info(f"[BRD-gen] loaded {len(out)} existing sections from {existing_brd_id} for regen-merge")
    return out


# ============================================================================
# Parallel section generation — opt-in path (event["parallel"] == True).
# Mirrors lambda_sad_orchestrator.py:1526 ThreadPoolExecutor pattern.
#
# Drops the monolithic single-LLM call (cap ~8K output tokens, ~90s) in
# favour of 16 per-section workers (~4K tokens each, max_workers=
# BRD_SECTION_PARALLELISM, ~30-40s wall clock). The trade-off is that
# each worker only sees its own section's slice of the template — for the
# Deluxe BRD template that's fine because each section is self-contained,
# and the full transcript still feeds every worker so cross-section
# coherence comes from shared input grounding rather than serial output.
#
# Output shape matches what brd_structure.json readers expect:
#   {"sections": [{"number": N, "title": "...", "content": [<blocks>],
#                  "status": "llm_generated", "last_updated_ts": "..."}]}
# ============================================================================

_TRANSIENT_ERROR_MARKERS = (
    "502", "503", "500", "429", "bad gateway", "service unavailable",
    "unexpected error calling model", "internal server error", "timeout",
    "timed out", "throttl", "too many requests", "connection reset",
    "connection aborted", "rate limit",
)


def _is_transient_gateway_error(exc: Exception) -> bool:
    """True if the exception looks like a retry-worthy transient gateway/model
    error (502/429/5xx/timeout/throttle) rather than a parse/validation
    failure. Transient errors get an exponential-backoff retry; format errors
    get the immediate 'JSON-only' nudge instead."""
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_ERROR_MARKERS)


def _generate_one_section(
    *,
    section_number: int,
    system_blocks: List[Dict[str, Any]],
    user_id: Optional[str],
    brd_id: str,
    gen_start_ts: float,
    current_draft: Optional[List[Dict[str, Any]]] = None,
    rag_chunks: Optional[List[Dict[str, Any]]] = None,
    facts_ledger: Optional[List[str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Phase 6 section worker — cached-prefix flow.

    Builds the SHORT per-section user message via SECTION_PROMPT_BUILDERS,
    fires one chat_completion using the pre-built cached system blocks,
    validates the result against SECTION_FORMATS, and writes
    brds/{brd_id}/sections/{n}.partial.json on success (the SSE endpoint
    polls this prefix to surface per-section progress to the user).

    Returns (section_number, section_dict). The section_dict carries a
    `_usage` field (stripped before final brd_structure.json) so the
    fan-out caller can aggregate token totals and cost. One retry on
    parse/format failure with an explicit "JSON-only, no markdown"
    prefill; after two failures returns status="generation_failed".

    gen_start_ts: wall-clock timestamp the generation started. Used so
    per-section log lines carry a "+Ns" offset proving parallelism in
    CloudWatch.
    """
    title = _section_title(section_number)
    # Per-section RAG: when chunks were retrieved for this section, they ride
    # in the user message (not the cached prefix) so each call stays small.
    # Facts ledger: surface facts (dates, names, vendors, metrics) routed to
    # this section by the deterministic extractor — additive to RAG, used to
    # fill the gaps the audit identified in embedding-similarity retrieval.
    section_ctx: Dict[str, Any] = {}
    if rag_chunks:
        section_ctx["rag_chunks"] = rag_chunks
    if facts_ledger:
        section_ctx["facts_ledger"] = facts_ledger
    user_msg = build_section_user_message(section_number, section_ctx or None)

    # Regeneration: hand the worker its current draft so it merges (carries
    # forward prior content + user edits) instead of overwriting. The
    # merge RULES live in SHARED_SYSTEM_PROMPT; this just supplies the draft.
    if current_draft:
        user_msg += (
            "\n\nCurrent draft of this section (regeneration — MERGE, do not "
            "overwrite; carry forward everything below unless the source "
            "input directly contradicts it):\n"
            + json.dumps(current_draft, ensure_ascii=False)
        )
    final_status = "llm_regenerated" if current_draft else "llm_generated"

    # Log start of this section relative to generation start. CloudWatch
    # shows multiple workers logging START within ~100ms of each other
    # when fan-out is working; serial execution would show ~5-10s gaps.
    section_start = time.time()
    logger.info(
        f"[BRD-gen] §{section_number:>2} START @ +{section_start - gen_start_ts:0.2f}s "
        f"(parallel worker)"
    )

    retry_suffix = (
        "\n\nIMPORTANT — your previous response could not be parsed or "
        "did not match the required block schema. Reply with ONLY a JSON "
        "array of content blocks. No prose before or after. No markdown "
        "fences (no ```json … ```). The first character of your response "
        "must be `[`.\n"
    )

    last_raw = ""
    last_err: Optional[Exception] = None
    last_usage: Dict[str, int] = {}
    base_delay = 2.0
    nudge_next = False  # set after a parse/format failure → JSON-only nudge
    for attempt in range(1, BRD_SECTION_MAX_ATTEMPTS + 1):
        try:
            res = chat_completion(
                messages=[{
                    "role": "user",
                    "content": user_msg + (retry_suffix if nudge_next else ""),
                }],
                system_prompt=system_blocks,
                temperature=BRD_SECTION_TEMPERATURE,
                max_tokens=BRD_SECTION_MAX_TOKENS,
                return_metadata=True,
                user_id=user_id,
                token_source=f"lambda_brd_generator:section{section_number}",
            )
            raw = res["content"] if isinstance(res, dict) else res
            last_raw = raw or ""
            usage = (res.get("usage") if isinstance(res, dict) else None) or {}
            last_usage = {
                "prompt":      int(usage.get("prompt_tokens", 0) or 0),
                "completion":  int(usage.get("completion_tokens", 0) or 0),
                "cache_write": int(usage.get("cache_creation_input_tokens", 0) or 0),
                "cache_read":  int(usage.get("cache_read_input_tokens", 0) or 0),
            }
            content = _extract_section_blocks(last_raw)
            _validate_section_against_format(section_number, content)

            section_end = time.time()
            logger.info(
                f"[BRD-gen] §{section_number:>2} DONE  @ +{section_end - gen_start_ts:0.2f}s "
                f"(took {section_end - section_start:0.2f}s, attempt {attempt}) "
                f"tokens prompt={last_usage['prompt']} completion={last_usage['completion']} "
                f"cache_write={last_usage['cache_write']} cache_read={last_usage['cache_read']}"
            )

            section_dict = {
                "number": section_number,
                "title": title,
                "content": content,
                "status": final_status,
                "last_updated_ts": int(time.time()),
                "previous_versions": [],
                "_usage": last_usage,        # stripped before final write
                "_duration_s": round(section_end - section_start, 2),
            }
            _write_section_partial(brd_id, section_number, section_dict)
            return section_number, section_dict
        except Exception as e:
            last_err = e
            transient = _is_transient_gateway_error(e)
            # Transient gateway errors (502/429/5xx) are NOT a JSON problem —
            # back off and retry the same request. Parse/format errors get the
            # immediate JSON-only nudge next attempt.
            nudge_next = not transient
            logger.warning(
                f"[BRD-gen] §{section_number} attempt {attempt}/{BRD_SECTION_MAX_ATTEMPTS} "
                f"failed ({'transient' if transient else 'format'}): {e} "
                f"raw[:200]={(last_raw or '')[:200]!r}"
            )
            if attempt < BRD_SECTION_MAX_ATTEMPTS and transient:
                # Exponential backoff + jitter so concurrent workers don't all
                # retry in lockstep and re-overwhelm the gateway.
                import random
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
                logger.info(f"[BRD-gen] §{section_number} transient backoff {delay:.1f}s before retry")
                time.sleep(delay)

    section_end = time.time()
    logger.error(
        f"[BRD-gen] §{section_number:>2} FAIL  @ +{section_end - gen_start_ts:0.2f}s "
        f"after {BRD_SECTION_MAX_ATTEMPTS} attempts: {last_err}"
    )
    failed = {
        "number": section_number,
        "title": title,
        "content": [{
            "type": "paragraph",
            "text": f"[Generation failed for this section: {last_err}. "
                    f"Click Regenerate to retry.]",
        }],
        "status": "generation_failed",
        "last_updated_ts": int(time.time()),
        "previous_versions": [],
        "error": str(last_err),
        "_usage": last_usage,
        "_duration_s": round(section_end - section_start, 2),
    }
    # Write the failed partial too so the SSE endpoint can surface it
    # — the final-assembly step decides whether to abort the whole BRD.
    _write_section_partial(brd_id, section_number, failed)
    return section_number, failed


def _extract_section_blocks(text: str) -> List[Dict[str, Any]]:
    """Pull a JSON array of content blocks out of an LLM response.

    Sonnet tends to wrap output in ```json …``` fences even when told
    not to. Handle that path before falling back to a permissive search.
    Returns the parsed list; raises ValueError if no array can be parsed.
    """
    import re
    if not text:
        raise ValueError("empty section response")
    s = text.strip()
    # 1. Plain JSON array.
    if s.startswith("["):
        return json.loads(s)
    # 2. Fenced ```json … ``` or ``` … ```.
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # 3. First [...] in the text.
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"no JSON array found in section response (first 200 chars: {s[:200]!r})")


def _validate_section_against_format(section_number: int, content: List[Dict[str, Any]]) -> None:
    """Assert the section's content blocks match SECTION_FORMATS[n].

    Raises ValueError on mismatch with a precise diagnostic. The worker
    treats this like any other parse failure — one retry, then bail.
    """
    if not isinstance(content, list) or not content:
        raise ValueError("content must be a non-empty list of blocks")
    spec = SECTION_FORMATS.get(section_number)
    if not spec:
        raise ValueError(f"no SECTION_FORMATS entry for §{section_number}")
    kind = spec.get("type")

    def _block_types() -> List[str]:
        return [b.get("type") for b in content if isinstance(b, dict)]

    types = _block_types()

    if kind == "table":
        tables = [b for b in content if isinstance(b, dict) and b.get("type") == "table"]
        if len(tables) != 1:
            raise ValueError(f"§{section_number} expects exactly one table block; got types={types}")
        tbl = tables[0]
        expected_headers = spec.get("headers") or []
        actual_headers = tbl.get("headers") or []
        if actual_headers != expected_headers:
            raise ValueError(
                f"§{section_number} table headers mismatch: "
                f"expected {expected_headers}, got {actual_headers}"
            )
        rows = tbl.get("rows") or []
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"§{section_number} table must have at least one row")
        n_cols = len(expected_headers)
        for i, r in enumerate(rows):
            if not isinstance(r, list) or len(r) != n_cols:
                raise ValueError(
                    f"§{section_number} table row {i} has {len(r) if isinstance(r, list) else 'N/A'} "
                    f"cells; expected {n_cols}"
                )

    elif kind == "prose":
        paras = [b for b in content if isinstance(b, dict) and b.get("type") == "paragraph"]
        if not paras:
            raise ValueError(f"§{section_number} (prose) requires at least one paragraph; got types={types}")
        # Reject embedded tables/bullets to keep the prose shape clean.
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("table", "bullet_list"):
                raise ValueError(f"§{section_number} (prose) must not contain {b['type']} blocks")

    elif kind == "bullet_list":
        bullets = [b for b in content if isinstance(b, dict) and b.get("type") == "bullet_list"]
        if len(bullets) != 1:
            raise ValueError(f"§{section_number} expects exactly one bullet_list; got types={types}")
        items = bullets[0].get("items") or []
        if not isinstance(items, list) or not items:
            raise ValueError(f"§{section_number} bullet_list must have at least one item")

    elif kind == "subsection_bullets":
        subs = spec.get("subsections") or []
        # Expect at least one heading + one bullet_list per subsection.
        headings = [b for b in content if isinstance(b, dict) and b.get("type") == "heading"]
        bullets = [b for b in content if isinstance(b, dict) and b.get("type") == "bullet_list"]
        if len(headings) < len(subs) or len(bullets) < len(subs):
            raise ValueError(
                f"§{section_number} expects {len(subs)} heading/bullet_list pairs; "
                f"got {len(headings)} headings and {len(bullets)} bullet_lists"
            )

    elif kind == "glossary":
        bullets = [b for b in content if isinstance(b, dict) and b.get("type") == "bullet_list"]
        if len(bullets) != 1:
            raise ValueError(f"§{section_number} glossary expects one bullet_list; got types={types}")

    else:
        raise ValueError(f"§{section_number}: unknown spec type {kind!r}")


def _write_section_partial(brd_id: str, section_number: int, section_dict: Dict[str, Any]) -> None:
    """Write brds/{brd_id}/sections/{n}.partial.json. Failures are
    logged but non-fatal — the SSE channel will just miss this update."""
    key = f"brds/{brd_id}/sections/{section_number}.partial.json"
    try:
        s3_put_object(
            key=key,
            body=json.dumps(section_dict, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(f"[BRD-gen] failed to write {key}: {e}")


def _write_generation_status(
    brd_id: str,
    status: str,
    sections_complete: Optional[List[int]] = None,
    error_message: Optional[str] = None,
    missing_sections: Optional[List[int]] = None,
    session_id: Optional[str] = None,
    embedding_progress: Optional[int] = None,
    embedding_total: Optional[int] = None,
) -> None:
    """Write brds/{brd_id}/_generation_status.json — the SSE endpoint's
    terminal-state signal. `status` is one of: running, complete, failed.

    `embedding_progress`/`embedding_total` are heartbeat fields written during
    the long pre-section embedding step so the SSE stream has fresh bytes to
    forward to the client. The SSE endpoint surfaces these as progress events.
    """
    body = {
        "brd_id": brd_id,
        "session_id": session_id,
        "status": status,
        "sections_complete": sections_complete or [],
        "updated_at": int(time.time()),
    }
    if error_message:
        body["error_message"] = error_message
    if missing_sections:
        body["missing_sections"] = missing_sections
    if embedding_progress is not None:
        body["embedding_progress"] = int(embedding_progress)
    if embedding_total is not None:
        body["embedding_total"] = int(embedding_total)
    try:
        s3_put_object(
            key=f"brds/{brd_id}/_generation_status.json",
            body=json.dumps(body, indent=2),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(f"[BRD-gen] failed to write _generation_status.json: {e}")


def _prime_cache(system_blocks: List[Dict[str, Any]], user_id: Optional[str]) -> Dict[str, int]:
    """Fire one tiny chat_completion to populate Anthropic's cache with
    the shared system blocks. Returns the usage dict (prompt/completion/
    cache_write/cache_read tokens) so the caller can aggregate cost.

    Anthropic's caching: for concurrent requests, a cache entry only
    becomes available AFTER the first response begins. If we fan out 16
    section calls in parallel without priming first, ALL 16 are cache
    writes (1.25× cost). Priming gives us 1 write + 15 reads (~0.1×).

    Best-effort: any failure is logged and we proceed without caching.
    Generation still works; we just pay full input cost on every call.
    """
    try:
        res = chat_completion(
            messages=[{"role": "user", "content": "Acknowledge: prime."}],
            system_prompt=system_blocks,
            temperature=0.0,
            max_tokens=8,
            return_metadata=True,
            user_id=user_id,
            token_source="lambda_brd_generator:prime",
        )
        usage = (res or {}).get("usage", {}) if isinstance(res, dict) else {}
        u = {
            "prompt":      int(usage.get("prompt_tokens", 0) or 0),
            "completion":  int(usage.get("completion_tokens", 0) or 0),
            "cache_write": int(usage.get("cache_creation_input_tokens", 0) or 0),
            "cache_read":  int(usage.get("cache_read_input_tokens", 0) or 0),
        }
        if u["cache_write"]:
            logger.info(
                f"[BRD-gen] PRIME wrote {u['cache_write']} tokens to cache "
                f"(prompt={u['prompt']} completion={u['completion']})"
            )
        else:
            logger.warning(
                f"[BRD-gen] PRIME returned usage but cache_write=0 — caching "
                f"may not be active. prompt={u['prompt']}; if 0 the gateway is "
                f"dropping the system block. Check llm_gateway pass-through."
            )
        return u
    except Exception as e:
        logger.warning(f"[BRD-gen] PRIME failed (continuing without cache): {e}")
        return {"prompt": 0, "completion": 0, "cache_write": 0, "cache_read": 0}


def _estimate_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
) -> float:
    """Per-call cost estimate using Anthropic Sonnet 4.5 published rates.

    Rates (per 1M tokens):
      base input        $3.00
      cache write       $3.75  (input × 1.25)
      cache read        $0.30  (input × 0.10)
      output            $15.00

    `prompt_tokens` from the API INCLUDES the cache_write and cache_read
    portions; the "regular" (uncached) input is the remainder.
    """
    regular_input = max(0, prompt_tokens - cache_write_tokens - cache_read_tokens)
    return round(
        (regular_input      * 3.00 / 1_000_000)
        + (cache_write_tokens * 3.75 / 1_000_000)
        + (cache_read_tokens  * 0.30 / 1_000_000)
        + (completion_tokens  * 15.00 / 1_000_000),
        5,
    )


def _build_rag_index(
    transcript_text: str,
    chat_history_text: str,
    progress_callback=None,
) -> Optional[Dict[str, Any]]:
    """Chunk + embed the input corpus into an IN-MEMORY vector index for
    per-section retrieval. Reuses services.embedding_service (the configured
    splitter + batched embeddings). Returns
    {"texts": [...], "tags": [...], "mat": np.ndarray (L2-normalized)} or
    None if RAG is unavailable (missing dep / embed failure) — the caller then
    falls back to the inline full-context path.

    Ephemeral + isolated: nothing is persisted here; BRD chunks never touch
    the shared pgvector store.
    """
    try:
        from services.embedding_service import embedding_service
        import numpy as np
    except Exception as e:
        logger.warning(f"[BRD-gen RAG] embedding_service/numpy unavailable — inline fallback: {e}")
        return None

    sources: List[Tuple[str, str]] = []
    if transcript_text:
        sources.append(("uploaded document", transcript_text))
    if chat_history_text:
        sources.append(("gathering conversation", chat_history_text))

    chunks: List[Tuple[str, str]] = []  # (chunk_text, source_tag)
    for tag, text in sources:
        try:
            pieces = embedding_service.recursive_splitter.split_text(text)
        except Exception as e:
            logger.warning(f"[BRD-gen RAG] split failed for {tag}: {e}; using whole text as one chunk")
            pieces = [text]
        for p in pieces:
            p = (p or "").strip()
            if len(p) > 50:
                chunks.append((p, tag))

    if not chunks:
        return None

    try:
        vecs = embedding_service.generate_embeddings_batch(
            [c[0] for c in chunks],
            progress_callback=progress_callback,
        )
    except Exception as e:
        logger.warning(f"[BRD-gen RAG] embedding failed — inline fallback: {e}")
        return None
    if not vecs or len(vecs) != len(chunks):
        logger.warning(
            f"[BRD-gen RAG] embedding count mismatch ({len(vecs) if vecs else 0} vs "
            f"{len(chunks)}) — inline fallback"
        )
        return None

    mat = np.asarray(vecs, dtype="float32")
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    logger.info(f"[BRD-gen RAG] built in-memory index: {len(chunks)} chunks, dim={mat.shape[1]}")
    return {"texts": [c[0] for c in chunks], "tags": [c[1] for c in chunks], "mat": mat}


def _retrieve_for_section(index: Dict[str, Any], query_vec: List[float], k: int) -> List[Dict[str, str]]:
    """Top-k cosine retrieval from the in-memory index for one section.
    Returns [{"title": source_tag, "content": chunk_text}]."""
    import numpy as np
    q = np.asarray(query_vec, dtype="float32")
    qn = np.linalg.norm(q)
    if qn == 0:
        return []
    q = q / qn
    sims = index["mat"] @ q
    top = np.argsort(-sims)[: max(1, k)]
    return [{"title": index["tags"][i], "content": index["texts"][i]} for i in top]


def _build_context_bundle(
    *,
    template_text: str,
    transcript_text: str = "",
    chat_history_text: str = "",
    long_term_facts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assemble the unified context bundle that goes into the cached system
    prefix. Every generation considers ALL available sources; the section
    workers don't branch on origin. Only non-empty sources are included so
    the cached prefix stays as small as the inputs allow.

      transcript        — uploaded doc text (docs path)
      chat_history      — formatted gathering conversation (history path)
      long_term_facts   — facts retrieved from prior sessions on this
                          project (opt-in; marked [from prior session] so
                          the model treats them as Tier-2 supporting context)
    """
    bundle: Dict[str, Any] = {
        "template_text": template_text,
        "style_constraints": (
            "Formal, professional tone. No first-person ('we'/'I'). "
            "Use tight, structured language — no padding. Reference other "
            "sections by number ('§4') not by content."
        ),
    }
    if transcript_text:
        bundle["transcript"] = transcript_text
    if chat_history_text:
        bundle["chat_history"] = chat_history_text
    if long_term_facts:
        # Marker mirrors the Tier-3 [assumption] convention: content sourced
        # from prior sessions is supporting context, not a fresh Tier-1 fact.
        bundle["long_term_facts"] = [f"{f} [from prior session]" for f in long_term_facts]
    return bundle


def _generate_brd_parallel(
    *,
    brd_id: str,
    session_id: Optional[str],
    template_text: str,
    transcript_text: str = "",
    chat_history_text: str = "",
    long_term_facts: Optional[List[str]] = None,
    existing_sections: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Phase 6 parallel generation: prime cache → fan out 16 → assemble.

    Sequence:
      1. Build cacheable system blocks from the shared context bundle.
      2. PRIME: fire one chat_completion to populate Anthropic's cache.
         Without priming, 16 parallel calls all become cache writes
         (1.25× cost). With priming, 1 write + 15 reads (~0.1× each).
      3. Fan out 16 section workers via ThreadPoolExecutor bounded by
         BRD_SECTION_PARALLELISM. Each worker validates its own output
         against SECTION_FORMATS and writes brds/{brd_id}/sections/{n}.partial.json
         on completion (the SSE endpoint polls this prefix).
      4. Assemble in canonical section-number order.

    Returns a brd_structure dict ready to write to brd_structure.json.
    """
    start_ts = time.time()

    logger.info(
        f"[BRD-gen] ════ START parallel generation brd_id={brd_id} "
        f"session={session_id} max_workers={BRD_SECTION_PARALLELISM} ════"
    )

    # Mark generation as started so the SSE endpoint can confirm in-flight.
    _write_generation_status(brd_id, "running", sections_complete=[], session_id=session_id)

    existing_sections = existing_sections or {}

    # ── Per-section RAG (Phase 1): index the corpus ONCE, retrieve per section.
    # When active, the full transcript/chat are NOT placed in the cached prefix
    # — each section only sees its retrieved slice, which keeps every call small
    # and avoids the ~450K-token gateway 502 on large inputs.
    #
    # Hybrid coverage (post-audit): embedding-similarity retrieval undercounts
    # SURFACE facts (dates, names, vendors, metrics) because they don't
    # contribute to semantic similarity. The facts-ledger pass extracts those
    # deterministically and routes them to gap-heavy sections. Runs in parallel
    # with embedding so the user-visible latency is max(embed, regex), not sum.
    corpus_chars = len(transcript_text or "") + len(chat_history_text or "")
    rag_index = None
    section_chunks: Dict[int, List[Dict[str, str]]] = {}
    section_facts: Dict[int, List[str]] = {}
    if BRD_USE_RAG_CONTEXT and corpus_chars >= BRD_RAG_MIN_CHARS:
        logger.info(
            f"[BRD-gen RAG] corpus={corpus_chars} chars >= {BRD_RAG_MIN_CHARS} "
            f"— building per-section index + facts ledger"
        )

        # Throttled heartbeat: write _generation_status.json every ~10s with
        # embedding_progress so the SSE endpoint has fresh bytes to forward.
        # Without this, large corpora (>1 MB) can sit silent for 2+ minutes
        # during the embedding-only phase and the browser drops the SSE
        # connection before any section completes.
        _emb_hb_state = {"last": 0.0}
        def _embedding_progress_cb(processed: int, total: int) -> None:
            now = time.time()
            if now - _emb_hb_state["last"] < 10.0:
                return
            _emb_hb_state["last"] = now
            try:
                _write_generation_status(
                    brd_id, "running",
                    sections_complete=[],
                    session_id=session_id,
                    embedding_progress=processed,
                    embedding_total=total,
                )
            except Exception as e:
                logger.warning(
                    f"[BRD-gen] embedding heartbeat write failed (non-fatal): {e}"
                )

        def _build_index_job():
            return _build_rag_index(
                transcript_text,
                chat_history_text,
                progress_callback=_embedding_progress_cb,
            )

        def _build_facts_job():
            if not BRD_USE_FACTS_LEDGER:
                return {}
            corpus = (transcript_text or "") + ("\n\n" + chat_history_text if chat_history_text else "")
            try:
                from services.facts_extractor import extract_facts, route_facts_to_sections
                t0 = time.time()
                # spaCy NER is opt-in via BRD_USE_FACT_EXTRACTION. When OFF
                # we run regex-only (always free, always available). When ON
                # we also try spaCy — falls back gracefully if the package or
                # en_core_web_sm model isn't bundled into the Lambda zip.
                facts = extract_facts(corpus, use_spacy=BRD_USE_FACT_EXTRACTION)
                routed = route_facts_to_sections(facts)
                logger.info(
                    f"[BRD-gen FACTS] extracted in {time.time()-t0:0.2f}s — "
                    f"{sum(len(v) for v in facts.values())} raw facts across "
                    f"{len(facts)} categories; routed to {len(routed)} sections "
                    f"(spacy={'on' if BRD_USE_FACT_EXTRACTION else 'off'})"
                )
                return routed
            except Exception as e:
                logger.warning(f"[BRD-gen FACTS] extractor failed — continuing without ledger: {e}")
                return {}

        with ThreadPoolExecutor(max_workers=2) as ex_idx:
            f_idx = ex_idx.submit(_build_index_job)
            f_fct = ex_idx.submit(_build_facts_job)
            rag_index = f_idx.result()
            section_facts = f_fct.result()

        if rag_index:
            try:
                from services.embedding_service import embedding_service
                seeds = [SECTION_RAG_QUERIES[n] for n, _t, _s in BRD_SECTIONS]
                seed_vecs = embedding_service.generate_embeddings_batch(seeds)
                for i, (n, _t, _s) in enumerate(BRD_SECTIONS):
                    # Gap-heavy sections (4,6,10,12,13,14,16) get more chunks
                    # because the coverage audit showed embedding-similarity
                    # under-retrieves the facts those sections need.
                    k = BRD_RAG_TOP_K_HIGH if n in HIGH_K_SECTIONS else BRD_RAG_TOP_K
                    section_chunks[n] = _retrieve_for_section(rag_index, seed_vecs[i], k)
                logger.info(
                    f"[BRD-gen RAG] retrieved chunks: default_k={BRD_RAG_TOP_K} "
                    f"high_k={BRD_RAG_TOP_K_HIGH} (sections {sorted(HIGH_K_SECTIONS)}) "
                    f"for {len(section_chunks)} sections"
                )
            except Exception as e:
                logger.warning(f"[BRD-gen RAG] section retrieval failed — inline fallback: {e}")
                rag_index = None
                section_chunks = {}
    elif BRD_USE_RAG_CONTEXT:
        logger.info(
            f"[BRD-gen RAG] corpus={corpus_chars} chars < {BRD_RAG_MIN_CHARS} "
            f"— small input, inlining full context (no RAG)"
        )
    rag_active = bool(rag_index and section_chunks)

    # Build cached system blocks ONCE; every section worker reuses them. When
    # RAG is active, omit the bulky transcript/chat from the cached prefix.
    context_bundle = _build_context_bundle(
        template_text=template_text,
        transcript_text="" if rag_active else transcript_text,
        chat_history_text="" if rag_active else chat_history_text,
        long_term_facts=long_term_facts,
    )
    system_blocks = build_cached_system_blocks(context_bundle)
    logger.info(
        f"[BRD-gen] cached prefix size: {len(system_blocks[0]['text'])} chars "
        f"(~{len(system_blocks[0]['text']) // 4} tokens estimated)"
    )

    # PRIME the cache before fan-out (Anthropic docs are explicit: cache
    # entries become readable only after the first response begins).
    prime_usage = _prime_cache(system_blocks, user_id)

    # Fan out 16 section workers.
    fanout_started = time.time()
    logger.info(
        f"[BRD-gen] >>> fan-out begins at +{fanout_started - start_ts:0.2f}s "
        f"({len(BRD_SECTIONS)} workers, max_workers={BRD_SECTION_PARALLELISM})"
    )
    results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=BRD_SECTION_PARALLELISM) as ex:
        futures = [
            ex.submit(
                _generate_one_section,
                section_number=n,
                system_blocks=system_blocks,
                user_id=user_id,
                brd_id=brd_id,
                gen_start_ts=start_ts,
                current_draft=existing_sections.get(n),
                rag_chunks=section_chunks.get(n),
                facts_ledger=section_facts.get(n),
            )
            for n, _title, _slug in BRD_SECTIONS
        ]
        for fut in as_completed(futures):
            try:
                n, section_dict = fut.result()
                results[n] = section_dict
                # Push a running-status update so the SSE endpoint sees
                # cumulative progress without having to LIST the prefix.
                _write_generation_status(
                    brd_id,
                    "running",
                    sections_complete=sorted(results.keys()),
                    session_id=session_id,
                )
            except Exception as e:
                logger.error(f"[BRD-gen] section worker raised: {e}")

    # Assemble in canonical order even though futures completed out of order.
    sections = [results[n] for n, _t, _s in BRD_SECTIONS if n in results]
    missing  = [n for n, _t, _s in BRD_SECTIONS if n not in results]
    failed   = [s["number"] for s in sections if s.get("status") == "generation_failed"]
    elapsed  = round(time.time() - start_ts, 2)

    # ── Aggregate token usage across prime + all section calls ────────
    agg = {"prompt": prime_usage["prompt"], "completion": prime_usage["completion"],
           "cache_write": prime_usage["cache_write"], "cache_read": prime_usage["cache_read"]}
    section_durations: List[float] = []
    for s in sections:
        u = s.get("_usage") or {}
        agg["prompt"]      += int(u.get("prompt", 0))
        agg["completion"]  += int(u.get("completion", 0))
        agg["cache_write"] += int(u.get("cache_write", 0))
        agg["cache_read"]  += int(u.get("cache_read", 0))
        if s.get("_duration_s") is not None:
            section_durations.append(float(s["_duration_s"]))

    cost = _estimate_cost_usd(
        prompt_tokens=agg["prompt"],
        completion_tokens=agg["completion"],
        cache_write_tokens=agg["cache_write"],
        cache_read_tokens=agg["cache_read"],
    )
    # Parallelism evidence: if the sum of section durations >> wall time
    # of fan-out, workers ran in parallel. Serial would show sum ≈ wall.
    sum_section_seconds = round(sum(section_durations), 2) if section_durations else 0.0
    fanout_wall = round(time.time() - fanout_started, 2)
    parallelism_factor = (
        round(sum_section_seconds / fanout_wall, 2)
        if fanout_wall > 0 else 0.0
    )
    cache_hit_ratio = (
        round(agg["cache_read"] / max(1, agg["cache_read"] + agg["cache_write"]), 3)
    )

    logger.info(
        f"[BRD-gen] ════ END parallel generation brd_id={brd_id} elapsed={elapsed}s ════"
    )
    logger.info(
        f"[BRD-gen] TOKEN SUMMARY  prompt={agg['prompt']} completion={agg['completion']} "
        f"cache_write={agg['cache_write']} cache_read={agg['cache_read']}"
    )
    logger.info(
        f"[BRD-gen] COST ESTIMATE  ${cost:.5f} per generation "
        f"(cache_hit_ratio={cache_hit_ratio} — higher is cheaper)"
    )
    logger.info(
        f"[BRD-gen] PARALLELISM    sum_section_seconds={sum_section_seconds}s "
        f"fanout_wall={fanout_wall}s factor={parallelism_factor}× "
        f"(>1.0 means workers overlapped; ~{BRD_SECTION_PARALLELISM} is ideal)"
    )
    logger.info(
        f"[BRD-gen] RESULT         {len(sections)}/{len(BRD_SECTIONS)} sections; "
        f"failed={failed}; missing={missing}"
    )

    # Strip debug fields from the canonical section payloads before
    # returning — _usage and _duration_s are LOG-ONLY, never written to
    # brd_structure.json.
    for s in sections:
        s.pop("_usage", None)
        s.pop("_duration_s", None)

    return {
        "brd_id": brd_id,
        "sections": sections,
        "missing": missing,
        "failed": failed,
        "duration_seconds": elapsed,
        "_token_summary": agg,        # routed to the handler's log line
        "_cost_usd": cost,
        "_parallelism_factor": parallelism_factor,
        "_cache_hit_ratio": cache_hit_ratio,
    }


def _coerce_event(event: Any) -> Dict[str, Any]:
    if isinstance(event, dict):
        return event
    if isinstance(event, str):
        try:
            return json.loads(event)
        except json.JSONDecodeError:
            return {"message": event}
    return {}


def _truncate_text(text: str, max_chars: int) -> str:
    """
    Truncate text to max_chars, trying to cut at sentence boundaries.
    
    Args:
        text: Text to truncate
        max_chars: Maximum characters allowed
        
    Returns:
        Truncated text (with ellipsis if truncated)
    """
    if len(text) <= max_chars:
        return text
    
    # Try to cut at sentence boundary
    truncated = text[:max_chars]
    last_period = truncated.rfind('.')
    last_newline = truncated.rfind('\n')
    cut_point = max(last_period, last_newline)
    
    if cut_point > max_chars * 0.8:  # Only use sentence boundary if it's not too early
        return truncated[:cut_point + 1] + "\n\n[... transcript truncated for length ...]"
    else:
        return truncated + "\n\n[... transcript truncated for length ...]"


def _convert_brd_text_to_structure(brd_text: str) -> Optional[Dict]:
    """
    Convert plain-text BRD into structured JSON format.
    This is a simplified version that creates a basic structure from the text.
    """
    try:
        import re
        sections = []
        lines = brd_text.split('\n')
        current_section = None
        current_content = []
        
        for line in lines:
            line = line.strip()
            if not line:
                if current_section and current_content:
                    # Add accumulated content as paragraph
                    current_section['content'].append({
                        "type": "paragraph",
                        "text": '\n'.join(current_content).strip()
                    })
                    current_content = []
                continue
            
            # Look for section headers (numbered sections like "1. Title" or "## Title" or "SECTION 4:")
            # CRITICAL: Only recognize sections 1-16. Ignore numbered items beyond 16 (they're sub-items within sections)
            section_match = re.match(r'^(?:SECTION\s+)?(\d+)\.?\s*(.+)$', line, re.IGNORECASE)
            if section_match:
                section_num = int(section_match.group(1))
                # Only treat as section if it's 1-16 (the main BRD sections)
                # Numbers 17+ are likely sub-items, flow steps, or use case details within a section
                if section_num > 16:
                    # This is a sub-item, not a section - treat as content
                    if current_section:
                        current_content.append(line)
                    continue
                
                # Also check if this looks like a document title (usually the first line without "Document Overview" etc.)
                title_text = section_match.group(2).strip()
                # Skip if it's likely a document title (contains "AI-Powered" or similar, and we haven't seen "Document Overview" yet)
                if section_num == 1 and not any(sec.get('title', '').lower().startswith('document overview') for sec in sections):
                    # Check if this looks like a document title rather than a section
                    if 'ai-powered' in title_text.lower() or 'brd' in title_text.lower() or len(title_text) < 30:
                        # Likely document title - skip it, don't create a section
                        if current_section:
                            current_content.append(line)
                        continue
            
            if section_match or (line.startswith('##') and len(line) > 3):
                # Save previous section
                if current_section:
                    if current_content:
                        current_section['content'].append({
                            "type": "paragraph",
                            "text": '\n'.join(current_content).strip()
                        })
                    sections.append(current_section)
                
                # Start new section
                if section_match:
                    title = section_match.group(2).strip()
                else:
                    title = line.replace('##', '').strip()
                
                current_section = {
                    "title": title,
                    "content": []
                }
                current_content = []
            elif current_section:
                # Check if line is a table row (contains | or tabs)
                if '|' in line or '\t' in line:
                    # Try to parse as table
                    if '|' in line:
                        cells = [cell.strip() for cell in line.split('|') if cell.strip()]
                    else:
                        cells = [cell.strip() for cell in line.split('\t') if cell.strip()]
                    
                    if cells and len(cells) > 1:
                        # Check if we have a table block already
                        if current_section['content'] and current_section['content'][-1].get('type') == 'table':
                            current_section['content'][-1]['rows'].append(cells)
                        else:
                            # Start new table
                            if current_content:
                                current_section['content'].append({
                                    "type": "paragraph",
                                    "text": '\n'.join(current_content).strip()
                                })
                                current_content = []
                            current_section['content'].append({
                                "type": "table",
                                "rows": [cells]
                            })
                        continue
                
                # Check if line is a bullet point
                if line.startswith('- ') or line.startswith('• ') or line.startswith('* '):
                    bullet_text = re.sub(r'^[-•*]\s+', '', line)
                    if current_section['content'] and current_section['content'][-1].get('type') == 'bullet':
                        current_section['content'][-1]['items'].append(bullet_text)
                    else:
                        # Start new bullet list
                        if current_content:
                            current_section['content'].append({
                                "type": "paragraph",
                                "text": '\n'.join(current_content).strip()
                            })
                            current_content = []
                        current_section['content'].append({
                            "type": "bullet",
                            "items": [bullet_text]
                        })
                    continue
                
                # Regular content line
                current_content.append(line)
        
        # Don't forget the last section
        if current_section:
            if current_content:
                current_section['content'].append({
                    "type": "paragraph",
                    "text": '\n'.join(current_content).strip()
                })
            sections.append(current_section)
        
        if sections:
            return {"sections": sections}
        return None
    except Exception as e:
        logger.error(f"Failed to convert BRD text to structure: {e}")
        return None


def _extract_text_from_docx(docx_bytes: bytes) -> str:
    """
    Extract text from DOCX file using pure Python (no external dependencies).
    
    DOCX files are ZIP archives containing XML files. This function:
    1. Extracts the ZIP
    2. Reads word/document.xml
    3. Parses XML to extract text content
    
    Args:
        docx_bytes: Raw bytes of the DOCX file
        
    Returns:
        Extracted text content
    """
    import zipfile
    import xml.etree.ElementTree as ET
    import io
    
    try:
        # DOCX is a ZIP archive
        zip_file = zipfile.ZipFile(io.BytesIO(docx_bytes))
        
        # Read the main document XML
        document_xml = zip_file.read('word/document.xml')
        
        # Parse XML
        root = ET.fromstring(document_xml)
        
        # Define namespace (DOCX uses specific namespaces)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        
        # Extract all text from paragraphs
        paragraphs = []
        for para in root.findall('.//w:p', ns):
            texts = []
            for text_elem in para.findall('.//w:t', ns):
                if text_elem.text:
                    texts.append(text_elem.text)
            if texts:
                paragraphs.append(''.join(texts))
        
        return '\n'.join(paragraphs)
        
    except Exception as e:
        logger.error(f"Failed to extract text from DOCX: {e}", exc_info=True)
        raise RuntimeError(f"Failed to extract text from DOCX: {e}")


def _invoke_bedrock(prompt: str, max_tokens: int = None, user_id: Optional[str] = None) -> str:
    """
    Invoke Bedrock model to generate BRD.

    Args:
        prompt: The full prompt text
        max_tokens: Maximum tokens to generate. If None, uses MAX_TOKENS env var.
        user_id: User to attribute token usage to.
    """
    effective_max_tokens = max_tokens if max_tokens is not None else MAX_TOKENS

    # Log configuration (model is resolved by llm_gateway from DLXAI_CHAT_MODEL env var)
    logger.info(f"Max tokens: {effective_max_tokens}, Temperature: {TEMPERATURE}")
    logger.info(f"Prompt length: {len(prompt)} characters (~{len(prompt)//4} tokens estimated)")

    brd_text = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=TEMPERATURE,
        max_tokens=effective_max_tokens,
        user_id=user_id,
        token_source="lambda_brd_generator",
    )

    logger.info(f"Generated BRD length: {len(brd_text)} characters")

    if not brd_text or not brd_text.strip():
        logger.error("Model response was empty or whitespace-only!")
        logger.error(f"brd_text value: '{brd_text[:200] if brd_text else 'None'}'")
        # Don't raise error - return a placeholder BRD instead
        # This prevents error messages from being saved as BRD content
        brd_text = "[BRD generation failed: Model returned empty response. Please check input and try again.]"
        logger.warning("Returning placeholder BRD instead of error")

    logger.info("BRD generation successful!")
    return brd_text


# ============================================================================
# Fix 2 — post-generation BRD-to-AgentCore-memory push
#
# After a successful generation, we serialize each section into one or more
# DECLARATIVE facts and write them as AgentCore Memory conversational events
# under a SEPARATE sessionId ("_brd_snapshot_<brd_id>"). The registered
# SEMANTIC strategy then extracts those facts into namespace
# "user-{user_id}:project-{project_id}" so a future session in the same
# project can retrieve them via get_long_term_facts.
#
# Decomposition is SECTION-AWARE so we don't over-flood retrieval with 87
# atomic "the system shall…" FR facts. See plan §"Files to modify > 4. lambda_brd_generator.py".
# ============================================================================

# Per-section decomposition strategy.
_SECTION_STRATEGY = {
    1:  "paragraph",   # Document Overview
    2:  "paragraph",   # Purpose
    3:  "paragraph",   # Background / Context
    4:  "per_row",     # Stakeholders
    5:  "per_bullet",  # Scope (In/Out)
    6:  "per_row",     # Business Objectives
    7:  "summary",     # Functional Requirements (~87 rows -> 1-3 facts)
    8:  "summary",     # NFR
    9:  "summary",     # User Stories
    10: "per_bullet",  # Assumptions
    11: "per_bullet",  # Constraints
    12: "summary",     # Acceptance Criteria / KPIs
    13: "per_row",     # Timeline / Milestones
    14: "per_row",     # Risks & Dependencies
    15: "per_row",     # Approval & Review
    16: "per_row",     # Glossary
}

_FACT_MAX_CHARS = 1500


def _trunc_fact(s: str) -> str:
    """Cap each fact at _FACT_MAX_CHARS with an ellipsis marker."""
    s = (s or "").strip()
    if len(s) > _FACT_MAX_CHARS:
        return s[:_FACT_MAX_CHARS].rstrip() + " …"
    return s


def _section_to_memory_facts(
    sec: Dict[str, Any],
    *,
    project_name: str,
    brd_id: str,
) -> List[str]:
    """Section-aware declarative fact builder. See plan for the per-section
    decomposition rationale. Returns a list of fact strings; caller writes
    each as an ASSISTANT event in the snapshot session.

    Skips:
      - blank / dash / [TBD] rows (those are gaps, not facts)
      - empty paragraphs
      - sections without a recognized strategy (no facts emitted)
    """
    try:
        from services.brd_orchestrator_utils import _row_is_blank_or_tbd
    except Exception:
        # Fallback if utils not bundled in this Lambda
        def _row_is_blank_or_tbd(row):  # type: ignore
            return not any(str(c or "").strip() for c in (row or []))

    n = sec.get("number")
    title = (sec.get("title") or "").strip()
    strategy = _SECTION_STRATEGY.get(n)
    if not strategy:
        return []

    p = project_name
    facts: List[str] = []

    if strategy == "paragraph":
        for block in sec.get("content") or []:
            if (block.get("type") or "").lower() != "paragraph":
                continue
            text = (block.get("text") or "").strip()
            if not text:
                continue
            facts.append(_trunc_fact(f"BRD for project {p}, {title}: {text}"))

    elif strategy == "per_bullet":
        for block in sec.get("content") or []:
            btype = (block.get("type") or "").lower()
            if btype not in ("bullet_list", "bullet", "glossary"):
                continue
            heading = (block.get("text") or "").strip()  # e.g. "In Scope" sub-header
            label = f"{title} ({heading})" if heading else title
            for item in block.get("items") or []:
                s = str(item or "").strip()
                if not s:
                    continue
                facts.append(_trunc_fact(f"BRD for project {p}, {label}: {s}"))

    elif strategy == "per_row":
        for block in sec.get("content") or []:
            if (block.get("type") or "").lower() != "table":
                continue
            headers = [str(h or "").strip() for h in (block.get("headers") or [])]
            for row in block.get("rows") or []:
                if _row_is_blank_or_tbd(row):
                    continue
                cells = [str(c or "").strip() for c in row]
                # Render as "Header: cell; Header: cell; …"
                pairs = [f"{h}: {c}" for h, c in zip(headers, cells) if c]
                fact = f"BRD for project {p}, {title} — " + "; ".join(pairs)
                facts.append(_trunc_fact(fact))

    elif strategy == "summary":
        total = 0
        first_id = ""
        last_id = ""
        sample_descs: List[str] = []
        for block in sec.get("content") or []:
            if (block.get("type") or "").lower() != "table":
                continue
            rows = [r for r in (block.get("rows") or []) if not _row_is_blank_or_tbd(r)]
            total += len(rows)
            if rows and not first_id:
                first_id = str(rows[0][0] or "").strip()
            if rows:
                last_id = str(rows[-1][0] or "").strip()
            # Collect up to 5 row "description" cells (second column conventionally)
            for r in rows[:5]:
                if len(r) >= 2:
                    desc = str(r[1] or "").strip()
                    if desc and len(sample_descs) < 5:
                        sample_descs.append(desc[:120])
        if total > 0:
            id_range = ""
            if first_id and last_id and first_id != last_id:
                id_range = f" (IDs {first_id}..{last_id})"
            elif first_id:
                id_range = f" (ID {first_id})"
            main = (
                f"BRD for project {p}, {title}: {total} entries{id_range}"
            )
            if sample_descs:
                main += ". Examples: " + " | ".join(sample_descs[:3])
            facts.append(_trunc_fact(main))
            # Up to two additional verbatim-row highlights for high-value
            # subjects (first two non-empty rows beyond the summary line)
            for desc in sample_descs[:2]:
                facts.append(_trunc_fact(f"BRD for project {p}, {title} — {desc}"))

    return facts


def _resolve_project_name(structure: Dict[str, Any], project_id: Optional[str]) -> str:
    """Try to recover a human-readable project name for the fact prefix.
    Falls back to project_id, then "<unknown>" if nothing helpful exists.

    Looks in §1 Document Overview's Document Name row, if present.
    """
    for sec in structure.get("sections") or []:
        if sec.get("number") != 1:
            continue
        for block in sec.get("content") or []:
            if (block.get("type") or "").lower() != "table":
                continue
            for row in block.get("rows") or []:
                if len(row) >= 2 and str(row[0] or "").strip().lower() == "document name":
                    name = str(row[1] or "").strip()
                    if name and name not in ("TBD", "-", "—"):
                        return name
    return project_id or "<unknown>"


def _push_brd_to_memory(
    *,
    brd_id: str,
    structure: Dict[str, Any],
    user_id: Optional[str],
    project_id: Optional[str],
    session_id: Optional[str],
) -> None:
    """Inline serial push of every section's facts into AgentCore Memory.

    Writes to a SEPARATE sessionId ("_brd_snapshot_<brd_id>") so
    read_memory_history (which queries by real chat sessionId) never
    sees these — they're for SEMANTIC extraction only.

    Failures are logged at WARNING and continue — bad memory writes
    NEVER fail BRD generation.
    """
    t0 = time.time()
    if not (user_id and project_id and session_id):
        logger.info(
            f"[BRD-mem] skipping memory push: user_id={bool(user_id)} "
            f"project_id={bool(project_id)} session_id={bool(session_id)}"
        )
        return

    try:
        from services.brd_orchestrator_utils import (
            write_memory_event,
            batch_create_facts,
            BRD_SNAPSHOT_SESSION_PREFIX,
        )
    except Exception as e:
        logger.warning(f"[BRD-mem] brd_orchestrator_utils unavailable — skip push: {e}")
        return

    snapshot_sid = f"{BRD_SNAPSHOT_SESSION_PREFIX}{brd_id}"
    project_name = _resolve_project_name(structure, project_id)
    sections = structure.get("sections") or []

    logger.info(
        f"[BRD-mem] push start brd_id={brd_id} snapshot_sid={snapshot_sid} "
        f"project_name={project_name!r} section_count={len(sections)}"
    )

    fact_count = 0
    failed_writes = 0
    all_facts: List[str] = []  # collected for direct batch_create
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        n = sec.get("number")
        if sec.get("status") == "generation_failed":
            logger.info(f"[BRD-mem] skip §{n} (status=generation_failed)")
            continue
        section_facts = _section_to_memory_facts(
            sec, project_name=project_name, brd_id=brd_id,
        )
        if not section_facts:
            continue
        for i, fact_text in enumerate(section_facts):
            try:
                write_memory_event(
                    session_id=snapshot_sid,
                    user_id=user_id,
                    role="ASSISTANT",
                    content=fact_text,
                )
                fact_count += 1
            except Exception as e:
                failed_writes += 1
                logger.warning(
                    f"[BRD-mem] write_memory_event failed §{n} fact#{i}: {e}"
                )
            all_facts.append(fact_text)
        logger.info(f"[BRD-mem] §{n} ({sec.get('title','')!r}) -> {len(section_facts)} facts")

    # CRITICAL FIX (2026-06-03): the memory store's builtin SEMANTIC strategy
    # was registered (2026-03-05) without proper extraction config, so events
    # written via create_event above NEVER produce searchable records. Verified
    # via probes in .scratch/probe_*.py — 4 brdsnap sessions with hundreds of
    # events produced 0 extracted records, while batch_create_memory_records
    # writes ARE retrievable via list_memory_records. So we ALSO push each
    # fact directly as a pre-formed record so get_long_term_facts(...) can
    # find them via the list fallback path on the read side.
    direct_written = batch_create_facts(
        user_id=user_id,
        project_id=project_id,
        facts=all_facts,
    )
    logger.info(
        f"[BRD-mem] direct batch_create_facts written={direct_written}/{len(all_facts)} "
        f"namespace=user-{user_id}:project-{project_id}"
    )

    elapsed = time.time() - t0
    logger.info(
        f"[BRD-mem] push DONE sections={len(sections)} facts_written={fact_count} "
        f"direct_records_written={direct_written} failed_writes={failed_writes} "
        f"elapsed={elapsed:0.1f}s sessionId={snapshot_sid}"
    )


def _handle_parallel(evt: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 entry point: prime-then-fan-out section generation.

    Expected event shape:
      {"parallel": True, "brd_id": "...", "session_id": "...",
       "user_id": "...",
       "template": "..." (or template_s3_*),
       "transcript": "..." (or transcript_s3_*)}

    The session_id is optional but recommended — when present, the
    final-assembly step records it on _generation_status.json so the
    SSE endpoint can confirm it's looking at the right execution.

    Writes:
      - brds/{brd_id}/sections/{n}.partial.json (per section, as each completes)
      - brds/{brd_id}/brd_structure.json        (final assembly)
      - brds/{brd_id}/_generation_status.json   (live status heartbeat)

    Returns a slim {brd_id, status, section_count} envelope.
    """
    brd_id     = evt.get("brd_id") or str(uuid.uuid4())
    session_id = evt.get("session_id")
    user_id    = evt.get("user_id")

    # Unified context bundle. The orchestrator passes everything available
    # under event["context"]; legacy/test callers may still pass the same
    # fields at the top level, so read context first then fall back.
    ctx = evt.get("context") or {}

    def _src(*keys: str):
        for k in keys:
            if ctx.get(k):
                return ctx.get(k)
            if evt.get(k):
                return evt.get(k)
        return None

    default_bucket = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")

    # Pull template + transcript — supports both inline-text and
    # S3-key payload shapes (matches the existing monolithic input contract).
    template_text   = _src("template", "template_text")
    transcript_text = _src("transcript", "transcript_text")

    template_s3_bucket   = _src("template_s3_bucket")
    template_s3_key      = _src("template_s3_key")
    transcript_s3_bucket = _src("transcript_s3_bucket") or (default_bucket if _src("transcript_s3_key") else None)
    transcript_s3_key    = _src("transcript_s3_key")

    # Additional unified sources.
    chat_session_id  = _src("chat_session_id") or session_id
    existing_brd_id  = _src("existing_brd_id")
    long_term_facts  = ctx.get("long_term_facts") or evt.get("long_term_facts") or []

    if not template_text and template_s3_bucket and template_s3_key:
        try:
            s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            obj = s3.get_object(Bucket=template_s3_bucket, Key=template_s3_key)
            data = obj["Body"].read()
            template_text = _extract_text_from_docx(data) if template_s3_key.endswith(".docx") \
                else data.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"[BRD-gen parallel] template S3 fetch failed: {e}")
            return _parallel_error_response(brd_id, f"template fetch failed: {e}", session_id)

    # Auto-fetch the canonical Deluxe template from S3 when the caller
    # didn't pass one. The template is FIXED (s3://.../templates/
    # Deluxe_BRD_Template.docx) — users never upload it. The from-history
    # worker has always done this fetch; the from-docs path historically
    # required the caller to supply it, which broke when the unified
    # frontend stopped collecting a separate template file.
    if not template_text:
        try:
            default_bucket = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
            default_key    = os.getenv("BRD_TEMPLATE_S3_KEY", "templates/Deluxe_BRD_Template.docx")
            logger.info(
                f"[BRD-gen parallel] no template in event — fetching canonical "
                f"Deluxe template from s3://{default_bucket}/{default_key}"
            )
            s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            obj = s3.get_object(Bucket=default_bucket, Key=default_key)
            data = obj["Body"].read()
            template_text = _extract_text_from_docx(data) if default_key.endswith(".docx") \
                else data.decode("utf-8", errors="replace")
            logger.info(f"[BRD-gen parallel] Deluxe template fetched: {len(template_text)} chars")
        except Exception as e:
            logger.error(f"[BRD-gen parallel] canonical template S3 fetch failed: {e}")
            return _parallel_error_response(
                brd_id, f"could not load Deluxe template from S3: {e}", session_id,
            )

    if not transcript_text and transcript_s3_bucket and transcript_s3_key:
        try:
            s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            obj = s3.get_object(Bucket=transcript_s3_bucket, Key=transcript_s3_key)
            data = obj["Body"].read()
            transcript_text = _extract_text_from_docx(data) if transcript_s3_key.endswith(".docx") \
                else data.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"[BRD-gen parallel] transcript S3 fetch failed: {e}")
            return _parallel_error_response(brd_id, f"transcript fetch failed: {e}", session_id)

    # Chat history — the gathering conversation. Fetched here (not passed
    # inline) so a long conversation never bloats the async-invoke payload.
    chat_history_text = ""
    if chat_session_id:
        try:
            messages = get_conversation_history(chat_session_id, user_id=user_id)
            chat_history_text = format_conversation(messages)
            logger.info(f"[BRD-gen parallel] chat history: {len(chat_history_text)} chars")
        except Exception as e:
            logger.warning(f"[BRD-gen parallel] chat history fetch failed (continuing): {e}")

    # Existing BRD — for regeneration, each section's current content is
    # handed to its worker so regen merges instead of overwriting.
    existing_sections: Dict[int, List[Dict[str, Any]]] = {}
    if existing_brd_id:
        existing_sections = _read_existing_brd_sections(existing_brd_id)

    if long_term_facts:
        logger.info(f"[BRD-gen parallel] long-term facts: {len(long_term_facts)} fact(s)")

    # Every generation uses all available sources. Require at least one
    # content source — the template alone produces an empty-shell BRD.
    if not (transcript_text or chat_history_text or existing_sections):
        return _parallel_error_response(
            brd_id,
            "no content source: provide a transcript, a chat_session_id with "
            "conversation history, or an existing_brd_id to regenerate",
            session_id,
        )

    # Fan out. _generate_brd_parallel writes status=running and section
    # partials; we own the terminal status write below.
    try:
        structure = _generate_brd_parallel(
            brd_id=brd_id,
            session_id=session_id,
            template_text=template_text,
            transcript_text=transcript_text or "",
            chat_history_text=chat_history_text,
            long_term_facts=long_term_facts,
            existing_sections=existing_sections,
            user_id=user_id,
        )
    except Exception as e:
        logger.exception("[BRD-gen parallel] generation raised")
        _write_generation_status(
            brd_id, "failed", error_message=str(e), session_id=session_id,
        )
        return _parallel_error_response(brd_id, f"generation failed: {e}", session_id)

    missing = structure.get("missing", [])
    failed  = structure.get("failed", [])
    sections = structure.get("sections", [])

    # Decide success/partial/failed. A BRD with any missing or failed
    # section is marked partial — the user can regenerate problem
    # sections individually rather than abandoning the whole document.
    if missing:
        terminal_status = "failed"
        error_msg = f"Sections missing from output: {missing}"
    elif failed:
        terminal_status = "partial"
        error_msg = f"Sections failed validation: {failed}"
    else:
        terminal_status = "complete"
        error_msg = None

    # Write the canonical brd_structure.json regardless — even partial
    # output is more useful to the user than nothing, and the per-
    # section "status=generation_failed" markers let the frontend show
    # an error chip on the affected sections only.
    structure_payload = {
        "brd_id": brd_id,
        "session_id": session_id,
        "sections": sections,
        "generated_at": int(time.time()),
        "duration_seconds": structure.get("duration_seconds"),
        "mode": "parallel",
    }
    structure_key = f"brds/{brd_id}/brd_structure.json"
    try:
        s3_put_object(
            key=structure_key,
            body=json.dumps(structure_payload, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
    except Exception as e:
        logger.error(f"[BRD-gen parallel] brd_structure.json write failed: {e}")
        _write_generation_status(
            brd_id, "failed", error_message=f"S3 write failed: {e}",
            sections_complete=[s["number"] for s in sections], session_id=session_id,
        )
        return _parallel_error_response(brd_id, f"S3 write failed: {e}", session_id)

    # ORDER MATTERS: write terminal status FIRST so the frontend SSE sees
    # generation as complete IMMEDIATELY. Then push BRD content into AgentCore
    # Memory after — that adds 6-10s to lambda billed time but is INVISIBLE
    # to the user. Previously this was reversed; the [BRD-mem] delay left
    # the SSE stream in a "running" state for 6-10s after S3 already had all
    # section partials, which manifested as late-fan-out sections (§13/§14/§16)
    # appearing stuck in the UI.
    _write_generation_status(
        brd_id,
        terminal_status,
        sections_complete=[s["number"] for s in sections if s.get("status") == "llm_generated"],
        missing_sections=(missing + failed) or None,
        error_message=error_msg,
        session_id=session_id,
    )

    # Fix 2: push BRD content into AgentCore Memory as declarative facts
    # under a "brdsnap-<brd_id>" sessionId (AgentCore's sessionId regex
    # rejects leading underscores) so cross-session recall surfaces prior-
    # session BRD content. Runs AFTER terminal_status so the user already
    # sees their BRD as complete.
    if terminal_status in ("complete", "partial"):
        project_id = evt.get("project_id") or (evt.get("context") or {}).get("project_id")
        try:
            _push_brd_to_memory(
                brd_id=brd_id,
                structure=structure_payload,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"[BRD-gen parallel] memory push failed (non-fatal): {e}")
    else:
        logger.info(f"[BRD-gen parallel] skipping memory push (terminal_status={terminal_status})")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "brd_id":            brd_id,
            "session_id":        session_id,
            "status":            terminal_status,
            "section_count":     len(sections),
            "failed_sections":   failed,
            "missing_sections":  missing,
            "s3_key":            structure_key,
            "duration_seconds":  structure.get("duration_seconds"),
            "mode":              "parallel",
        }),
    }


def _parallel_error_response(brd_id: str, message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Uniform error envelope for the parallel path. Same statusCode
    shape the orchestrator expects from any other action handler."""
    return {
        "statusCode": 500,
        "body": json.dumps({
            "brd_id":     brd_id,
            "session_id": session_id,
            "status":     "error",
            "error":      message,
            "mode":       "parallel",
        }),
    }


def lambda_handler(event, context):
    """
    Lambda handler for BRD generation.

    This function is called by:
    1. Bedrock Agent (Agent Mode) - expects Bedrock Agent response format
    2. AgentCore Gateway (Direct Mode) - expects simple JSON with 'brd' field
    3. lambda_brd_orchestrator with event["parallel"]=True -- routes to
       the parallel section-generation path defined above (~30-40s
       vs. ~90s monolithic).

    CRITICAL: Always returns a valid response, even on errors.
    """
    logger.info("=== BRD Generator Lambda Started ===")
    logger.info(f"Received event type: {type(event)}")
    logger.info(f"Received event: {json.dumps(event, default=str)[:1000]}")

    evt = _coerce_event(event)

    # Parallel section-generation opt-in. The orchestrator's
    # _start_generation sets event["parallel"]=True from Phase 2
    # commit 9 onward; legacy callers (Bedrock Agent invocations,
    # direct test invokes) fall through to the monolithic path below
    # so this is a strictly additive change.
    if isinstance(evt, dict) and evt.get("parallel") is True:
        return _handle_parallel(evt)

    # Log the FULL event structure for debugging (no truncation)
    logger.info("=" * 80)
    logger.info("=== FULL EVENT STRUCTURE DEBUG ===")
    logger.info("=" * 80)
    try:
        if isinstance(evt, dict):
            logger.info(f"Event is a dict with {len(evt)} keys")
            logger.info(f"Event keys: {list(evt.keys())}")
            
            # Log each key-value pair separately
            for key, value in evt.items():
                if isinstance(value, (dict, list)):
                    logger.info(f"  {key}: {type(value).__name__} with {len(value)} items")
                    if isinstance(value, dict):
                        logger.info(f"    Sub-keys: {list(value.keys())}")
                        # Check for actionGroupInput
                        if key == "actionGroupInput" or "actionGroupInput" in str(value):
                            logger.info(f"    Found actionGroupInput structure!")
                            if isinstance(value, dict) and "actionGroupInput" in value:
                                logger.info(f"    actionGroupInput keys: {list(value['actionGroupInput'].keys())}")
                else:
                    value_str = str(value)
                    if len(value_str) > 200:
                        logger.info(f"  {key}: {value_str[:200]}... (truncated, length: {len(value_str)})")
                    else:
                        logger.info(f"  {key}: {value_str}")
            
            # Try to find brd_id in various locations
            logger.info("--- Searching for brd_id ---")
            if "brd_id" in evt:
                logger.info(f"  Found brd_id at top level: {evt['brd_id']}")
            if "brdId" in evt:
                logger.info(f"  Found brdId at top level: {evt['brdId']}")
            if "actionGroupInput" in evt:
                ag_input = evt["actionGroupInput"]
                if isinstance(ag_input, dict):
                    if "brd_id" in ag_input:
                        logger.info(f"  Found brd_id in actionGroupInput: {ag_input['brd_id']}")
                    if "brdId" in ag_input:
                        logger.info(f"  Found brdId in actionGroupInput: {ag_input['brdId']}")
            if "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    if "brd_id" in params:
                        logger.info(f"  Found brd_id in parameters dict: {params['brd_id']}")
                elif isinstance(params, list):
                    logger.info(f"  parameters is a list with {len(params)} items")
                    for i, param in enumerate(params):
                        if isinstance(param, dict):
                            if param.get("name") == "brd_id" or param.get("key") == "brd_id":
                                logger.info(f"  Found brd_id in parameters[{i}]: {param.get('value')}")
        else:
            logger.info(f"Event is NOT a dict, it's: {type(evt)}")
            logger.info(f"Event value: {str(evt)[:500]}")
    except Exception as e:
        logger.error(f"Failed to log event structure: {e}", exc_info=True)
    logger.info("=" * 80)

    # Handle agent invocation format
    # Bedrock Agent passes parameters in different formats:
    # 1. Direct: {"template": "...", "transcript": "...", "brd_id": "..."}
    # 2. Nested: {"parameters": {"template": "...", "transcript": "...", "brd_id": "..."}}
    # 3. List format: {"parameters": [{"name": "template", "value": "..."}, ...]}
    # 4. Action group format: {"actionGroupInput": {"template": "...", "transcript": "...", "brd_id": "..."}}
    # 5. NEW: S3 keys format: {"template_s3_bucket": "...", "template_s3_key": "...", "transcript_s3_bucket": "...", "transcript_s3_key": "..."}
    
    template_text = None
    transcript_text = None
    brd_id = None
    template_s3_bucket = None
    template_s3_key = None
    transcript_s3_bucket = None
    transcript_s3_key = None
    user_id = None  # for token attribution

    # First, try direct access
    if isinstance(evt, dict):
        template_text = evt.get("template") or evt.get("template_text")
        transcript_text = evt.get("transcript") or evt.get("transcript_text")
        brd_id = evt.get("brd_id") or evt.get("brdId")
        user_id = evt.get("user_id")
        
        # Check for S3 keys (new approach - files in S3, not in message)
        template_s3_bucket = evt.get("template_s3_bucket")
        template_s3_key = evt.get("template_s3_key")
        transcript_s3_bucket = evt.get("transcript_s3_bucket")
        transcript_s3_key = evt.get("transcript_s3_key")
        
        # Check actionGroupInput format (Bedrock Agent format)
        if not template_text and "actionGroupInput" in evt:
            logger.info("Detected actionGroupInput format")
            action_input = evt["actionGroupInput"]
            if isinstance(action_input, dict):
                template_text = action_input.get("template") or action_input.get("template_text")
                transcript_text = action_input.get("transcript") or action_input.get("transcript_text")
                if not brd_id:
                    brd_id = action_input.get("brd_id") or action_input.get("brdId")
                
                # Check for S3 keys in actionGroupInput
                if not template_s3_bucket:
                    template_s3_bucket = action_input.get("template_s3_bucket")
                    template_s3_key = action_input.get("template_s3_key")
                    transcript_s3_bucket = action_input.get("transcript_s3_bucket")
                    transcript_s3_key = action_input.get("transcript_s3_key")
        
        # If not found, check nested "parameters" key
        if not template_text and "parameters" in evt:
            logger.info("Detected agent invocation format with 'parameters' key")
            params = evt["parameters"]
            
            # Handle dict format: {"parameters": {"template": "...", "transcript": "...", "brd_id": "..."}}
            if isinstance(params, dict):
                template_text = params.get("template") or params.get("template_text")
                transcript_text = params.get("transcript") or params.get("transcript_text")
                if not brd_id:
                    brd_id = params.get("brd_id") or params.get("brdId")
            
            # Handle list format: {"parameters": [{"name": "template", "value": "..."}, ...]}
            elif isinstance(params, list):
                logger.info(f"Parameters is a list with {len(params)} items")
                for param in params:
                    if isinstance(param, dict):
                        param_name = param.get("name") or param.get("key")
                        param_value = param.get("value") or param.get("val")
                        
                        if param_name == "template" or param_name == "template_text":
                            template_text = param_value
                        elif param_name == "transcript" or param_name == "transcript_text":
                            transcript_text = param_value
                        elif param_name == "brd_id" or param_name == "brdId":
                            brd_id = param_value
                        elif param_name == "template_s3_bucket":
                            template_s3_bucket = param_value
                        elif param_name == "template_s3_key":
                            template_s3_key = param_value
                        elif param_name == "transcript_s3_bucket":
                            transcript_s3_bucket = param_value
                        elif param_name == "transcript_s3_key":
                            transcript_s3_key = param_value
                
                # Also try direct list access (if list contains dicts with keys)
                if not template_text and not transcript_text:
                    for item in params:
                        if isinstance(item, dict):
                            if "template" in item:
                                template_text = item.get("template") or item.get("template_text")
                            if "transcript" in item:
                                transcript_text = item.get("transcript") or item.get("transcript_text")
    
    # If evt itself is a list (unlikely but handle it)
    elif isinstance(evt, list):
        logger.warning("Event is a list, trying to extract from items")
        for item in evt:
            if isinstance(item, dict):
                if not template_text:
                    template_text = item.get("template") or item.get("template_text")
                if not transcript_text:
                    transcript_text = item.get("transcript") or item.get("transcript_text")
                if template_text and transcript_text:
                    break
    
    # If we have S3 keys but not text, fetch from S3
    if (template_s3_bucket and template_s3_key) and not template_text:
        logger.info(f"Fetching template from S3: s3://{template_s3_bucket}/{template_s3_key}")
        try:
            s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            template_obj = s3_client.get_object(Bucket=template_s3_bucket, Key=template_s3_key)
            template_bytes = template_obj["Body"].read()
            
            # Extract text from DOCX or plain text
            if template_s3_key.endswith(".docx"):
                template_text = _extract_text_from_docx(template_bytes)
            else:
                template_text = template_bytes.decode("utf-8", errors="replace")
            logger.info(f"Successfully fetched template from S3: {len(template_text)} characters")
        except Exception as e:
            logger.error(f"Failed to fetch template from S3: {e}", exc_info=True)
            raise RuntimeError(f"Failed to fetch template from S3: {e}")
    
    if (transcript_s3_bucket and transcript_s3_key) and not transcript_text:
        logger.info(f"Fetching transcript from S3: s3://{transcript_s3_bucket}/{transcript_s3_key}")
        try:
            s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            transcript_obj = s3_client.get_object(Bucket=transcript_s3_bucket, Key=transcript_s3_key)
            transcript_bytes = transcript_obj["Body"].read()
            
            # Extract text from DOCX or plain text
            if transcript_s3_key.endswith(".docx"):
                transcript_text = _extract_text_from_docx(transcript_bytes)
            else:
                transcript_text = transcript_bytes.decode("utf-8", errors="replace")
            logger.info(f"Successfully fetched transcript from S3: {len(transcript_text)} characters")
        except Exception as e:
            logger.error(f"Failed to fetch transcript from S3: {e}", exc_info=True)
            raise RuntimeError(f"Failed to fetch transcript from S3: {e}")
    
    logger.info(f"Extracted template length: {len(template_text) if template_text else 0} characters")
    logger.info(f"Extracted transcript length: {len(transcript_text) if transcript_text else 0} characters")
    logger.info(f"Extracted brd_id: {brd_id if brd_id else 'NOT FOUND - S3 SAVE WILL BE SKIPPED!'}")

    if not template_text or not transcript_text:
        logger.error("Missing required fields: template or transcript")
        # Return error in Bedrock Agent expected format
        return {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": "Both 'template' and 'transcript' fields are required."
                    }
                }
            }
        }

    # Build prompt using separated prompt templates
    # Calculate token estimates using PromptConfig
    from prompts.brd_generator_prompts import get_prompt_base_length
    
    instructions_tokens = PromptConfig.estimate_tokens(str(get_prompt_base_length()))
    template_tokens = PromptConfig.estimate_tokens(template_text)
    transcript_tokens = PromptConfig.estimate_tokens(transcript_text)
    
    # Target: Keep total input under 2000 tokens to leave ~6000 for output
    # This ensures we have enough room for comprehensive BRD generation
    # More aggressive truncation to prevent token limit issues
    max_input_tokens = PromptConfig.MAX_INPUT_TOKENS
    safety_margin = PromptConfig.SAFETY_MARGIN_TOKENS
    reserved_for_template = PromptConfig.RESERVED_TEMPLATE_TOKENS
    
    # Truncate template if extremely long
    if template_tokens > reserved_for_template:
        max_template_chars = reserved_for_template * PromptConfig.CHARS_PER_TOKEN
        logger.warning(f"Template is very long ({template_tokens} tokens). Truncating to ~{reserved_for_template} tokens ({max_template_chars} chars)")
        template_text = _truncate_text(template_text, max_template_chars)
        template_length = len(template_text)
        template_tokens = PromptConfig.estimate_tokens(template_text)
    
    # Calculate available space for transcript
    max_transcript_tokens = max_input_tokens - instructions_tokens - template_tokens - safety_margin
    
    # Ensure minimum space for transcript (at least 500 tokens)
    if max_transcript_tokens < PromptConfig.MIN_TRANSCRIPT_TOKENS:
        logger.warning(f"Very little space for transcript ({max_transcript_tokens} tokens). Further truncating template if needed.")
        # Recalculate with more aggressive template truncation
        max_template_tokens = max_input_tokens - instructions_tokens - PromptConfig.MIN_TRANSCRIPT_TOKENS - safety_margin
        if max_template_tokens > 0:
            max_template_chars = max_template_tokens * PromptConfig.CHARS_PER_TOKEN
            template_text = _truncate_text(template_text, max_template_chars)
            template_length = len(template_text)
            template_tokens = PromptConfig.estimate_tokens(template_text)
            max_transcript_tokens = PromptConfig.MIN_TRANSCRIPT_TOKENS
    
    # Truncate transcript if too long
    if transcript_tokens > max_transcript_tokens:
        max_transcript_chars = max_transcript_tokens * PromptConfig.CHARS_PER_TOKEN
        logger.warning(f"Transcript is too long ({transcript_tokens} tokens). Truncating to ~{max_transcript_tokens} tokens ({max_transcript_chars} chars)")
        transcript_text = _truncate_text(transcript_text, max_transcript_chars)
        transcript_length = len(transcript_text)
        transcript_tokens = PromptConfig.estimate_tokens(transcript_text)
    
    # Build final prompt using the separated prompt template function
    prompt = get_full_brd_generation_prompt(template_text, transcript_text)

    # Calculate dynamic max tokens based on actual prompt length.
    # Recalculate after truncation to get accurate estimate.
    # Use PromptConfig.TOTAL_CONTEXT_TOKENS so this stays aligned with the model's context.
    estimated_prompt_tokens = (instructions_tokens + template_tokens + transcript_tokens)
    total_context = PromptConfig.TOTAL_CONTEXT_TOKENS
    # Separate safety margin for output-side calculations (in addition to PromptConfig.SAFETY_MARGIN_TOKENS
    # which is used on the input side).
    safety_margin = 3_000
    available_output_tokens = total_context - estimated_prompt_tokens - safety_margin
    
    # Use the smaller of: available tokens or configured MAX_TOKENS
    dynamic_max_tokens = min(available_output_tokens, MAX_TOKENS)
    
    # Ensure minimum of 2000 tokens for output (but never exceed available)
    if available_output_tokens >= 2000:
        dynamic_max_tokens = max(dynamic_max_tokens, 2000)
    else:
        # If less than 2000 available, use what we have (but warn)
        logger.warning(f"Only {available_output_tokens} tokens available for output. Prompt may be too long.")
        dynamic_max_tokens = available_output_tokens
    
    # CRITICAL: Final safety check - never exceed available
    dynamic_max_tokens = min(dynamic_max_tokens, available_output_tokens)
    
    logger.info(f"Prompt breakdown: Instructions ~{instructions_tokens}, Template ~{template_tokens}, Transcript ~{transcript_tokens} tokens")
    logger.info(f"Total input: ~{estimated_prompt_tokens} tokens, Available for output: ~{available_output_tokens} tokens")
    logger.info(f"Using dynamic max_gen_len: {dynamic_max_tokens} tokens (configured MAX_TOKENS: {MAX_TOKENS})")
    
    if estimated_prompt_tokens > 3500:
        logger.warning(f"Prompt is very long ({estimated_prompt_tokens} tokens). Output limited to {dynamic_max_tokens} tokens.")

    try:
        brd_text = _invoke_bedrock(prompt, max_tokens=dynamic_max_tokens, user_id=user_id)
    except RuntimeError as exc:
        # RuntimeError usually means token limit or model issue
        error_msg = str(exc)
        logger.error(f"RuntimeError generating BRD: {error_msg}", exc_info=True)

        # Save error message as BRD content to S3 if brd_id provided
        error_brd_content = f"[BRD Generation Error]\n\n{error_msg}\n\nPlease check the input parameters and try again."
        brd_id = None
        if isinstance(evt, dict):
            brd_id = evt.get("brd_id") or evt.get("brdId")
            if not brd_id and "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId")

        if brd_id:
            try:
                brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
                s3_put_object(key=brd_key, body=error_brd_content, content_type="text/plain")
                logger.info(f"Saved error message as BRD to S3: {brd_key}")
            except Exception as e:
                logger.error(f"Failed to save error BRD to S3: {e}", exc_info=True)
        
        # Return error in Bedrock Agent expected format
        error_response = {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"BRD generation failed: {error_msg}"
                    }
                }
            }
            
        }
        logger.info(f"Returning error response. brd_id: {brd_id}")
        return error_response
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected error generating BRD: {str(exc)}", exc_info=True)

        # Save error message as BRD content to S3 if brd_id provided
        error_brd_content = f"[BRD Generation Error]\n\nUnexpected error: {str(exc)}\n\nPlease check the logs and try again."
        brd_id = None
        if isinstance(evt, dict):
            brd_id = evt.get("brd_id") or evt.get("brdId")
            if not brd_id and "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId")

        if brd_id:
            try:
                brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
                s3_put_object(key=brd_key, body=error_brd_content, content_type="text/plain")
                logger.info(f"Saved error message as BRD to S3: {brd_key}")
            except Exception as e:
                logger.error(f"Failed to save error BRD to S3: {e}", exc_info=True)
        
        # Return error in Bedrock Agent expected format
        error_response = {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"Unexpected error: {str(exc)}"
                    }
                }
            }
        }
        logger.info(f"Returning unexpected error response. brd_id: {brd_id}")
        return error_response

    # brd_id should already be extracted above, but double-check
    # (This code was redundant - brd_id is already extracted earlier)
    if not brd_id:
        logger.warning("brd_id is still None after extraction - attempting to re-extract")
        if isinstance(evt, dict):
            brd_id = evt.get("brd_id") or evt.get("brdId")
            if not brd_id and "actionGroupInput" in evt:
                action_input = evt["actionGroupInput"]
                if isinstance(action_input, dict):
                    brd_id = action_input.get("brd_id") or action_input.get("brdId")
            if not brd_id and "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId")
                elif isinstance(params, list):
                    for param in params:
                        if isinstance(param, dict):
                            param_name = param.get("name") or param.get("key")
                            if param_name in ["brd_id", "brdId"]:
                                brd_id = param.get("value") or param.get("val")
                                break
    
    logger.info(f"Final brd_id value before S3 save: {brd_id if brd_id else 'NONE - WILL NOT SAVE TO S3'}")
    
    # FALLBACK: If brd_id is None, generate one
    # This ensures BRD is always saved to S3, even if agent doesn't pass brd_id
    if not brd_id:
        brd_id = str(uuid.uuid4())
        logger.warning(f"⚠️  brd_id was None! Generated new brd_id: {brd_id}")
        logger.warning(f"⚠️  This means the Bedrock Agent didn't pass brd_id as a parameter")
        logger.warning(f"⚠️  The BRD will be saved with this generated ID, but it won't match the expected ID")
    
    # Always save to S3 now (since we have brd_id)
    if brd_id:
        try:
            brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
            s3_put_object(key=brd_key, body=brd_text, content_type="text/plain")
            logger.info(f"Saved BRD text to S3: {brd_key}")

            # Also save structure JSON file (for chat Lambda to use)
            try:
                brd_structure = _convert_brd_text_to_structure(brd_text)
                if brd_structure:
                    structure_key = f"brds/{brd_id}/brd_structure.json"
                    s3_put_object(
                        key=structure_key,
                        body=json.dumps(brd_structure, indent=2, ensure_ascii=False),
                        content_type="application/json",
                    )
                    logger.info(f"Saved BRD structure to S3: {structure_key}")
                else:
                    logger.warning("Could not convert BRD text to structure, skipping structure file save")
            except Exception as structure_err:
                logger.warning(f"Failed to save BRD structure (non-critical): {structure_err}")
                # Continue - structure file is optional, chat Lambda can reconstruct it
        except Exception as e:
            logger.error(f"Failed to save BRD to S3: {e}", exc_info=True)
            # Continue anyway - return the BRD even if S3 save failed

    logger.info("=== BRD Generator Lambda Completed Successfully ===")
    
    # Detect invocation method: Agent Mode vs Direct Mode
    # CRITICAL: If S3 keys are present, it MUST be Agent Mode (we use S3 keys for Agent Mode)
    # Otherwise, default to Agent Mode (safer) unless we can definitively prove it's Direct Mode
    
    is_agent_mode = True  # Default to Agent Mode (safer)
    
    # If ANY S3 keys are present, it's definitely Agent Mode (new S3-based approach)
    if template_s3_bucket or template_s3_key or transcript_s3_bucket or transcript_s3_key:
        is_agent_mode = True
        logger.info("Detected Agent Mode: S3 keys present")
    elif isinstance(evt, dict):
        # Check for Agent Mode markers
        has_agent_markers = (
            "actionGroupInput" in evt or
            ("parameters" in evt and isinstance(evt["parameters"], list)) or
            ("parameters" in evt and isinstance(evt["parameters"], dict) and ("template_s3_key" in evt.get("parameters", {}) or "template_s3_bucket" in evt.get("parameters", {})))
        )
        
        # Check for Direct Mode markers (AgentCore Gateway specific)
        # Direct Mode typically has: {"template": "...", "transcript": "..."} at top level
        # AND no actionGroupInput, AND no parameters list format, AND no S3 keys
        has_direct_markers = (
            ("template" in evt or "transcript" in evt or "template_text" in evt or "transcript_text" in evt)
            and "actionGroupInput" not in evt
            and not has_agent_markers
        )
        
        if has_agent_markers:
            is_agent_mode = True
        elif has_direct_markers:
            is_agent_mode = False
    
    logger.info(f"Invocation mode detected: {'Agent Mode' if is_agent_mode else 'Direct Mode'}")
    logger.info(f"Event keys: {list(evt.keys()) if isinstance(evt, dict) else 'N/A'}")
    logger.info(f"Has actionGroupInput: {'actionGroupInput' in evt if isinstance(evt, dict) else False}")
    logger.info(f"Has S3 keys: template_s3_bucket={bool(template_s3_bucket)}, template_s3_key={bool(template_s3_key)}")
    
    # ALWAYS return Direct Mode format for AgentCore
    # This ensures BRD content and ID are both available
    logger.info("Returning Direct Mode format (includes BRD content + ID)")
    response = {
        "statusCode": 200,
        "body": json.dumps({
            "brd": brd_text,
            "brd_id": brd_id,
            "status": "success",
            "message": f"BRD generated successfully. Length: {len(brd_text)} chars."
        })
    }
    
    # Validate JSON serialization
    try:
        response_json = json.dumps(response)
        logger.info(f"Response JSON length: {len(response_json)} chars")
        logger.info(f"Response JSON (first 200 chars): {response_json[:200]}")
        return response
        
    except Exception as e:
        logger.error(f"CRITICAL: Failed to create response: {e}", exc_info=True)
        # Return error in correct format
        return {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"Error creating response: {str(e)[:500]}"
                    }
                }
            }
    }


