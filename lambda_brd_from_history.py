"""
BRD from History Lambda Function
Generates BRD directly from conversation history using Bedrock.

Two paths:
  - Monolithic (legacy): single chat_completion produces all 16 sections.
  - Parallel (Phase 6, opt-in via event["parallel"]=True): prime-then-
    fan-out using prompts.brd_section_prompts + chat_completion's
    cacheable system blocks. Sections write to
    brds/{brd_id}/sections/{n}.partial.json as they complete, then
    final assembly writes brd_structure.json + _generation_status.json.
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import boto3
# Environment-specific LLM and S3 (local: direct Bedrock + plain S3 | VDI: Gateway + KMS S3)
from environment import chat_completion, s3_put_object

# Import prompts from centralized prompts module
from prompts import get_brd_from_history_prompt

# Phase 6 — parallel path imports. Reuses the same prompt and
# validation infrastructure as lambda_brd_generator so the two
# generation entry points behave identically.
from prompts.brd_section_definitions import BRD_SECTIONS
from prompts.brd_section_prompts import (
    build_cached_system_blocks,
    build_section_user_message,
)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
from environment import S3_BUCKET_NAME, DEFAULT_AGENTCORE_MEMORY_ID, DEFAULT_AGENTCORE_ACTOR_ID
AGENTCORE_MEMORY_ID = DEFAULT_AGENTCORE_MEMORY_ID
AGENTCORE_ACTOR_ID = DEFAULT_AGENTCORE_ACTOR_ID
S3_BUCKET = S3_BUCKET_NAME
TEMPLATE_S3_KEY = 'templates/Deluxe_BRD_Template.docx'
# Env-var reads use os.getenv() with sane defaults so the module imports
# cleanly in test/CI environments that don't set these. Lambda runtime
# always provides them via function env config.
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0')
BEDROCK_GUARDRAIL_ARN = os.getenv('BEDROCK_GUARDRAIL_ARN', '')
BEDROCK_GUARDRAIL_VERSION = os.getenv('BEDROCK_GUARDRAIL_VERSION', '1')
MAX_TOKENS = int(os.getenv('BEDROCK_MAX_TOKENS', '32000'))
TEMPERATURE = float(os.getenv('BEDROCK_TEMPERATURE', '0'))

# Lazy loading
_agentcore_memory_client = None
_s3_client = None


def _get_agentcore_memory_client():
    global _agentcore_memory_client
    if _agentcore_memory_client is None:
        _agentcore_memory_client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)
    return _agentcore_memory_client


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client


def get_conversation_history(
    session_id: str,
    max_messages: int = 99,
    user_id: Optional[str] = None,
) -> List[Dict]:
    """Get conversation history from AgentCore Memory using the DUAL-ACTOR
    read pattern (per-user actor + legacy 'analyst-session' actor).

    Why dual: the orchestrator writes USER/ASSISTANT events under
    `user-{user_id}` (Phase 2 plan, Resolved Q#5). Older sessions still
    have events under the legacy shared actor `analyst-session`. Reading
    only the legacy actor misses everything the orchestrator wrote this
    session — the symptom that hit Phase 6 testing: worker reported
    "Retrieved 0 messages from history" even after the user chatted.

    Returns events from both actors merged by eventTimestamp ascending.
    """
    client = _get_agentcore_memory_client()

    # Build the list of actors to query. When user_id is known, query the
    # per-user actor first; always also query the legacy actor for
    # historical continuity.
    actors: List[str] = []
    if user_id:
        actor_prefix = os.getenv("BRD_AGENTCORE_ACTOR_PREFIX", "user-")
        actors.append(f"{actor_prefix}{user_id}")
    legacy_actor = os.getenv("BRD_AGENTCORE_LEGACY_ACTOR", AGENTCORE_ACTOR_ID)
    if legacy_actor not in actors:
        actors.append(legacy_actor)

    logger.info(
        f"Fetching conversation history session={session_id} "
        f"actors={actors} max_messages={max_messages}"
    )

    # Tuples of (eventTimestamp_ms, actor_priority, {role,content}) so we
    # can sort across actors with a stable tiebreak.
    merged: List[Tuple[int, int, Dict]] = []
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
            logger.warning(f"list_events failed actor={actor} session={session_id}: {e}")
            continue

        actor_msg_count = 0
        for event in response.get("events", []):
            # Convert eventTimestamp (datetime) to epoch ms for sorting.
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
                actor_msg_count += 1
        logger.info(f"  actor={actor}: {actor_msg_count} messages")

    merged.sort(key=lambda t: (t[0], t[1]))
    messages = [m[2] for m in merged[-max_messages:]]
    logger.info(f"Retrieved {len(messages)} messages from history (merged from {len(actors)} actors)")
    return messages


def format_conversation(messages: List[Dict]) -> str:
    """Format conversation history as readable text"""
    lines = []
    
    for msg in messages:
        role = "USER" if msg.get("role") == "user" else "ANALYST"
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    
    return "\n\n".join(lines)


def fetch_template_from_s3() -> str:
    """Fetch BRD template from S3 and extract text"""
    s3_client = _get_s3_client()
    
    try:
        logger.info(f"Fetching template from s3://{S3_BUCKET}/{TEMPLATE_S3_KEY}")
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=TEMPLATE_S3_KEY)
        template_bytes = response['Body'].read()
        
        # Extract text from DOCX
        template_text = extract_text_from_docx(template_bytes)
        logger.info(f"Template extracted: {len(template_text)} characters")
        return template_text
        
    except Exception as e:
        logger.error(f"Error fetching template: {e}", exc_info=True)
        raise


def extract_text_from_docx(docx_bytes: bytes) -> str:
    """Extract text from DOCX file"""
    import zipfile
    import xml.etree.ElementTree as ET
    import io
    
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(docx_bytes))
        document_xml = zip_file.read('word/document.xml')
        root = ET.fromstring(document_xml)
        
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        
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
        raise


def generate_brd_with_bedrock(template: str, conversation: str, user_id: str = None) -> str:
    """Generate BRD using Bedrock AI"""
    # Build the full prompt using the centralized prompt function
    prompt = get_brd_from_history_prompt(
        template=template,
        conversation=conversation
    )

    logger.info(f"Calling Bedrock with prompt length: {len(prompt)} characters")
    logger.info(f"Model: {BEDROCK_MODEL_ID}, Max tokens: {MAX_TOKENS}")

    try:
        brd_text = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            user_id=user_id,
            token_source="lambda_brd_from_history",
        )
        
        logger.info(f"Generated BRD: {len(brd_text)} characters")
        return brd_text
        
    except Exception as e:
        logger.error(f"Error calling Bedrock: {e}", exc_info=True)
        raise


def convert_brd_to_json(brd_text: str) -> Dict:
    """
    Convert plain-text BRD into structured JSON format for editing.
    
    Parses sections, paragraphs, bullet points, and tables.
    """
    import re
    
    try:
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
            
            # Look for section headers (1-16 only)
            section_match = re.match(r'^(?:SECTION\s+)?(\d+)\.?\s*(.+)$', line, re.IGNORECASE)
            if section_match:
                section_num = int(section_match.group(1))
                
                # Only treat as section if it's 1-16
                if section_num > 16:
                    if current_section:
                        current_content.append(line)
                    continue
                
                # Save previous section
                if current_section:
                    if current_content:
                        current_section['content'].append({
                            "type": "paragraph",
                            "text": '\n'.join(current_content).strip()
                        })
                    sections.append(current_section)
                
                # Start new section
                title = section_match.group(2).strip()
                current_section = {
                    "section_number": section_num,
                    "title": title,
                    "content": []
                }
                current_content = []
                
            elif line.startswith('##') and len(line) > 3:
                # Alternative section header format
                if current_section:
                    if current_content:
                        current_section['content'].append({
                            "type": "paragraph",
                            "text": '\n'.join(current_content).strip()
                        })
                    sections.append(current_section)
                
                title = line.replace('##', '').strip()
                current_section = {
                    "title": title,
                    "content": []
                }
                current_content = []
                
            elif current_section:
                # Check for bullet points
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
                
                # Check for tables
                if '|' in line:
                    cells = [cell.strip() for cell in line.split('|') if cell.strip()]
                    if cells and len(cells) > 1:
                        if current_section['content'] and current_section['content'][-1].get('type') == 'table':
                            current_section['content'][-1]['rows'].append(cells)
                        else:
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
        
        logger.info(f"Converted BRD to JSON: {len(sections)} sections")
        return {"sections": sections}
        
    except Exception as e:
        logger.error(f"Error converting BRD to JSON: {e}", exc_info=True)
        return {"sections": [], "error": str(e)}


def save_brd_to_s3(brd_text: str, brd_id: str) -> Dict[str, str]:
    """Save generated BRD to S3 in both text and JSON formats.

    Writes:
      - brds/{brd_id}/BRD_{brd_id}.txt          (human-readable export)
      - brds/{brd_id}/brd_structure.json        (canonical JSON — was
                                                  BRD_{brd_id}.json; the
                                                  alternate path broke
                                                  every reader that expects
                                                  the canonical key)
    """
    try:
        # Save as text file (human-readable export — unchanged)
        txt_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
        s3_put_object(key=txt_key, body=brd_text, content_type="text/plain")
        txt_location = f"s3://{S3_BUCKET}/{txt_key}"
        logger.info(f"Saved BRD text to {txt_location}")

        # Convert to JSON structure
        brd_json = convert_brd_to_json(brd_text)

        # Save as canonical brd_structure.json (audit-flagged bug fix:
        # was BRD_{brd_id}.json, which app.py:3670 / routers/integrations.py:678
        # / lambda_brd_chat.py:264 / routers/brd.py /{sid}/sections couldn't
        # find — they all expect brd_structure.json).
        json_key = f"brds/{brd_id}/brd_structure.json"
        s3_put_object(key=json_key, body=json.dumps(brd_json, indent=2), content_type="application/json")
        json_location = f"s3://{S3_BUCKET}/{json_key}"
        logger.info(f"Saved BRD JSON to {json_location}")

        return {
            "txt": txt_location,
            "json": json_location
        }

    except Exception as e:
        logger.error(f"Error saving BRD to S3: {e}", exc_info=True)
        raise


# ============================================================================
# Phase 6 — Parallel section generation (history path)
# ============================================================================

# Same env knobs as lambda_brd_generator so they tune together.
BRD_SECTION_PARALLELISM = int(os.getenv("BRD_SECTION_PARALLELISM", "5"))
BRD_SECTION_MAX_TOKENS  = int(os.getenv("BRD_SECTION_MAX_TOKENS", "4000"))
BRD_SECTION_TEMPERATURE = float(os.getenv("BRD_SECTION_TEMPERATURE", "0.3"))


def _write_section_partial_h(brd_id: str, n: int, section_dict: Dict[str, Any]) -> None:
    """Write brds/{brd_id}/sections/{n}.partial.json. Best-effort."""
    key = f"brds/{brd_id}/sections/{n}.partial.json"
    try:
        s3_put_object(
            key=key,
            body=json.dumps(section_dict, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(f"[BRD-hist] failed to write {key}: {e}")


def _write_generation_status_h(
    brd_id: str,
    status: str,
    sections_complete: Optional[List[int]] = None,
    error_message: Optional[str] = None,
    missing_sections: Optional[List[int]] = None,
    session_id: Optional[str] = None,
) -> None:
    """Write brds/{brd_id}/_generation_status.json — terminal-state signal
    consumed by the SSE endpoint."""
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
        logger.warning(f"[BRD-hist] failed to write _generation_status.json: {e}")


def _extract_section_blocks_h(text: str) -> List[Dict[str, Any]]:
    """Pull a JSON array of content blocks from an LLM response.
    Mirrors lambda_brd_generator._extract_section_blocks."""
    import re
    if not text:
        raise ValueError("empty section response")
    s = text.strip()
    if s.startswith("["):
        return json.loads(s)
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"no JSON array found in section response (first 200 chars: {s[:200]!r})")


def _validate_section_format_h(n: int, content: List[Dict[str, Any]]) -> None:
    """Re-imports from lambda_brd_generator at call time to keep the
    validation logic in one place. Lazy import avoids a circular
    dependency at module load."""
    from lambda_brd_generator import _validate_section_against_format
    _validate_section_against_format(n, content)


def _generate_section_history(
    *,
    section_number: int,
    system_blocks: List[Dict[str, Any]],
    user_id: Optional[str],
    brd_id: str,
    gen_start_ts: float,
) -> Dict[str, Any]:
    """One section worker. Two attempts: original, then JSON-only retry.

    Records token usage on the returned dict under `_usage` so the
    fan-out caller can aggregate cost/parallelism stats. The debug
    fields are stripped before final brd_structure.json write.
    """
    from prompts.brd_section_definitions import section_title
    title = section_title(section_number)
    user_msg = build_section_user_message(section_number)
    retry_suffix = (
        "\n\nIMPORTANT — your previous response could not be parsed or "
        "did not match the schema. Reply with ONLY a JSON array of content "
        "blocks. No prose. No markdown fences. First character must be `[`.\n"
    )

    section_start = time.time()
    logger.info(
        f"[BRD-hist] §{section_number:>2} START @ +{section_start - gen_start_ts:0.2f}s "
        f"(parallel worker)"
    )

    last_err: Optional[Exception] = None
    last_usage: Dict[str, int] = {"prompt": 0, "completion": 0, "cache_write": 0, "cache_read": 0}
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
                token_source=f"lambda_brd_from_history:section{section_number}",
            )
            raw = res["content"] if isinstance(res, dict) else res
            usage = (res.get("usage") if isinstance(res, dict) else None) or {}
            last_usage = {
                "prompt":      int(usage.get("prompt_tokens", 0) or 0),
                "completion":  int(usage.get("completion_tokens", 0) or 0),
                "cache_write": int(usage.get("cache_creation_input_tokens", 0) or 0),
                "cache_read":  int(usage.get("cache_read_input_tokens", 0) or 0),
            }
            content = _extract_section_blocks_h(raw)
            _validate_section_format_h(section_number, content)
            section_end = time.time()
            logger.info(
                f"[BRD-hist] §{section_number:>2} DONE  @ +{section_end - gen_start_ts:0.2f}s "
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
                "_usage": last_usage,
                "_duration_s": round(section_end - section_start, 2),
            }
            _write_section_partial_h(brd_id, section_number, section_dict)
            return section_dict
        except Exception as e:
            last_err = e
            logger.warning(f"[BRD-hist] §{section_number} attempt {attempt} failed: {e}")

    section_end = time.time()
    logger.error(
        f"[BRD-hist] §{section_number:>2} FAIL  @ +{section_end - gen_start_ts:0.2f}s: {last_err}"
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
    _write_section_partial_h(brd_id, section_number, failed)
    return failed


def _handle_parallel_history(evt: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 entry point for the history path. Pulls conversation
    history from AgentCore Memory, formats it as the transcript, and
    runs the same prime-then-fanout flow as lambda_brd_generator.

    Expected event:
      {"parallel": True, "session_id": "...", "brd_id": "...",
       "user_id": "..."}

    Returns the same envelope shape as the monolithic path so the
    orchestrator can handle both transparently.
    """
    session_id = evt.get("session_id")
    brd_id     = evt.get("brd_id") or str(uuid.uuid4())
    user_id    = evt.get("user_id")

    if not session_id:
        _write_generation_status_h(brd_id, "failed",
                                    error_message="session_id required",
                                    session_id=session_id)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "session_id required", "brd_id": brd_id}),
        }

    # Mark running so the SSE endpoint can confirm in-flight.
    _write_generation_status_h(brd_id, "running", sections_complete=[], session_id=session_id)

    # Fetch conversation history under DUAL-ACTOR read so we find events
    # the orchestrator wrote under `user-{user_id}` AS WELL AS any legacy
    # events under the shared `analyst-session` actor.
    try:
        messages = get_conversation_history(session_id, user_id=user_id)
    except Exception as e:
        logger.error(f"[BRD-hist parallel] history fetch failed: {e}", exc_info=True)
        _write_generation_status_h(brd_id, "failed",
                                    error_message=f"history fetch failed: {e}",
                                    session_id=session_id)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"history fetch failed: {e}", "brd_id": brd_id}),
        }
    if not messages:
        _write_generation_status_h(brd_id, "failed",
                                    error_message="no conversation history",
                                    session_id=session_id)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "no conversation history for this session",
                                "brd_id": brd_id}),
        }

    transcript_text = format_conversation(messages)

    # Fetch the template so the cached prefix can reference Deluxe's
    # structural conventions. Failures are non-fatal — section workers
    # only need the section format specs (which live in SECTION_FORMATS,
    # passed in the cached block) to produce a valid BRD.
    try:
        template_text = fetch_template_from_s3()
    except Exception as e:
        logger.warning(f"[BRD-hist parallel] template fetch failed (continuing without): {e}")
        template_text = ""

    gen_start_ts = time.time()
    logger.info(
        f"[BRD-hist] ════ START parallel generation brd_id={brd_id} "
        f"session={session_id} max_workers={BRD_SECTION_PARALLELISM} ════"
    )

    # Build cached system blocks ONCE for the whole generation.
    context_bundle = {
        "transcript": transcript_text,
        "template_text": template_text,
        "style_constraints": (
            "Formal, professional tone. No first-person ('we'/'I'). "
            "Use tight, structured language — no padding. Reference other "
            "sections by number ('§4') not by content."
        ),
    }
    system_blocks = build_cached_system_blocks(context_bundle)
    logger.info(
        f"[BRD-hist] cached prefix size: {len(system_blocks[0]['text'])} chars "
        f"(~{len(system_blocks[0]['text']) // 4} tokens estimated)"
    )

    # Prime cache before fan-out (Anthropic requires the first response
    # to begin before parallel reads see the cache).
    prime_usage = {"prompt": 0, "completion": 0, "cache_write": 0, "cache_read": 0}
    try:
        from lambda_brd_generator import _prime_cache
        prime_usage = _prime_cache(system_blocks, user_id)
    except Exception as e:
        logger.warning(f"[BRD-hist] prime failed (continuing): {e}")

    # Fan out 16 section workers.
    fanout_started = time.time()
    logger.info(
        f"[BRD-hist] >>> fan-out begins at +{fanout_started - gen_start_ts:0.2f}s "
        f"({len(BRD_SECTIONS)} workers, max_workers={BRD_SECTION_PARALLELISM})"
    )
    results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=BRD_SECTION_PARALLELISM) as ex:
        futures = {
            ex.submit(
                _generate_section_history,
                section_number=n,
                system_blocks=system_blocks,
                user_id=user_id,
                brd_id=brd_id,
                gen_start_ts=gen_start_ts,
            ): n
            for n, _t, _s in BRD_SECTIONS
        }
        for fut in as_completed(futures):
            n = futures[fut]
            try:
                results[n] = fut.result()
                _write_generation_status_h(
                    brd_id, "running",
                    sections_complete=sorted(results.keys()),
                    session_id=session_id,
                )
            except Exception as e:
                logger.error(f"[BRD-hist] §{n} raised: {e}")

    elapsed  = round(time.time() - gen_start_ts, 2)
    sections = [results[n] for n, _t, _s in BRD_SECTIONS if n in results]
    missing  = [n for n, _t, _s in BRD_SECTIONS if n not in results]
    failed   = [s["number"] for s in sections if s.get("status") == "generation_failed"]

    # Aggregate token usage + log summary block (mirrors generator path).
    from lambda_brd_generator import _estimate_cost_usd
    agg = dict(prime_usage)
    section_durations: List[float] = []
    for s in sections:
        u = s.get("_usage") or {}
        for k in ("prompt", "completion", "cache_write", "cache_read"):
            agg[k] = agg.get(k, 0) + int(u.get(k, 0))
        if s.get("_duration_s") is not None:
            section_durations.append(float(s["_duration_s"]))
    cost = _estimate_cost_usd(
        prompt_tokens=agg["prompt"],
        completion_tokens=agg["completion"],
        cache_write_tokens=agg["cache_write"],
        cache_read_tokens=agg["cache_read"],
    )
    sum_section_seconds = round(sum(section_durations), 2) if section_durations else 0.0
    fanout_wall = round(time.time() - fanout_started, 2)
    parallelism_factor = (
        round(sum_section_seconds / fanout_wall, 2) if fanout_wall > 0 else 0.0
    )
    cache_hit_ratio = round(
        agg["cache_read"] / max(1, agg["cache_read"] + agg["cache_write"]), 3,
    )

    logger.info(
        f"[BRD-hist] ════ END parallel generation brd_id={brd_id} elapsed={elapsed}s ════"
    )
    logger.info(
        f"[BRD-hist] TOKEN SUMMARY  prompt={agg['prompt']} completion={agg['completion']} "
        f"cache_write={agg['cache_write']} cache_read={agg['cache_read']}"
    )
    logger.info(
        f"[BRD-hist] COST ESTIMATE  ${cost:.5f} per generation "
        f"(cache_hit_ratio={cache_hit_ratio})"
    )
    logger.info(
        f"[BRD-hist] PARALLELISM    sum_section_seconds={sum_section_seconds}s "
        f"fanout_wall={fanout_wall}s factor={parallelism_factor}× "
        f"(>1.0 means workers overlapped)"
    )
    logger.info(
        f"[BRD-hist] RESULT         {len(sections)}/{len(BRD_SECTIONS)} sections; "
        f"failed={failed}; missing={missing}"
    )

    # Strip debug fields from per-section payloads before serialising.
    for s in sections:
        s.pop("_usage", None)
        s.pop("_duration_s", None)

    if missing:
        terminal_status = "failed"
        error_msg = f"Sections missing from output: {missing}"
    elif failed:
        terminal_status = "partial"
        error_msg = f"Sections failed validation: {failed}"
    else:
        terminal_status = "complete"
        error_msg = None

    structure_payload = {
        "brd_id": brd_id,
        "session_id": session_id,
        "sections": sections,
        "generated_at": int(time.time()),
        "duration_seconds": elapsed,
        "mode": "parallel-history",
    }
    structure_key = f"brds/{brd_id}/brd_structure.json"
    try:
        s3_put_object(
            key=structure_key,
            body=json.dumps(structure_payload, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
    except Exception as e:
        logger.error(f"[BRD-hist parallel] brd_structure.json write failed: {e}")
        _write_generation_status_h(
            brd_id, "failed", error_message=f"S3 write failed: {e}",
            sections_complete=[s["number"] for s in sections],
            session_id=session_id,
        )
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"S3 write failed: {e}", "brd_id": brd_id}),
        }

    _write_generation_status_h(
        brd_id,
        terminal_status,
        sections_complete=[s["number"] for s in sections if s.get("status") == "llm_generated"],
        missing_sections=(missing + failed) or None,
        error_message=error_msg,
        session_id=session_id,
    )

    logger.info(
        f"[BRD-hist parallel] {terminal_status}: {len(sections)}/{len(BRD_SECTIONS)} "
        f"sections, failed={failed}, missing={missing}, elapsed={elapsed}s"
    )
    return {
        "statusCode": 200,
        "body": json.dumps({
            "brd_id":           brd_id,
            "session_id":       session_id,
            "status":           terminal_status,
            "section_count":    len(sections),
            "failed_sections":  failed,
            "missing_sections": missing,
            "s3_key":           structure_key,
            "duration_seconds": elapsed,
            "mode":             "parallel-history",
        }),
    }


