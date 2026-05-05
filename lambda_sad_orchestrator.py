"""
SAD Orchestrator Lambda
========================

Single Lambda that powers the entire SAD phase of the multi-session Design
Assistant. It dispatches on `event["action"]` to one of:

  turn            → unified chat box (intent router + per-intent handler)
  generate_sad    → parallel section drafting (streamed via SSE-style chunks
                    when invoked by the backend with InvocationType=RequestResponse
                    using a chunked-response shim, OR returned in one go)
  audit           → run all 10 audit prompts in parallel
  get_history     → list_events from AgentCore Memory
  revert_section  → pop the previous_versions stack on a section

State lives in:
  • AgentCore Memory (chat turns, actor_id="sad-session", session_id)
  • S3 (sad_structure.json, facts.json, audit_latest.json, sources/, diagrams/)
  • RDS (design_sessions row, owned by routers/design_sessions.py)

This Lambda doesn't touch RDS — the backend owns the design_sessions row and
flips stages when needed. The Lambda only reads/writes S3 + AgentCore Memory.
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from environment import (
    chat_completion,
    s3_put_object,
    S3_BUCKET_NAME,
    DEFAULT_AGENTCORE_MEMORY_ID,
)

# Per-section + per-intent prompts live in prompts/sad_*.py — imported lazily
# inside handlers so cold-start of a single action doesn't pull every prompt.

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AGENTCORE_MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", DEFAULT_AGENTCORE_MEMORY_ID)
# IMPORTANT: this must match `_DESIGN_ACTOR_ID` in routers/design_sessions.py.
# The history endpoint reads from `actor_id="design-session"`, so the Lambda
# must write under the same actor or `list_events` returns nothing. The env
# var name is preserved for backward compatibility.
SAD_ACTOR_ID = os.getenv("SAD_AGENTCORE_ACTOR_ID", "design-session")

MAX_PARALLEL_SECTION_WORKERS = int(os.getenv("SAD_MAX_PARALLEL_WORKERS", "5"))
ROUTER_MAX_TOKENS = int(os.getenv("SAD_ROUTER_MAX_TOKENS", "400"))
SECTION_MAX_TOKENS = int(os.getenv("SAD_SECTION_MAX_TOKENS", "4000"))
EDIT_MAX_TOKENS = int(os.getenv("SAD_EDIT_MAX_TOKENS", "3000"))

# The 10 Deluxe SAD sections, in order. The keys are stable identifiers used
# by per-section prompts and audit checks.
SAD_SECTIONS: List[Dict[str, Any]] = [
    {"number": 1, "key": "summary", "title": "Summary"},
    {"number": 2, "key": "problem_statement", "title": "Problem Statement"},
    {"number": 3, "key": "asr", "title": "Architectural Significant Requirements"},
    {"number": 4, "key": "logical_diagram", "title": "Logical Architecture Diagram"},
    {"number": 5, "key": "pending_decisions", "title": "Pending Decisions"},
    {"number": 6, "key": "security_view", "title": "Security View"},
    {"number": 7, "key": "infrastructure_diagram", "title": "Infrastructure Architecture Diagram"},
    {"number": 8, "key": "risks", "title": "Architecture Risks and Mitigations"},
    {"number": 9, "key": "nfrs", "title": "Non-Functional Requirements (NFRs)"},
    {"number": 10, "key": "cost", "title": "Infra Cost Estimate"},
]


# ============================================
# AWS clients (lazy)
# ============================================

_s3_client = None
_agentcore_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def _memory():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    return _agentcore_client


# ============================================
# S3 helpers (paths under sessions/{session_id}/sad/)
# ============================================

def _key(session_id: str, *parts: str) -> str:
    return "/".join(["sessions", session_id, *parts])


def _s3_get_json(key: str, default: Any = None) -> Any:
    try:
        obj = _s3().get_object(Bucket=S3_BUCKET_NAME, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.info(f"[SAD] _s3_get_json miss {key}: {e}")
        return default


def _s3_get_text(key: str, default: str = "") -> str:
    try:
        obj = _s3().get_object(Bucket=S3_BUCKET_NAME, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        logger.info(f"[SAD] _s3_get_text miss {key}: {e}")
        return default


def _s3_put_json(key: str, data: Any) -> None:
    s3_put_object(key=key, body=json.dumps(data, indent=2).encode("utf-8"), content_type="application/json")


def load_sad(session_id: str) -> Optional[Dict[str, Any]]:
    return _s3_get_json(_key(session_id, "sad", "sad_structure.json"))


def save_sad(session_id: str, sad: Dict[str, Any]) -> None:
    _s3_put_json(_key(session_id, "sad", "sad_structure.json"), sad)


def load_facts(session_id: str) -> Dict[str, Any]:
    return _s3_get_json(_key(session_id, "sad", "facts.json"), default={"sad_id": session_id, "facts": []})


def save_facts(session_id: str, facts: Dict[str, Any]) -> None:
    _s3_put_json(_key(session_id, "sad", "facts.json"), facts)


def load_diagram_xml(session_id: str, diagram_type: str = "logical") -> str:
    """Read a session's saved mxGraph XML for one diagram type.

    Empty string if missing. Always falls back to `logical.xml` if the
    requested type doesn't exist — prompt builders that ask for any diagram
    XML still get the best-available source on un-migrated sessions.
    """
    if diagram_type not in ("logical", "infrastructure", "security"):
        diagram_type = "logical"
    primary = _s3_get_text(_key(session_id, "diagram", f"{diagram_type}.xml"))
    if primary:
        return primary
    if diagram_type != "logical":
        # Per-type slot empty → fall back to logical so prompts that
        # reference 'the diagram' still see something useful.
        return _s3_get_text(_key(session_id, "diagram", "logical.xml"))
    return ""


# ============================================
# AgentCore Memory helpers (chat history)
# ============================================

def add_message_to_memory(session_id: str, role: str, content: str) -> None:
    """Append a user/assistant message to AgentCore Memory under this session."""
    if not AGENTCORE_MEMORY_ID:
        logger.warning("[SAD] AGENTCORE_MEMORY_ID not set; skipping memory write")
        return
    role_upper = role.upper() if role.upper() in ("USER", "ASSISTANT", "TOOL", "OTHER") else "ASSISTANT"
    try:
        _memory().create_event(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=SAD_ACTOR_ID,
            eventTimestamp=int(time.time()),
            payload=[{"conversational": {"role": role_upper, "content": {"text": content}}}],
        )
    except Exception as e:
        logger.warning(f"[SAD] add_message_to_memory failed for {session_id}: {e}")


def get_recent_history(session_id: str, max_messages: int = 30) -> List[Dict[str, str]]:
    """Return recent {role, content} pairs from AgentCore Memory for this session."""
    if not AGENTCORE_MEMORY_ID:
        return []
    try:
        resp = _memory().list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=SAD_ACTOR_ID,
            includePayloads=True,
            maxResults=max_messages,
        )
    except Exception as e:
        logger.warning(f"[SAD] get_recent_history failed for {session_id}: {e}")
        return []
    msgs: List[Dict[str, str]] = []
    for ev in resp.get("events", []):
        for item in ev.get("payload", []) or []:
            conv = item.get("conversational")
            if not conv:
                continue
            text = (conv.get("content") or {}).get("text") or ""
            role = (conv.get("role") or "ASSISTANT").lower()
            if text:
                msgs.append({"role": role, "content": text})
    return msgs


# ============================================
# Card helpers — agent → frontend response shape
# ============================================

def card(card_type: str, **payload) -> Dict[str, Any]:
    """Wrap a handler return value in the {type, payload} shape the frontend expects."""
    return {"type": card_type, "payload": payload}


def text_summary_for_memory(response: Dict[str, Any]) -> str:
    """Best-effort one-line summary of an assistant response for AgentCore Memory."""
    t = response.get("type", "text")
    p = response.get("payload", {})
    if t == "text":
        return p.get("text", "")[:500]
    if t == "fact_saved":
        return f"[fact saved] {p.get('text', '')[:200]}"
    if t == "doc_ingested":
        return f"[doc ingested] {p.get('filename', '?')}"
    if t == "section_view":
        return f"[shown section {p.get('n', '?')}]"
    if t == "section_updated":
        return f"[section {p.get('n', '?')} updated]"
    if t == "section_regenerated":
        return f"[section {p.get('n', '?')} regenerated]"
    if t == "audit":
        return f"[audit completed: {len(p.get('badges', []))} sections rated]"
    if t == "suggestions":
        return f"[{len(p.get('items', []))} suggestions returned]"
    if t == "generation_starting":
        return "[generation_starting]"
    return f"[{t}]"


# ============================================
# Action dispatch
# ============================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    action = (event or {}).get("action") or "turn"
    logger.info(f"[SAD] action={action}")

    handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
        "turn": handle_turn,
        "generate_sad": handle_generate_sad,
        "audit": handle_audit,
        "revert_section": handle_revert_section,
    }

    handler = handlers.get(action)
    if not handler:
        return {"statusCode": 400, "body": json.dumps({"error": f"unknown action: {action}"})}

    try:
        result = handler(event)
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as e:
        logger.exception(f"[SAD] handler {action} failed")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


# ============================================
# JSON parse helpers (LLMs occasionally wrap output in markdown fences)
# ============================================

def _extract_json(text: str) -> Any:
    """Best-effort JSON parse. Handles, in order:
      1. Plain JSON.
      2. Markdown-fenced JSON (```json ... ``` or ``` ... ```).
      3. Prose-wrapped JSON ("Here is the audit: { ... }. Hope this helps.").
         We scan brace/bracket pairs from the outside in and try each
         maximally-nested span until one parses. Object preferred over array
         because most of our handlers expect objects.
    """
    if not text:
        raise ValueError("empty response")
    s = text.strip()

    # 1. Strip markdown fences (multiple variants)
    if s.startswith("```"):
        # ```json\n...\n``` or ```\n...\n```
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        else:
            s = s.lstrip("`")
            if s.lower().startswith("json"):
                s = s[4:].lstrip()
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()

    # 2. Try direct parse
    try:
        return json.loads(s)
    except Exception:
        pass

    # 3. Prefer the OUTERMOST balanced object first, then array. We use
    # bracket-counting (not just first/last index) so we don't get tripped
    # up by quoted braces inside strings.
    for opener, closer in (("{", "}"), ("[", "]")):
        i = s.find(opener)
        if i < 0:
            continue
        depth = 0
        in_str = False
        escape = False
        for j in range(i, len(s)):
            ch = s[j]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = s[i:j + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
    # 4. Last-ditch: greedy first/last brace
    for opener, closer in (("{", "}"), ("[", "]")):
        i = s.find(opener)
        j = s.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    raise ValueError(f"could not extract JSON from response (starts: {s[:120]!r})")


# ============================================
# Lazy BRD loader — read from the project's brd_id if available
# ============================================

def _load_brd_for_session(session_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    """Try loading the project's BRD JSON for grounding; return empty dict if missing."""
    if not project_id:
        return {}
    # The BRD pipeline saves at brds/{brd_id}/brd_structure.json. We don't know
    # the brd_id from the Lambda, so the orchestrator backend is expected to
    # pass it via event["brd_id"] when generating. If not present, we just
    # return empty so the prompts can degrade gracefully.
    brd_id = None
    try:
        # Best-effort: many handler payloads carry brd_id alongside session_id.
        brd_id = (load_sad(session_id) or {}).get("brd_id")
    except Exception:
        pass
    if not brd_id:
        return {}
    try:
        obj = _s3().get_object(Bucket=S3_BUCKET_NAME, Key=f"brds/{brd_id}/brd_structure.json")
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.info(f"[SAD] no BRD JSON found for brd_id {brd_id}: {e}")
        return {}


