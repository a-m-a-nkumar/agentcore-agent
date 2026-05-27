import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import boto3
# Environment-specific LLM and S3 (local: direct Bedrock + plain S3 | VDI: Gateway + KMS S3)
from environment import chat_completion, s3_put_object

# Phase 6 imports — cacheable per-section prompts. The single source of
# truth for the 16-section structure lives in brd_section_definitions.
from prompts.brd_section_definitions import (
    BRD_SECTIONS,
    SECTION_FORMATS,
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
BRD_SECTION_PARALLELISM    = int(os.getenv("BRD_SECTION_PARALLELISM", "5"))
BRD_SECTION_MAX_TOKENS     = int(os.getenv("BRD_SECTION_MAX_TOKENS", "4000"))
BRD_SECTION_TEMPERATURE    = float(os.getenv("BRD_SECTION_TEMPERATURE", "0.3"))


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

def _generate_one_section(
    *,
    section_number: int,
    system_blocks: List[Dict[str, Any]],
    user_id: Optional[str],
    brd_id: str,
    gen_start_ts: float,
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
    user_msg = build_section_user_message(section_number)

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
    for attempt in (1, 2):
        try:
            res = chat_completion(
                messages=[{
                    "role": "user",
                    "content": user_msg + (retry_suffix if attempt == 2 else ""),
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
                "status": "llm_generated",
                "last_updated_ts": int(time.time()),
                "previous_versions": [],
                "_usage": last_usage,        # stripped before final write
                "_duration_s": round(section_end - section_start, 2),
            }
            _write_section_partial(brd_id, section_number, section_dict)
            return section_number, section_dict
        except Exception as e:
            last_err = e
            logger.warning(
                f"[BRD-gen] §{section_number} attempt {attempt} failed: {e} "
                f"raw[:300]={(last_raw or '')[:300]!r}"
            )

    section_end = time.time()
    logger.error(
        f"[BRD-gen] §{section_number:>2} FAIL  @ +{section_end - gen_start_ts:0.2f}s "
        f"after 2 attempts: {last_err}"
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
) -> None:
    """Write brds/{brd_id}/_generation_status.json — the SSE endpoint's
    terminal-state signal. `status` is one of: running, complete, failed.
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


def _build_context_bundle(
    *,
    template_text: str,
    transcript_text: str,
) -> Dict[str, Any]:
    """Assemble the context bundle that goes into the cached system
    prefix. Same shape used by both docs-path and history-path so the
    section workers don't need to branch on origin."""
    return {
        "transcript": transcript_text,
        "template_text": template_text,
        "style_constraints": (
            "Formal, professional tone. No first-person ('we'/'I'). "
            "Use tight, structured language — no padding. Reference other "
            "sections by number ('§4') not by content."
        ),
    }


def _generate_brd_parallel(
    *,
    brd_id: str,
    session_id: Optional[str],
    template_text: str,
    transcript_text: str,
    user_id: Optional[str],
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

    # Build cached system blocks ONCE; every section worker reuses them.
    context_bundle = _build_context_bundle(
        template_text=template_text,
        transcript_text=transcript_text,
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

    # Pull template + transcript — supports both inline-text and
    # S3-key payload shapes (matches the existing monolithic input contract).
    template_text   = evt.get("template")   or evt.get("template_text")
    transcript_text = evt.get("transcript") or evt.get("transcript_text")

    template_s3_bucket   = evt.get("template_s3_bucket")
    template_s3_key      = evt.get("template_s3_key")
    transcript_s3_bucket = evt.get("transcript_s3_bucket")
    transcript_s3_key    = evt.get("transcript_s3_key")

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

    if not transcript_text:
        return _parallel_error_response(
            brd_id,
            "transcript is required (the template is auto-fetched from S3; "
            "the transcript must be supplied by the caller)",
            session_id,
        )

    # Fan out. _generate_brd_parallel writes status=running and section
    # partials; we own the terminal status write below.
    try:
        structure = _generate_brd_parallel(
            brd_id=brd_id,
            session_id=session_id,
            template_text=template_text,
            transcript_text=transcript_text,
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

    # Final status write — the SSE endpoint reads this for terminal-state.
    _write_generation_status(
        brd_id,
        terminal_status,
        sections_complete=[s["number"] for s in sections if s.get("status") == "llm_generated"],
        missing_sections=(missing + failed) or None,
        error_message=error_msg,
        session_id=session_id,
    )

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


