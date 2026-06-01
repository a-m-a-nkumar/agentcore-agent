"""
BRD ↔ Code Summary comparison router.

Two-step flow:
  POST /api/brd-sync/compare  — fetch both pages, ask the LLM for structured
                                ADD / MODIFY / REMOVE suggestions per section.
  POST /api/brd-sync/apply    — given the subset of suggestions the human
                                approved, ask the LLM to regenerate the full
                                merged BRD with those changes baked in, then
                                overwrite the BRD's Confluence page.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
import logging
import json
import os
import re
import threading
import uuid
from html import unescape

from auth import verify_azure_token
from db_helper import get_user_atlassian_credentials, create_or_update_user, get_project, track_event
from services.confluence_service import ConfluenceService
from services.lineage_service import record_lineage
from utils.content_hashing import hash_text
from environment import chat_completion

router = APIRouter(prefix="/api/brd-sync", tags=["brd-sync"])
logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")


# ── Auth dependency (same shape as test_generation.py) ───────────────────────

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")
    try:
        return create_or_update_user(user_id, email, name)
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ── Request / response models ────────────────────────────────────────────────

ChangeType = Literal["ADD", "MODIFY", "REMOVE"]


class CompareRequest(BaseModel):
    project_id: str
    code_summary_page_id: str = Field(..., description="Confluence page ID of the published code summary")
    brd_page_id: str = Field(..., description="Confluence page ID of the BRD to update")


class Suggestion(BaseModel):
    id: str
    change_type: ChangeType
    section: str               # human-readable section path, e.g. "5. External Integrations"
    current_text: Optional[str] = None   # null for ADD
    proposed_text: Optional[str] = None  # null for REMOVE
    reason: str


class CompareResponse(BaseModel):
    brd_page_id: str
    brd_title: str
    code_summary_page_id: str
    code_summary_title: str
    suggestions: List[Suggestion]


class ApplyRequest(BaseModel):
    project_id: str
    code_summary_page_id: str
    brd_page_id: str
    approved_suggestions: List[Suggestion]


class ApplyResponse(BaseModel):
    page_id: str
    title: str
    web_url: str
    version: int
    applied: int


# ── HTML → plain text helper ─────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    if not html:
        return ""
    cleaned = re.sub(r"<ac:structured-macro[\s\S]*?</ac:structured-macro>", " ", html)
    cleaned = re.sub(r"<ac:adf-extension[\s\S]*?</ac:adf-extension>", " ", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


# ── Prompt builders ──────────────────────────────────────────────────────────

def _build_compare_prompt(code_summary: str, brd: str, code_summary_title: str, brd_title: str) -> str:
    return f"""You are an analyst reconciling a Business Requirements Document (BRD) with a Code Summary that describes the system as it actually exists today.

CODE SUMMARY (ground truth — what the code does now)
TITLE: {code_summary_title}
---
{code_summary}
---

BRD (the document to update)
TITLE: {brd_title}
---
{brd}
---

Task: produce a list of concrete suggestions that would bring the BRD into alignment with the code summary. Each suggestion must be one of:
  - ADD     → information present in the code summary but missing from the BRD
  - MODIFY  → information in the BRD that contradicts the code summary
  - REMOVE  → information in the BRD that no longer applies according to the code summary

Rules:
1. Be precise. Quote the BRD text you want to change verbatim in `current_text`.
2. For ADD suggestions, `current_text` is null and `proposed_text` is the new content.
3. For REMOVE suggestions, `proposed_text` is null.
4. `section` is the human-readable BRD section path, e.g. "5. External Integrations" or "User Story 3.2".
5. `reason` is one sentence explaining why this change reflects the code.
6. Do NOT invent code behavior that isn't in the code summary.
7. Do NOT suggest stylistic edits — only semantic mismatches.
8. Skip sections that already agree.

Return ONLY valid JSON in this exact shape, no prose, no markdown fences:
{{
  "suggestions": [
    {{
      "change_type": "ADD" | "MODIFY" | "REMOVE",
      "section": "string",
      "current_text": "string or null",
      "proposed_text": "string or null",
      "reason": "string"
    }}
  ]
}}"""


def _build_apply_prompt(brd_text: str, brd_title: str, approved: List[Suggestion]) -> str:
    changes_block = "\n\n".join(
        f"{i + 1}. [{s.change_type}] Section: {s.section}\n"
        f"   Reason: {s.reason}\n"
        f"   Current: {s.current_text or '(none — this is an ADD)'}\n"
        f"   Proposed: {s.proposed_text or '(none — this is a REMOVE)'}"
        for i, s in enumerate(approved)
    )
    return f"""You are rewriting a Business Requirements Document to incorporate a set of approved changes.