# ============================================
# Intent router — single classifier LLM call
# ============================================

def run_intent_router(
    *,
    user_message: str,
    stage: str,
    sad_exists: bool,
    currently_viewing_section: Optional[int],
    file_attached: bool,
    last_assistant_card_type: Optional[str] = None,
    last_assistant_proposed_section: Optional[int] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    from prompts import get_router_system_prompt, build_router_prompt

    system_prompt = get_router_system_prompt()
    user_block = build_router_prompt(
        user_message=user_message,
        stage=stage,
        sad_exists=sad_exists,
        currently_viewing_section=currently_viewing_section,
        file_attached=file_attached,
        last_assistant_card_type=last_assistant_card_type,
        last_assistant_proposed_section=last_assistant_proposed_section,
    )

    raw = chat_completion(
        messages=[{"role": "user", "content": user_block}],
        temperature=0.0,
        max_tokens=ROUTER_MAX_TOKENS,
        system_prompt=system_prompt,
        user_id=user_id,
        token_source="lambda_sad_orchestrator:router",
    )
    try:
        parsed = _extract_json(raw)
    except Exception as e:
        logger.warning(f"[SAD] router JSON parse failed ({e}); raw='{raw[:200]}'")
        # Safe default: treat as ADD_INFO so we don't accidentally edit
        return {
            "intent": "ADD_INFO",
            "target_section": None,
            "fact": user_message[:500],
            "edit_instruction": "",
            "regen_proposed": False,
            "confidence": 0.0,
        }

    # Normalize fields
    parsed.setdefault("target_section", None)
    parsed.setdefault("fact", "")
    parsed.setdefault("edit_instruction", "")
    parsed.setdefault("regen_proposed", False)
    parsed.setdefault("confidence", 0.5)
    if parsed.get("target_section") in ("", "null", "None"):
        parsed["target_section"] = None
    return parsed


# ============================================
# Facts buffer helpers
# ============================================

def _new_fact(
    *,
    text: str,
    provenance: str,
    session_id: str,
    target_section: Optional[int],
    doc_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": f"fact-{uuid.uuid4().hex[:8]}",
        "text": text,
        "provenance": provenance,
        "session_id": session_id,
        "turn_ts": int(time.time()),
        "applied_to_sections": [],
        "suggested_section": target_section,
        "doc_id": doc_id,
        "status": "pending",
    }


def append_fact(session_id: str, fact: Dict[str, Any]) -> Dict[str, Any]:
    facts = load_facts(session_id)
    facts.setdefault("facts", []).append(fact)
    save_facts(session_id, facts)
    return facts


# ============================================
# Per-intent handlers
# ============================================

def handle_turn(event: Dict[str, Any]) -> Dict[str, Any]:
    """Single chat-box submission. Intent router → dispatched handler.

    Three input shapes (mutually exclusive in practice):
      1. `files: [{filename, extracted_text, doc_id}, ...]` — multi-doc
         ingest from pasted Confluence URLs in the message. Each file is
         pushed through `_do_ingest_doc`, one card per file is emitted,
         and only the LAST card has `auto_regen=true` so the frontend
         fires regeneration once after all are ingested. Bypasses the
         intent router entirely (we already know it's INGEST_DOC).
      2. `file: {filename, extracted_text}` — legacy single-file upload.
         Goes through the intent router (which recognises file_attached
         and routes to INGEST_DOC).
      3. Plain text (no file/files) — runs the intent router, dispatches
         to whichever handler.
    """
    session_id: str = event["session_id"]
    user_message: str = (event.get("message") or "").strip()
    project_id: Optional[str] = event.get("project_id")
    user_id: Optional[str] = event.get("user_id")
    viewing_section: Optional[int] = event.get("viewing_section")
    file_attached: bool = bool(event.get("file"))
    files_list: List[Dict[str, Any]] = event.get("files") or []
    stage: str = event.get("stage") or "SAD_GATHERING"
    last_card_type: Optional[str] = event.get("last_card_type")
    last_proposed_section: Optional[int] = event.get("last_proposed_section")

    if not user_message and not file_attached and not files_list:
        return card("text", text="(empty input)")

    # Persist the user's turn to memory once.
    if user_message:
        add_message_to_memory(session_id, "USER", user_message)

    # ── Path 1: multi-file ingest from Confluence URLs ─────────────────
    if files_list:
        cards: List[Dict[str, Any]] = []
        for i, file_info in enumerate(files_list):
            ingest_card = _do_ingest_doc(
                session_id, file_info, target_section=None, user_id=user_id,
            )
            # Only the LAST ingest gets auto_regen=true; without this, the
            # frontend would fire one regeneration per ingested doc.
            is_last = (i == len(files_list) - 1)
            payload = ingest_card.get("payload") or {}
            payload["auto_regen"] = bool(payload.get("auto_regen") and is_last)
            ingest_card["payload"] = payload
            cards.append(ingest_card)

        # Single assistant memory entry summarising the ingest.
        names = ", ".join(
            (c.get("payload") or {}).get("filename", "doc") for c in cards
        )
        add_message_to_memory(
            session_id,
            "ASSISTANT",
            f"Ingested {len(cards)} document(s): {names}",
        )
        return {"cards": cards}

    sad = load_sad(session_id)
    sad_exists = sad is not None

    intent_obj = run_intent_router(
        user_message=user_message or "[user attached a file]",
        stage=stage,
        sad_exists=sad_exists,
        currently_viewing_section=viewing_section,
        file_attached=file_attached,
        last_assistant_card_type=last_card_type,
        last_assistant_proposed_section=last_proposed_section,
        user_id=user_id,
    )
    intent = intent_obj.get("intent", "ADD_INFO")
    target_section = intent_obj.get("target_section")
    fact_text = intent_obj.get("fact") or user_message
    edit_instruction = intent_obj.get("edit_instruction") or user_message
    regen_proposed = bool(intent_obj.get("regen_proposed"))

    response: Dict[str, Any]
    if intent == "ADD_INFO":
        response = _do_add_info(session_id, fact_text, target_section, regen_proposed, user_id=user_id)
    elif intent == "INGEST_DOC":
        response = _do_ingest_doc(session_id, event.get("file") or {}, target_section, user_id=user_id)
    elif intent == "SHOW_SECTION":
        response = _do_show_section(session_id, target_section)
    elif intent == "EDIT_SECTION":
        response = _do_edit_section(session_id, target_section, edit_instruction, project_id=project_id, user_id=user_id)
    elif intent == "REGENERATE_SECTION":
        response = _do_regenerate_section(session_id, target_section, project_id=project_id, user_id=user_id)
    elif intent == "AUDIT":
        result = handle_audit({"session_id": session_id, "project_id": project_id, "user_id": user_id})
        response = card("audit", badges=result.get("badges", []), details=result.get("details", []))
    elif intent == "SUGGEST":
        response = _do_suggest(session_id, target_section, project_id=project_id, user_id=user_id)
    elif intent == "ASK_QUESTION":
        response = _do_ask_question(session_id, user_message, user_id=user_id)
    elif intent == "GENERATE_NEW_SAD":
        response = card("generation_starting", session_id=session_id)
    elif intent == "LINK_DIAGRAM":
        response = card("text", text="Re-linking the saved diagram is handled by the backend; this is a no-op in the Lambda for now.")
    else:
        response = card("text", text=f"Unhandled intent: {intent}")

    # Persist the assistant response summary back to memory
    add_message_to_memory(session_id, "ASSISTANT", text_summary_for_memory(response))
    return response


def _do_add_info(
    session_id: str,
    fact_text: str,
    target_section: Optional[int],
    regen_proposed: bool,
    *,
    user_id: Optional[str],
) -> Dict[str, Any]:
    fact = _new_fact(
        text=fact_text, provenance="chat",
        session_id=session_id, target_section=target_section,
    )
    append_fact(session_id, fact)

    # Optional: a Mary-style follow-up question to keep the conversation moving.
    follow_up = ""
    try:
        from prompts import SAD_GATHER_SYSTEM_PROMPT, build_gather_prompt
        facts = load_facts(session_id).get("facts", [])
        sections_with_facts = [
            f.get("suggested_section") for f in facts if f.get("suggested_section")
        ]
        history = get_recent_history(session_id, max_messages=12)
        last_assistant_questions = [
            m["content"] for m in history if m.get("role") == "assistant" and "?" in m.get("content", "")
        ]
        prompt = build_gather_prompt(
            new_fact=fact_text,
            facts_so_far=facts,
            sections_with_facts=[s for s in sections_with_facts if s],
            last_few_assistant_questions=last_assistant_questions,
        )
        follow_up = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=300,
            system_prompt=SAD_GATHER_SYSTEM_PROMPT,
            user_id=user_id,
            token_source="lambda_sad_orchestrator:gather",
        ).strip()
    except Exception as e:
        logger.warning(f"[SAD] gather follow-up failed (non-fatal): {e}")

    return card(
        "fact_saved",
        fact_id=fact["id"],
        text=fact_text,
        suggested_section=target_section,
        regen_proposed=regen_proposed,
        follow_up=follow_up,
    )


