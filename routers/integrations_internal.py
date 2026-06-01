"""
Internal Integrations Router — MCP/API-key authenticated endpoints.

Currently exposes the code-documentation publish flow consumed by the
`code-documentation` MCP server: the MCP sends a finished markdown
document, this endpoint resolves the project's owner + Confluence
credentials, finds (or creates) a parent "Code Documentation" page in
the project space, publishes the new page underneath it, and labels it
`code-documentation` so the frontend can list it via the public
pages-by-label endpoint.
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

from db_helper import (
    get_project,
    get_user_atlassian_credentials,
    track_event,
)
from routers.internal_utils import validate_api_key
from services.confluence_service import ConfluenceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["integrations-internal"])


# Anchor page title that gathers every code-documentation child page in a space.
PARENT_PAGE_TITLE = "Code Documentation"
CODE_SUMMARY_LABEL = "code-documentation"


class PushCodeSummaryRequest(BaseModel):
    scope: str
    content: str        # markdown body produced by the IDE AI
    commit_sha: str
    project_id: Optional[str] = None


def _short_sha(sha: str) -> str:
    return (sha or "").strip()[:8] or "unknown"


def _build_title(scope: str, commit_sha: str) -> str:
    return f"Code Documentation — {scope} — {_short_sha(commit_sha)}"


@router.post("/code-documentation/push-to-confluence-internal")
async def push_code_summary_internal(
    request: PushCodeSummaryRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Publish a code documentation page in Confluence under the project's
    "Code Documentation" parent page, tagged with the `code-documentation` label.

    Auth: X-API-Key (validated via INTERNAL_API_KEYS → project_id).
    """
    key_project_id = validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required (in request body or via key mapping)")

    # ── Resolve project → owner + space ──────────────────────────────────────
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    space_key = project.get("confluence_space_key")
    if not space_key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project {project_id} has no Confluence space linked. "
                f"Link a space in Settings → Integrations before publishing summaries."
            ),
        )

    user_id = project.get("user_id")
    if not user_id:
        raise HTTPException(status_code=500, detail="Project has no owner; cannot resolve credentials.")

    # ── Resolve owner's Atlassian credentials ────────────────────────────────
    creds = get_user_atlassian_credentials(user_id)
    if not creds or not all(creds.get(k) for k in ("atlassian_domain", "atlassian_email", "atlassian_api_token")):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project owner has not linked an Atlassian account. "
                f"Owner must visit Settings → Atlassian to enable Confluence publishing."
            ),
        )

    confluence = ConfluenceService(
        domain=creds["atlassian_domain"],
        email=creds["atlassian_email"],
        api_token=creds["atlassian_api_token"],
    )

    # ── Ensure the "Code Documentation" parent page exists ──────────────────
    try:
        parent = confluence.find_or_create_page(space_key, PARENT_PAGE_TITLE)
    except Exception as e:
        logger.exception("Failed to find_or_create parent page")
        raise HTTPException(status_code=502, detail=f"Confluence parent-page error: {e}")

    parent_id = parent.get("id")
    if not parent_id:
        raise HTTPException(status_code=502, detail="Confluence returned no parent page id.")

    # ── Build storage HTML: info panel + converted markdown ──────────────────
    info_panel = confluence.build_code_summary_info_panel(
        project_id=project_id, commit_sha=request.commit_sha, scope=request.scope
    )
    body_html = confluence.markdown_to_storage(request.content or "")
    page_html = info_panel + body_html

    title = _build_title(request.scope, request.commit_sha)

    # ── Create the page (Confluence rejects duplicate titles in same space;
    #     if a summary for this exact scope+sha already exists, surface it). ──
    existing = confluence.find_page_by_title(space_key, title)
    if existing:
        existing_url = f"{confluence.base_url}{existing.get('_links', {}).get('webui', '')}"
        logger.info(f"Code documentation already exists at {existing_url} — returning existing")
        return {
            "page_id": existing.get("id"),
            "title": existing.get("title"),
            "web_url": existing_url,
            "version": existing.get("version", {}).get("number", 1),
            "created": False,
            "parent_id": parent_id,
            "label": CODE_SUMMARY_LABEL,
        }

    try:
        created = confluence.create_page(
            space_key=space_key,
            title=title,
            content=page_html,
            parent_id=parent_id,
        )
    except Exception as e:
        logger.exception("Failed to create code-documentation page")
        raise HTTPException(status_code=502, detail=f"Confluence create-page error: {e}")

    # Best-effort label — page already exists either way, so don't fail the call.
    label_ok = confluence.apply_label(created["id"], CODE_SUMMARY_LABEL)
    if not label_ok:
        logger.warning(f"Label '{CODE_SUMMARY_LABEL}' could not be applied to {created['id']}")

    # ── Track the event against the project owner ────────────────────────────
    try:
        track_event(
            user_id=user_id,
            module="pair-programming",
            event_type="code_summary_published",
            project_id=project_id,
            source="mcp",
            metadata={
                "scope": request.scope,
                "commit_sha": _short_sha(request.commit_sha),
                "page_id": created.get("id"),
                "content_chars": len(request.content or ""),
            },
        )
    except Exception as e:
        logger.warning(f"track_event failed (non-fatal): {e}")

    return {
        "page_id": created.get("id"),
        "title": created.get("title"),
        "web_url": created.get("web_url"),
        "version": 1,
        "created": True,
        "parent_id": parent_id,
        "label": CODE_SUMMARY_LABEL,
        "label_applied": label_ok,
    }