ORIGINAL BRD
TITLE: {brd_title}
---
{brd_text}
---

APPROVED CHANGES (apply ALL of these — do not skip any)
---
{changes_block}
---

Rules:
1. Output the FULL updated BRD in markdown, including every section that already existed.
2. Apply each approved change exactly: ADDs are inserted in the right section, MODIFYs replace the quoted current_text, REMOVEs delete the quoted text (and the surrounding bullet/paragraph if it becomes empty).
3. Preserve the BRD's existing heading hierarchy, numbering, and tone.
4. Do NOT add changes that weren't in the approved list.
5. Do NOT add commentary, change-log notes, or meta sections like "Changes Applied".

Return ONLY the rewritten BRD markdown. No prose preamble, no code fences around the whole document."""


# ── LLM helpers ──────────────────────────────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(raw: str) -> dict:
    """Find the first {...} block in the LLM output and parse it."""
    if not raw:
        raise ValueError("LLM returned empty response")
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
    return json.loads(match.group(0))


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/compare", response_model=CompareResponse)
def compare_brd_with_code_summary(
    request: CompareRequest,
    current_user: dict = Depends(get_current_user),
):
    # `def` (not `async def`) — FastAPI dispatches sync handlers to a 40-thread
    # threadpool so the 30-90s LLM call doesn't block the event loop. If this
    # were `async def`, a single in-flight compare would freeze every other
    # request on the same uvicorn worker until it returned.
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(status_code=400, detail="Atlassian account not linked.")

    project = get_project(request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    confluence = ConfluenceService(
        domain=credentials["atlassian_domain"],
        email=credentials["atlassian_email"],
        api_token=credentials["atlassian_api_token"],
    )

    # The two page fetches are independent — fan them out in parallel
    # (~1-3s each → ~1-3s total instead of ~2-6s serial). ThreadPoolExecutor
    # is fine here since requests.get releases the GIL on the socket read.
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            cs_fut = pool.submit(confluence.get_page_content, request.code_summary_page_id)
            brd_fut = pool.submit(confluence.get_page_content, request.brd_page_id)
            code_summary_page = cs_fut.result()
            brd_page = brd_fut.result()
    except Exception as e:
        logger.exception("Failed to fetch Confluence page(s)")
        raise HTTPException(status_code=502, detail=f"Confluence fetch error: {e}")

    code_summary_text = _strip_html(code_summary_page.get("content", ""))
    brd_text = _strip_html(brd_page.get("content", ""))
    if not code_summary_text or not brd_text:
        raise HTTPException(status_code=400, detail="One of the pages has no body content.")

    prompt = _build_compare_prompt(
        code_summary=code_summary_text,
        brd=brd_text,
        code_summary_title=code_summary_page["title"],
        brd_title=brd_page["title"],
    )

    raw = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=BEDROCK_MODEL_ID,
        temperature=0,
        max_tokens=8000,
        user_id=current_user["id"],
        token_source="brd_compare",
    )

    try:
        parsed = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse LLM diff output: {e}\n--- raw ---\n{raw}")
        raise HTTPException(status_code=502, detail=f"LLM returned malformed JSON: {e}")

    suggestions: List[Suggestion] = []
    for s in parsed.get("suggestions", []):
        if s.get("change_type") not in ("ADD", "MODIFY", "REMOVE"):
            continue
        suggestions.append(
            Suggestion(
                id=str(uuid.uuid4()),
                change_type=s["change_type"],
                section=s.get("section") or "(unspecified)",
                current_text=s.get("current_text"),
                proposed_text=s.get("proposed_text"),
                reason=s.get("reason") or "",
            )
        )

    try:
        track_event(
            current_user["id"],
            module="brd-sync",
            event_type="brd_compared",
            project_id=request.project_id if hasattr(request, "project_id") else None,
            metadata={
                "brd_page_id": brd_page["id"],
                "code_summary_page_id": code_summary_page["id"],
                "suggestion_count": len(suggestions),
            },
        )
    except Exception as _track_err:
        logger.warning(f"track_event failed (non-fatal): {_track_err}")

    return CompareResponse(
        brd_page_id=brd_page["id"],
        brd_title=brd_page["title"],
        code_summary_page_id=code_summary_page["id"],
        code_summary_title=code_summary_page["title"],
        suggestions=suggestions,
    )


@router.post("/apply", response_model=ApplyResponse)
def apply_approved_changes(
    request: ApplyRequest,
    current_user: dict = Depends(get_current_user),
):
    # Sync def for the same reason as /compare — the LLM merge call inside
    # would otherwise block the event loop for every concurrent request.
    if not request.approved_suggestions:
        raise HTTPException(status_code=400, detail="No approved suggestions to apply.")

    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(status_code=400, detail="Atlassian account not linked.")

    project = get_project(request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    confluence = ConfluenceService(
        domain=credentials["atlassian_domain"],
        email=credentials["atlassian_email"],
        api_token=credentials["atlassian_api_token"],
    )

    try:
        brd_page = confluence.get_page_content(request.brd_page_id)
    except Exception as e:
        logger.exception("Failed to fetch BRD page")
        raise HTTPException(status_code=502, detail=f"Confluence fetch error: {e}")

    brd_text = _strip_html(brd_page.get("content", ""))
    if not brd_text:
        raise HTTPException(status_code=400, detail="BRD has no body content.")

    prompt = _build_apply_prompt(
        brd_text=brd_text,
        brd_title=brd_page["title"],
        approved=request.approved_suggestions,
    )

    merged_markdown = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=BEDROCK_MODEL_ID,
        temperature=0,
        max_tokens=16000,
        user_id=current_user["id"],
        token_source="brd_merge",
    )

    if not merged_markdown or not merged_markdown.strip():
        raise HTTPException(status_code=502, detail="LLM returned empty merged BRD.")

    storage_html = confluence.markdown_to_storage(merged_markdown)

    try:
        updated = confluence.update_page(
            page_id=brd_page["id"],
            title=brd_page["title"],
            content=storage_html,
            current_version=brd_page["version"],
        )
    except Exception as e:
        logger.exception("Failed to update BRD page in Confluence")
        raise HTTPException(status_code=502, detail=f"Confluence update error: {e}")

    # Best-effort fetch of code-summary version for lineage; fall back to 0 if it fails.
    code_summary_version = 0
    try:
        code_summary_version = confluence.get_page_content(request.code_summary_page_id).get("version", 0)
    except Exception as e:
        logger.warning(f"Could not fetch code-summary page version for lineage (non-fatal): {e}")

    # Record lineage in background — failure is non-fatal.
    def _write_lineage():
        try:
            for s in request.approved_suggestions:
                record_lineage(
                    project_id=request.project_id,
                    user_id=current_user["id"],
                    source_type="confluence_page",
                    source_id=request.code_summary_page_id,
                    source_section_id=s.section,
                    source_version=code_summary_version,
                    source_content_hash=hash_text(s.proposed_text or s.current_text or s.reason),
                    target_type="confluence_page",
                    target_id=request.brd_page_id,
                    target_content_hash=hash_text(merged_markdown),
                    target_metadata={
                        "change_type": s.change_type,
                        "section": s.section,
                        "reason": s.reason,
                    },
                    original_generated_content={
                        "change_type": s.change_type,
                        "section": s.section,
                        "current_text": s.current_text,
                        "proposed_text": s.proposed_text,
                    },
                )
        except Exception as e:
            logger.warning(f"Background lineage write failed (non-fatal): {e}")

    threading.Thread(target=_write_lineage, daemon=True).start()

    try:
        track_event(
            current_user["id"],
            module="brd-sync",
            event_type="brd_merged",
            project_id=request.project_id,
            metadata={
                "brd_page_id": brd_page["id"],
                "approved_count": len(request.approved_suggestions or []),
            },
        )
    except Exception as _track_err:
        logger.warning(f"track_event failed (non-fatal): {_track_err}")

    return ApplyResponse(
        page_id=updated["id"],
        title=updated["title"],
        web_url=updated.get("web_url", ""),
        version=brd_page["version"] + 1,
        applied=len(request.approved_suggestions),
    )