_DOC_RELEVANCE_SYSTEM_PROMPT = """\
You classify which sections of a Software Architecture Document an
ingested document is relevant to. The 10 sections of the Deluxe SAD are:

  1.  Summary — one-paragraph project framing.
  2.  Problem Statement — why this is being built.
  3.  Architecturally Significant Requirements (ARSR) — frontend tech, API
      decisions, data storage, auth, scalability, deployment, backup,
      monitoring, DR, load balancing, agent runtime, processing layer,
      AI/LLM, object storage, API protection, IAM, networking.
  4.  Logical Architecture Diagram & flow narrative.
  5.  Pending Decisions — open questions, undecided choices, TBDs.
  6.  Security View — encryption, KMS, TLS, IAM, OIDC, secrets, network
      isolation, compliance posture.
  7.  Infrastructure — VPC layout, subnets, regions, deployment topology.
  8.  Risks & Mitigations — failure modes, dependencies, hallucination,
      cost overrun, vendor lock-in.
  9.  Non-Functional Requirements — measurable NFRs (performance,
      scalability, security NFR, maintainability, observability, backup, DR).
  10. Cost Estimate — pricing, budget, AWS calculator URL.

Output ONLY this JSON object:

  {"sections": [<int>, ...]}

Up to 5 sections, ranked by relevance (most relevant first). Empty
list when the content doesn't clearly map to any section.
"""