def lambda_handler(event, context):
    """
    Lambda handler for BRD generation from conversation history.

    Two paths:
      - event["parallel"]=True  -> Phase 6 prime-then-fan-out flow
      - default                 -> legacy monolithic flow (single LLM call)

    Expected event:
    {
        "session_id": "analyst-session-xxx",
        "brd_id": "optional-brd-id",
        "parallel": True | False   # optional, opt-in to Phase 6 path
    }
    """
    logger.info("=== BRD from History Lambda Started ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    # Phase 6 opt-in path — orchestrator flips this when
    # BRD_USE_PARALLEL_GENERATION is on.
    if isinstance(event, dict) and event.get("parallel") is True:
        return _handle_parallel_history(event)

    try:
        # Extract inputs
        session_id = event.get('session_id')
        brd_id = event.get('brd_id')
        user_id = event.get('user_id')  # for token attribution
        
        if not session_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required field: session_id'
                })
            }
        
        # Generate BRD ID if not provided
        if not brd_id:
            brd_id = str(uuid.uuid4())
            logger.info(f"Generated new BRD ID: {brd_id}")
        else:
            logger.info(f"Using provided BRD ID: {brd_id}")
        
        # Step 1: Fetch conversation history from AgentCore Memory
        logger.info("Step 1: Fetching conversation history...")
        messages = get_conversation_history(session_id)
        
        if not messages:
            logger.warning("No conversation history found")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'No conversation history found for this session',
                    'message': 'Please have a conversation with the analyst first'
                })
            }
        
        # Step 2: Format conversation
        logger.info("Step 2: Formatting conversation...")
        conversation_text = format_conversation(messages)
        
        # Step 3: Fetch template from S3
        logger.info("Step 3: Fetching template from S3...")
        template_text = fetch_template_from_s3()
        
        # Step 4: Generate BRD using Bedrock
        logger.info("Step 4: Generating BRD with Bedrock...")
        brd_text = generate_brd_with_bedrock(template_text, conversation_text, user_id=user_id)
        
        # Step 5: Save BRD to S3
        logger.info("Step 5: Saving BRD to S3...")
        s3_locations = save_brd_to_s3(brd_text, brd_id)
        
        logger.info(f"BRD generation completed successfully. BRD ID: {brd_id}")
        
        # Return success
        return {
            'statusCode': 200,
            'body': json.dumps({
                'brd_id': brd_id,
                'message': 'BRD generated successfully from conversation history',
                'status': 'success',
                's3_location_txt': s3_locations['txt'],
                's3_location_json': s3_locations['json']
            })
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Error generating BRD from history'
            })
        }