def _classify_doc_relevance(
    filename: str,
    text: str,
    user_id: Optional[str],
) -> List[int]:
    """Quick LLM classification of which SAD sections an ingested document
    is relevant to. Cheap (~500 input tokens, ~50 output) and runs once
    per ingest. Used to populate `suggested_sections` on the doc_ingested
    card so the user sees "Looks relevant to Sections 3, 5, 8" upfront.

    Returns a list of section numbers (1-10), at most 5, in relevance
    order. Empty on failure or when the content doesn't fit any section.
    """
    if not text or not text.strip():
        return []
    excerpt = text[:4000]
    user_block = f"Filename: {filename}\n\nDocument excerpt:\n{excerpt}"
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": user_block}],
            temperature=0.0,
            max_tokens=200,
            system_prompt=_DOC_RELEVANCE_SYSTEM_PROMPT,
            user_id=user_id,
            token_source="lambda_sad_orchestrator:doc_relevance",
        )
        parsed = _extract_json(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("sections"), list):
            out: List[int] = []
            for n in parsed["sections"]:
                try:
                    n_int = int(n)
                except (TypeError, ValueError):
                    continue
                if 1 <= n_int <= 10 and n_int not in out:
                    out.append(n_int)
            return out[:5]
    except Exception as e:
        logger.warning(f"[SAD] doc relevance classify failed (non-fatal): {e}")
    return []


def _do_ingest_doc(
    session_id: str,
    file_info: Dict[str, Any],
    target_section: Optional[int],
    *,
    user_id: Optional[str],
) -> Dict[str, Any]:
    """File upload — the backend has already extracted text and put it in S3.

    Expected file_info shape (from backend):
      { "filename": "...", "doc_id": "...", "extracted_text": "..." }

    Behaviour:
      • Persist the FULL extracted text as a fact (capped at MAX_DOC_FACT_CHARS
        as a runaway-prevention bound). The previous 500-char cap meant most
        of an uploaded SAD/PDF was silently dropped.
      • Store the fact with suggested_section=None so it's visible to ALL ten
        section workers during regen — the router's per-doc target_section
        guess is too coarse: a doc usually has content for several sections.
        We still surface that guess in the response card as a UX hint
        ("Looks relevant to Section X").
      • If a SAD already exists, set auto_regen=true so the frontend fires
        regen automatically — the user doesn't have to ask.
    """
    filename = file_info.get("filename", "uploaded-doc")
    doc_id = file_info.get("doc_id")
    extracted_text = (file_info.get("extracted_text") or "").strip()

    # Generous cap — Claude Sonnet 4.5 has a 200K-token context window, so
    # ~50K chars per doc keeps us well under even when several docs and the
    # full BRD + diagram XML are also in the prompt.
    MAX_DOC_FACT_CHARS = 50_000
    truncated = len(extracted_text) > MAX_DOC_FACT_CHARS
    body = extracted_text[:MAX_DOC_FACT_CHARS] if truncated else extracted_text

    if body:
        fact_text = f"[Uploaded document: {filename}]\n{body}"
        if truncated:
            fact_text += (
                f"\n[... document truncated at {MAX_DOC_FACT_CHARS} chars; "
                f"original is {len(extracted_text)} chars]"
            )
    else:
        fact_text = f"[Uploaded document: {filename}] (no extractable text)"

    fact = _new_fact(
        text=fact_text,
        provenance=f"doc:{filename}",
        session_id=session_id,
        # target_section=None on purpose — see docstring.
        target_section=None,
        doc_id=doc_id,
    )
    append_fact(session_id, fact)

    sad_exists = load_sad(session_id) is not None

    # Classify which sections this doc is most relevant to. Best-effort —
    # one LLM call (~200ms), failures are logged and fall through to an
    # empty list (the card just won't show the "Looks relevant to..." hint).
    suggested_sections = _classify_doc_relevance(filename, body, user_id) if body else []

    logger.info(
        f"[SAD] doc ingested: {filename} ({len(extracted_text)} chars, "
        f"{'truncated' if truncated else 'whole'}); auto_regen={sad_exists}; "
        f"suggested_sections={suggested_sections}"
    )

    return card(
        "doc_ingested",
        fact_id=fact["id"],
        filename=filename,
        # Legacy single-section hint (router's guess at intent-routing time).
        suggested_section=target_section,
        # New: classifier-derived list of sections this doc most likely
        # contributes to. Frontend renders as "Looks relevant to Sections X, Y, Z".
        suggested_sections=suggested_sections,
        auto_regen=sad_exists,
    )


def _do_show_section(session_id: str, section_n: Optional[int]) -> Dict[str, Any]:
    if not section_n:
        return card("text", text="Which section would you like to see? (1-10)")
    sad = load_sad(session_id)
    if not sad:
        return card("text", text="No SAD has been generated yet for this session.")
    secs = sad.get("sections", [])
    if not (1 <= section_n <= len(secs)):
        return card("text", text=f"Section {section_n} doesn't exist.")
    section = secs[section_n - 1]
    return card("section_view", n=section["number"], title=section["title"], content=section.get("content", []))


def _do_edit_section(
    session_id: str,
    section_n: Optional[int],
    instruction: str,
    *,
    project_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    if not section_n:
        return card("text", text="Which section did you mean to edit? (1-10)")
    sad = load_sad(session_id)
    if not sad:
        return card("text", text="No SAD draft exists yet — generate one first.")
    secs = sad.get("sections", [])
    if not (1 <= section_n <= len(secs)):
        return card("text", text=f"Section {section_n} doesn't exist.")
    section = secs[section_n - 1]

    from prompts import EDIT_SYSTEM_PROMPT, build_edit_prompt

    diagram_xml = load_diagram_xml(session_id) if section_n in (4, 6, 7) else ""
    brd = _load_brd_for_session(session_id, project_id)
    brd_excerpt = ""
    try:
        from prompts.sad_section_prompts import _format_brd_excerpt as _fmt
        brd_excerpt = _fmt(brd) if brd else ""
    except Exception:
        pass

    prompt = build_edit_prompt(
        section_number=section["number"],
        section_title=section["title"],
        current_content=section.get("content", []),
        user_instruction=instruction,
        brd_excerpt=brd_excerpt,
        diagram_xml=diagram_xml,
    )
    raw = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=EDIT_MAX_TOKENS,
        system_prompt=EDIT_SYSTEM_PROMPT,
        user_id=user_id,
        token_source="lambda_sad_orchestrator:edit",
    )
    try:
        new_content = _extract_json(raw)
        if not isinstance(new_content, list):
            raise ValueError("edit returned non-array")
    except Exception as e:
        logger.warning(f"[SAD] edit JSON parse failed: {e}; raw={raw[:300]}")
        return card("text", text=f"I tried to apply that edit but couldn't parse the result. ({e})")

    # Push previous version onto the section's revert stack
    section.setdefault("previous_versions", [])
    section["previous_versions"].insert(0, section.get("content", []))
    section["previous_versions"] = section["previous_versions"][:3]
    section["content"] = new_content
    section["status"] = "user_edited"
    section["last_modified_ts"] = int(time.time())
    save_sad(session_id, sad)
    return card("section_updated", n=section["number"], title=section["title"], content=new_content)


def _do_regenerate_section(
    session_id: str,
    section_n: Optional[int],
    *,
    project_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    if not section_n:
        return card("text", text="Which section should I regenerate? (1-10)")
    sad = load_sad(session_id)
    if not sad:
        return card("text", text="No SAD draft exists yet — generate one first.")
    secs = sad.get("sections", [])
    if not (1 <= section_n <= len(secs)):
        return card("text", text=f"Section {section_n} doesn't exist.")
    section = secs[section_n - 1]

    new_content = _generate_section_content(
        session_id=session_id,
        project_id=project_id,
        user_id=user_id,
        section_number=section_n,
        previous_content=section.get("content") or None,
    )
    if new_content is None:
        return card("text", text=f"I couldn't regenerate section {section_n}. Try again or simplify the instruction.")

    section.setdefault("previous_versions", [])
    section["previous_versions"].insert(0, section.get("content", []))
    section["previous_versions"] = section["previous_versions"][:3]
    section["content"] = new_content
    section["status"] = "regenerated"
    section["last_modified_ts"] = int(time.time())

    # Mark facts targeting this section as applied
    facts_obj = load_facts(session_id)
    for f in facts_obj.get("facts", []):
        if f.get("suggested_section") == section_n and f.get("status") != "applied":
            f["status"] = "applied"
            f.setdefault("applied_to_sections", []).append(section_n)
    save_facts(session_id, facts_obj)
    save_sad(session_id, sad)

    return card("section_regenerated", n=section["number"], title=section["title"], content=new_content)


def _do_suggest(
    session_id: str,
    section_n: Optional[int],
    *,
    project_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    sad = load_sad(session_id)
    if not sad:
        return card("text", text="No SAD draft exists yet — generate one first.")
    secs = sad.get("sections", [])
    target_idx = (section_n - 1) if (section_n and 1 <= section_n <= len(secs)) else None
    if target_idx is None:
        # Pick the section with the lowest audit score, fall back to section 1.
        worst = None
        for s in secs:
            score = (s.get("audit") or {}).get("score", 100)
            if worst is None or score < worst[0]:
                worst = (score, s)
        target_idx = secs.index(worst[1]) if worst else 0

    section = secs[target_idx]

    from prompts import SUGGEST_SYSTEM_PROMPT, build_suggest_prompt
    brd = _load_brd_for_session(session_id, project_id)
    brd_excerpt = ""
    try:
        from prompts.sad_section_prompts import _format_brd_excerpt as _fmt
        brd_excerpt = _fmt(brd) if brd else ""
    except Exception:
        pass

    prompt = build_suggest_prompt(
        section_number=section["number"],
        section_title=section["title"],
        current_content=section.get("content", []),
        audit_issues=(section.get("audit") or {}).get("issues", []),
        brd_excerpt=brd_excerpt,
    )
    raw = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=900,
        system_prompt=SUGGEST_SYSTEM_PROMPT,
        user_id=user_id,
        token_source="lambda_sad_orchestrator:suggest",
    )
    try:
        parsed = _extract_json(raw)
        items = parsed.get("items", []) if isinstance(parsed, dict) else []
    except Exception as e:
        logger.warning(f"[SAD] suggest JSON parse failed: {e}")
        items = []
    return card(
        "suggestions",
        n=section["number"],
        title=section["title"],
        items=items,
    )


def _do_ask_question(session_id: str, question: str, *, user_id: Optional[str]) -> Dict[str, Any]:
    sad = load_sad(session_id)
    if not sad:
        return card("text", text="No SAD draft exists yet to answer questions about.")

    # Naive RAG over SAD: dumbly include all sections; the LLM will self-filter.
    from prompts import QA_SYSTEM_PROMPT, build_qa_prompt
    sections = sad.get("sections", [])
    prompt = build_qa_prompt(question=question, relevant_sections=sections)
    raw = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=900,
        system_prompt=QA_SYSTEM_PROMPT,
        user_id=user_id,
        token_source="lambda_sad_orchestrator:qa",
    )
    try:
        parsed = _extract_json(raw)
        return card(
            "text",
            text=parsed.get("answer", ""),
            citations=parsed.get("citations", []),
        )
    except Exception:
        return card("text", text=raw[:1000])


# SAD-redesign: which diagram type lands in each section.
# §4 Logical Architecture, §6 Security View, §7 Infrastructure Architecture.
_SECTION_DIAGRAM_TYPE: Dict[int, str] = {
    4: "logical",
    6: "security",
    7: "infrastructure",
}

_DIAGRAM_TYPE_LABEL: Dict[str, str] = {
    "logical": "Logical architecture diagram",
    "security": "Security architecture diagram",
    "infrastructure": "Infrastructure architecture diagram",
}


def _diagram_block_for_section(
    session_id: str,
    section_number: int,
    diagram_slots: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Pick the artifact to embed in §4/§6/§7, or return a placeholder.

    Honours P3 — each saved diagram lands in its own section. A skipped or
    missing slot renders an explicit placeholder paragraph, never silently
    substitutes the Logical artifact.
    """
    diagram_type = _SECTION_DIAGRAM_TYPE.get(section_number)
    if not diagram_type:
        return None  # not a diagram-bearing section

    slot = (diagram_slots or {}).get(diagram_type) or {}
    status = slot.get("status", "pending")

    # Done / skipped_saved: use the slot's artifact_key if it has one;
    # otherwise fall through to the per-type S3 path so the legacy single-
    # slot behaviour stays intact for un-migrated sessions.
    if status in ("done", "skipped_saved") and slot.get("artifact_key"):
        return {
            "type": "diagram",
            "s3_key": slot["artifact_key"],
            "alt": _DIAGRAM_TYPE_LABEL.get(diagram_type, "Architecture diagram"),
        }

    # Anything other than Done with an artifact: explicit placeholder
    # paragraph. The SAD viewer renders this as italic prose; never as a
    # broken `<img>` pointing at an S3 key we know is empty.
    label = _DIAGRAM_TYPE_LABEL.get(diagram_type, diagram_type.title())

    if status in ("skipped", "skipped_saved"):
        return {
            "type": "paragraph",
            "text": (
                f"_{label} skipped for this SAD. Open the Diagram hub in Velox "
                f"and author this view to include it on regeneration._"
            ),
        }

    if status == "failed":
        return {
            "type": "paragraph",
            "text": (
                f"_{label} authoring failed and was not included. Retry from "
                f"the Diagram hub in Velox._"
            ),
        }

    # Pending / in_progress: not authored yet. Same placeholder shape as
    # the skipped case — different copy, same trust contract: never a
    # diagram block when nothing is saved.
    return {
        "type": "paragraph",
        "text": (
            f"_{label} not yet authored. Open the Diagram hub in Velox and "
            f"author this view to include it on regeneration._"
        ),
    }


def _generate_section_content(
    *,
    session_id: str,
    project_id: Optional[str],
    user_id: Optional[str],
    section_number: int,
    previous_content: Optional[List[Dict[str, Any]]] = None,
    diagram_slots: Optional[Dict[str, Any]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Run one section's drafting prompt; return its content array.

    When `previous_content` is supplied (regeneration), the section's existing
    content is rendered into the prompt so the LLM can merge new inputs with
    what's already there per the system prompt's regeneration rules.

    `diagram_slots` is the per-type slot snapshot (passed in by the FastAPI
    router from `db_helper.get_diagram_slots`). Sections 4/6/7 use it to
    pick the matching artifact — never substitute one type for another.
    """
    from prompts import SECTION_SYSTEM_PROMPT, SECTION_PROMPT_BUILDERS

    builder = SECTION_PROMPT_BUILDERS.get(section_number)
    if not builder:
        return None

    brd = _load_brd_for_session(session_id, project_id)
    facts = load_facts(session_id).get("facts", [])
    # Diagram XML is fed to section prompts that reference it (3, 4, 6, 7,
    # 8). Pick the type matching the section so per-type prompts get the
    # right XML; sections without a matching slot fall back to the logical
    # artifact (legacy behaviour).
    section_type = _SECTION_DIAGRAM_TYPE.get(section_number, "logical")
    diagram_xml = load_diagram_xml(session_id, diagram_type=section_type)
    rag: List[Dict[str, Any]] = []  # backend can pre-populate via event["rag_chunks"] if available

    # Build kwargs based on what each builder expects (introspect signature loosely).
    kwargs: Dict[str, Any] = {}
    code = builder.__code__.co_varnames
    if "brd" in code: kwargs["brd"] = brd
    if "facts" in code: kwargs["facts"] = facts
    if "diagram_xml" in code: kwargs["diagram_xml"] = diagram_xml
    if "rag" in code: kwargs["rag"] = rag
    if "previous_content" in code: kwargs["previous_content"] = previous_content

    user_block = builder(**kwargs)
    raw = chat_completion(
        messages=[{"role": "user", "content": user_block}],
        temperature=0.3,
        max_tokens=SECTION_MAX_TOKENS,
        system_prompt=SECTION_SYSTEM_PROMPT,
        user_id=user_id,
        token_source=f"lambda_sad_orchestrator:section{section_number}",
    )
    try:
        content = _extract_json(raw)
        if not isinstance(content, list):
            raise ValueError("section worker returned non-array")
        # Sections 4, 6, 7 prepend a diagram block (or skipped placeholder)
        # honouring the per-type slot mapping P3 promises.
        if section_number in _SECTION_DIAGRAM_TYPE:
            block = _diagram_block_for_section(session_id, section_number, diagram_slots)
            if block:
                content = [block] + content
        return content
    except Exception as e:
        logger.warning(f"[SAD] section {section_number} JSON parse failed: {e}; raw={raw[:300]}")
        return [{"type": "paragraph", "text": "(generation failed — please regenerate this section)"}]


def handle_generate_sad(event: Dict[str, Any]) -> Dict[str, Any]:
    """Run all 10 section workers in parallel, persist sad_structure.json.

    Behaviour split:
      • First-time generation (no SAD on disk): build fresh skeleton, run
        each worker with no prior content.
      • Regeneration (SAD already exists): keep the existing skeleton +
        previous_versions stack, push the current content of each section
        into previous_versions, then call workers WITH previous_content
        so the LLM merges new inputs into what's already there.
    """
    session_id: str = event["session_id"]
    project_id: Optional[str] = event.get("project_id")
    user_id: Optional[str] = event.get("user_id")
    brd_id: Optional[str] = event.get("brd_id")
    # Per-type slot snapshot from the FastAPI router. Shape:
    #   {"logical": {...}, "infrastructure": {...}, "security": {...}}
    # When absent (legacy callers, direct Lambda invokes), section workers
    # fall back to the logical S3 path — same as the pre-redesign behaviour.
    diagram_slots: Dict[str, Any] = event.get("diagram_slots") or {}

    started = time.time()

    existing = load_sad(session_id)
    previous_by_n: Dict[int, List[Dict[str, Any]]] = {}

    if existing and existing.get("sections"):
        # Regeneration path — preserve previous_versions, snapshot current
        # content into it, and pass current content to each worker.
        sad = dict(existing)
        sad["stage"] = "SAD_GENERATING"
        sad["template_version"] = sad.get("template_version", "Deluxe_SAD_v1")
        sad["sad_id"] = sad.get("sad_id", session_id)
        sad["project_id"] = sad.get("project_id", project_id)
        sad["brd_id"] = sad.get("brd_id", brd_id)
        sad["diagram_source"] = sad.get("diagram_source") or {
            "logical_xml_s3_key": _key(session_id, "diagram", "logical.xml"),
            "logical_svg_s3_key": _key(session_id, "diagram", "logical.svg"),
        }
        for sec in sad["sections"]:
            n = sec.get("number")
            current = sec.get("content") or []
            if current:
                previous_by_n[n] = current
                stack = sec.setdefault("previous_versions", [])
                # Push the PLAIN block array — same shape the revert handler
                # restores, same shape /save-section pushes. We used to wrap
                # in {ts, reason, content} for telemetry; the wrapper made
                # revert restore the envelope as section.content and the
                # frontend's RenderBlock crashed because dict has no .map.
                stack.append(current)
                # Cap the history so the JSON doesn't grow without bound.
                if len(stack) > 5:
                    sec["previous_versions"] = stack[-5:]
    else:
        # First-time generation — fresh skeleton.
        sad = {
            "sad_id": session_id,
            "project_id": project_id,
            "brd_id": brd_id,
            "template_version": "Deluxe_SAD_v1",
            "stage": "SAD_GENERATING",
            "diagram_source": {
                "logical_xml_s3_key": _key(session_id, "diagram", "logical.xml"),
                "logical_svg_s3_key": _key(session_id, "diagram", "logical.svg"),
            },
            "sections": [
                {
                    "number": s["number"],
                    "title": s["title"],
                    "content": [],
                    "status": "auto_drafted",
                    "previous_versions": [],
                }
                for s in SAD_SECTIONS
            ],
        }

    # Persist the (possibly-existing) skeleton early so any subsequent partial
    # failures are recoverable.
    save_sad(session_id, sad)

    def _worker(n: int) -> Tuple[int, List[Dict[str, Any]]]:
        return n, _generate_section_content(
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
            section_number=n,
            previous_content=previous_by_n.get(n),
            diagram_slots=diagram_slots,
        ) or []

    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SECTION_WORKERS) as ex:
        futures = [ex.submit(_worker, s["number"]) for s in SAD_SECTIONS]
        for fut in as_completed(futures):
            try:
                n, content = fut.result()
            except Exception as e:
                logger.error(f"[SAD] section worker exploded: {e}")
                continue
            sad["sections"][n - 1]["content"] = content
            sad["sections"][n - 1]["last_modified_ts"] = int(time.time())
            save_sad(session_id, sad)  # write-through after each section
            completed += 1

    sad["stage"] = "SAD_REFINING"
    save_sad(session_id, sad)
    logger.info(f"[SAD] generated {completed}/10 sections for {session_id} in {time.time() - started:.1f}s")
    return {"sad_id": session_id, "sections_completed": completed, "duration_s": round(time.time() - started, 1)}


def handle_audit(event: Dict[str, Any]) -> Dict[str, Any]:
    """Run audit prompts. If `section_number` is in the event, only that
    section is audited (one LLM call). Otherwise all 10 sections are audited
    in parallel. The decorated sad_structure.json is persisted either way."""
    session_id: str = event["session_id"]
    project_id: Optional[str] = event.get("project_id")
    user_id: Optional[str] = event.get("user_id")
    section_filter: Optional[int] = event.get("section_number")

    sad = load_sad(session_id)
    if not sad:
        return {"badges": [], "details": [], "error": "no SAD"}

    from prompts import AUDIT_SYSTEM_PROMPT, build_audit_prompt
    from prompts.sad_section_prompts import (
        ARSR_IN_SCOPE_CATEGORIES,
        ARSR_OUT_OF_SCOPE_CATEGORIES,
    )

    diagram_xml = load_diagram_xml(session_id)
    brd = _load_brd_for_session(session_id, project_id)
    brd_excerpt = ""
    try:
        from prompts.sad_section_prompts import _format_brd_excerpt as _fmt
        brd_excerpt = _fmt(brd) if brd else ""
    except Exception:
        pass

    AUDIT_RETRY_SUFFIX = (
        "\n\nIMPORTANT — your previous response could not be parsed. Reply "
        "with ONLY a single JSON object, no prose before or after, no "
        "markdown fences. The object MUST have exactly two keys: "
        '"score" (integer 0-100) and "issues" (array of {"code", "msg"} '
        "objects).\n"
    )

    def _normalize_audit_payload(parsed: Any) -> Dict[str, Any]:
        """Coerce common LLM output shapes into the canonical
        {score: int, issues: [{code, msg}]} dict.

        Recovers from:
          • A bare list of issues   → {score: derived, issues: list}
            (score = 100 - 10 * min(len(list), 5))
          • An object that nests under "audit" / "result" / "data".
        """
        # Unwrap one level of envelope objects we've seen the LLM produce.
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
            return {
                "score": int(parsed.get("score", 0)),
                "issues": parsed.get("issues", []) or [],
            }

        raise ValueError(f"audit returned unrecognized shape: {type(parsed).__name__}")

    def _audit_worker(section: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        n = section["number"]
        required = None
        if n == 3:
            required = ARSR_IN_SCOPE_CATEGORIES + ARSR_OUT_OF_SCOPE_CATEGORIES
        prompt = build_audit_prompt(
            section_number=n,
            section_title=section["title"],
            section_content=section.get("content", []),
            brd_excerpt=brd_excerpt,
            diagram_xml=diagram_xml if n in (4, 6, 7) else "",
            required_categories=required,
        )

        last_raw: str = ""
        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                raw = chat_completion(
                    messages=[{"role": "user", "content": prompt + (AUDIT_RETRY_SUFFIX if attempt == 2 else "")}],
                    temperature=0.0,
                    max_tokens=1500,
                    system_prompt=AUDIT_SYSTEM_PROMPT,
                    user_id=user_id,
                    token_source=f"lambda_sad_orchestrator:audit{n}",
                )
                last_raw = raw
                parsed = _extract_json(raw)
                normalized = _normalize_audit_payload(parsed)
                if attempt == 2:
                    logger.info(f"[SAD] audit section {n} recovered on retry")
                return n, normalized
            except Exception as e:
                last_err = e
                logger.warning(
                    f"[SAD] audit section {n} attempt {attempt} parse failed: {e} | "
                    f"raw[:400]={raw[:400] if (raw := last_raw) else '(no response)'!r}"
                )

        # Both attempts failed. Surface the failure with the raw snippet so
        # CloudWatch shows what came back.
        snippet = (last_raw or "").strip()[:300].replace("\n", " ")
        logger.error(
            f"[SAD] audit section {n} unrecoverable: {last_err} "
            f"raw_snippet={snippet!r}"
        )
        return n, {
            "score": 0,
            "issues": [{
                "code": "FORMAT_VIOLATION",
                "msg": f"Auditor returned malformed output after retry: {last_err}".strip(),
            }],
        }

    sections_all = sad.get("sections", [])
    if section_filter:
        sections_to_audit = [s for s in sections_all if s.get("number") == section_filter]
        if not sections_to_audit:
            return {"badges": [], "details": [], "error": f"section {section_filter} not found"}
    else:
        sections_to_audit = sections_all

    results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SECTION_WORKERS) as ex:
        futures = [ex.submit(_audit_worker, s) for s in sections_to_audit]
        for fut in as_completed(futures):
            try:
                n, payload = fut.result()
                results[n] = payload
            except Exception as e:
                logger.error(f"[SAD] audit worker exploded: {e}")

    # Decorate sad_structure with audit results — only update the sections we
    # actually audited; leave others' existing `audit` field intact.
    badges: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    for s in sections_all:
        n = s["number"]
        if n in results:
            r = results[n]
            s["audit"] = r
            s["status"] = "flagged" if r["score"] < 80 else s.get("status", "auto_drafted")
        elif "audit" in s:
            r = s["audit"]
        else:
            continue
        badge_icon = "✅" if r["score"] >= 90 else ("⚠️" if r["score"] >= 60 else "🚫")
        badges.append({"n": n, "title": s["title"], "score": r["score"], "icon": badge_icon})
        details.append({"n": n, "title": s["title"], "issues": r.get("issues", [])})

    save_sad(session_id, sad)
    _s3_put_json(_key(session_id, "sad", "audit_latest.json"),
                 {"badges": badges, "details": details, "ts": int(time.time())})
    return {"badges": badges, "details": details}


def handle_revert_section(event: Dict[str, Any]) -> Dict[str, Any]:
    session_id: str = event["session_id"]
    section_n: int = int(event.get("section_number") or 0)
    sad = load_sad(session_id)
    if not sad:
        return {"reverted": False, "error": "no SAD"}
    secs = sad.get("sections", [])
    if not (1 <= section_n <= len(secs)):
        return {"reverted": False, "error": f"section {section_n} out of range"}
    section = secs[section_n - 1]
    versions = section.get("previous_versions", [])
    if not versions:
        return {"reverted": False, "error": "no previous version"}
    # Pop most recent prior content
    prev = versions.pop(0)

    # Self-heal: older sessions have entries shaped like
    # {"ts": ..., "reason": "regen-merge", "content": [<blocks>]}.
    # The revert handler used to assign the envelope directly to
    # section.content, which crashed RenderBlock (dict has no .map).
    # If we see that shape, unwrap it before assigning.
    if isinstance(prev, dict) and isinstance(prev.get("content"), list):
        prev = prev["content"]

    # Final guard: if it's still not a list, treat as no-op (caller
    # surfaces the error to the user instead of corrupting the section).
    if not isinstance(prev, list):
        return {"reverted": False, "error": "previous version is malformed"}

    section["previous_versions"] = versions
    section["content"] = prev
    section["status"] = "user_edited"
    section["last_modified_ts"] = int(time.time())
    save_sad(session_id, sad)
    return {"reverted": True, "n": section_n, "content": prev}
