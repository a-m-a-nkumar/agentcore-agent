from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import logging
import re
import io
import os
import json
import boto3
import requests
from datetime import datetime, timedelta

from environment import S3_BUCKET_NAME
from auth import verify_azure_token
from db_helper import (
    update_user_atlassian_credentials,
    get_user_atlassian_credentials,
    update_user_lucid_credentials,
    get_user_lucid_credentials,
    clear_user_lucid_credentials,
    update_user_harness_credentials,
    get_user_harness_credentials,
    clear_user_harness_credentials,
    update_user_github_credentials,
    get_user_github_credentials,
    clear_user_github_credentials,
    update_user_bitbucket_credentials,
    get_user_bitbucket_credentials,
    clear_user_bitbucket_credentials,
    create_or_update_user,
    get_project,
    get_design_session,
)
from services.jira_service import JiraService
from services.confluence_service import ConfluenceService
from services.bitbucket_service import BitbucketService
from services.lucid_api_service import (
    LucidAPIService,
    InvalidLucidKeyError,
    LucidNotAccessibleError,
    LucidUpstreamError,
    LucidError,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
logger = logging.getLogger(__name__)

# Cache for token validation results: { user_id: (is_valid: bool, expires_at: datetime) }
_token_validation_cache: Dict[str, tuple] = {}
TOKEN_CACHE_TTL_MINUTES = 5


def _parse_brd_text_to_structure(brd_text: str) -> dict:
    """Parse markdown BRD text into structured JSON with sections, bullets, and tables."""
    sections = []
    current_section = None
    current_content = []

    for line in brd_text.split('\n'):
        stripped = line.strip()

        # Skip markdown separator lines (e.g., ---)
        if re.match(r'^[-=]{3,}$', stripped):
            continue

        # Detect headings (## or #)
        heading_match = re.match(r'^(#{1,3})\s+(.*)', stripped)
        if heading_match:
            # Save previous section
            if current_section:
                if current_content:
                    current_section['content'].append({
                        "type": "paragraph",
                        "text": '\n'.join(current_content).strip()
                    })
                    current_content = []
                sections.append(current_section)

            title = heading_match.group(2).strip()
            current_section = {"title": title, "content": []}
            continue

        if not current_section:
            # Create a default section if content appears before any heading
            if stripped:
                current_section = {"title": "Business Requirements Document", "content": []}
            else:
                continue

        # Bullet points
        if re.match(r'^[-*•]\s+', stripped):
            bullet_text = re.sub(r'^[-*•]\s+', '', stripped)
            if current_content:
                current_section['content'].append({
                    "type": "paragraph",
                    "text": '\n'.join(current_content).strip()
                })
                current_content = []
            if current_section['content'] and current_section['content'][-1].get('type') == 'bullet':
                current_section['content'][-1]['items'].append(bullet_text)
            else:
                current_section['content'].append({"type": "bullet", "items": [bullet_text]})
            continue

        # Table rows
        if '|' in stripped and stripped.startswith('|'):
            cells = [c.strip() for c in stripped.split('|') if c.strip()]
            # Skip separator rows like |---|---|
            if cells and all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            if cells and len(cells) > 1:
                if current_content:
                    current_section['content'].append({
                        "type": "paragraph",
                        "text": '\n'.join(current_content).strip()
                    })
                    current_content = []
                if current_section['content'] and current_section['content'][-1].get('type') == 'table':
                    current_section['content'][-1]['rows'].append(cells)
                else:
                    current_section['content'].append({"type": "table", "rows": [cells]})
                continue

        # Regular text
        if stripped:
            current_content.append(stripped)
        elif current_content:
            # Empty line = paragraph break
            current_section['content'].append({
                "type": "paragraph",
                "text": '\n'.join(current_content).strip()
            })
            current_content = []

    # Finalize last section
    if current_section:
        if current_content:
            current_section['content'].append({
                "type": "paragraph",
                "text": '\n'.join(current_content).strip()
            })
        sections.append(current_section)

    return {"sections": sections}


# ============================================
# AUTHENTICATION DEPENDENCY
# ============================================

def get_current_user(token_data: dict = Depends(verify_azure_token)):
    """
    Get current user from Azure AD token
    Creates/updates user in database if needed.
    Using def (not async def) so FastAPI runs this in a thread pool.
    """
    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")

    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")

    try:
        user = create_or_update_user(user_id, email, name)
        return user
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ============================================
# REQUEST MODELS
# ============================================


class LinkAtlassianRequest(BaseModel):
    domain: str = Field(..., description="Atlassian domain (e.g., mycompany.atlassian.net)")
    email: str = Field(..., description="Email address")
    api_token: str = Field(..., description="Atlassian API token")


class UploadBRDToConfluenceRequest(BaseModel):
    brd_id: str = Field(..., description="BRD ID to upload")
    project_id: str = Field(..., description="Project ID")
    page_title: Optional[str] = Field(None, description="Custom page title (optional)")


class UploadSADToConfluenceRequest(BaseModel):
    session_id: str = Field(..., description="Design session ID whose SAD will be uploaded")
    project_id: str = Field(..., description="Project ID (provides the Confluence space key)")
    page_title: Optional[str] = Field(None, description="Custom page title (optional)")


@router.post("/atlassian/link")
def link_atlassian_account(
    request: LinkAtlassianRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Link user's Atlassian account by saving credentials
    
    The credentials will be validated by testing the connection to Jira.
    If successful, they will be saved to the database.
    """
    
    # Validate credentials by testing connection
    jira_service = JiraService(request.domain, request.email, request.api_token)
    
    success, error_message = jira_service.test_connection()
    if not success:
        raise HTTPException(
            status_code=400,
            detail=error_message or "Invalid Atlassian credentials. Please check your domain, email, and API token."
        )
    
    # Save credentials to database
    try:
        update_user_atlassian_credentials(
            user_id=current_user['id'],
            domain=request.domain,
            email=request.email,
            api_token=request.api_token
        )

        # Clear cached validation so status reflects new token immediately
        _token_validation_cache.pop(current_user['id'], None)

        return {
            "status": "success",
            "message": "Atlassian account linked successfully"
        }
    except Exception as e:
        logger.error(f"Error linking Atlassian account: {e}")
        raise HTTPException(status_code=500, detail="Failed to link Atlassian account")


@router.get("/atlassian/status")
def get_atlassian_status(current_user: dict = Depends(get_current_user)):
    """
    Check if user has linked their Atlassian account

    Returns:
        - linked: bool - Whether account is linked
        - token_expired: bool - Whether the stored token is expired/invalid
        - domain: str (optional) - Atlassian domain
        - email: str (optional) - Email used for authentication
        - linked_at: timestamp (optional) - When the account was linked
    """
    credentials = get_user_atlassian_credentials(current_user['id'])

    if credentials and credentials.get('atlassian_api_token'):
        user_id = current_user['id']

        # Use cached validation result if still fresh
        cached = _token_validation_cache.get(user_id)
        if cached and datetime.utcnow() < cached[1]:
            token_valid = cached[0]
        else:
            # Validate token against Atlassian — only mark expired on 401, not on network errors
            try:
                jira_service = JiraService(
                    credentials['atlassian_domain'],
                    credentials['atlassian_email'],
                    credentials['atlassian_api_token']
                )
                token_valid, _ = jira_service.test_connection()
            except Exception:
                # Network/timeout errors — assume token is still valid, don't disconnect user
                token_valid = True
            _token_validation_cache[user_id] = (token_valid, datetime.utcnow() + timedelta(minutes=TOKEN_CACHE_TTL_MINUTES))

        return {
            "linked": True,
            "token_expired": not token_valid,
            "domain": credentials.get('atlassian_domain'),
            "email": credentials.get('atlassian_email'),
            "linked_at": int(credentials['atlassian_linked_at'].timestamp() * 1000) if credentials.get('atlassian_linked_at') else None
        }

    return {"linked": False, "token_expired": False}


# ============================================================================
# Lucid REST API key — personal API token, KMS-encrypted at rest
# ============================================================================
# Mirrors the Atlassian PAT pattern (POST /link, GET /status, DELETE /unlink).
# Used by routers/design.py endpoints that fetch / import Lucid diagrams into
# a session's diagram slot.

class LinkLucidRequest(BaseModel):
    api_key: str = Field(..., description="Lucid REST API key (starts with 'key-...')")


@router.post("/lucid/link")
def link_lucid_account(
    request: LinkLucidRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate the user's Lucid REST API key against /users/me, then save
    it KMS-encrypted. Returns 200 on success.

    The validation step protects against typos / wrong-region keys (a US
    key against the EU base URL or vice versa would 401 here).
    """
    api_key = (request.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")

    try:
        lucid = LucidAPIService(api_key)
        # Validates the key by exercising the auth path against /documents.
        # Doesn't return identity info (Lucid REST has no /users/me); the
        # purpose here is binary accept/reject of the key.
        lucid.test_connection()
    except InvalidLucidKeyError:
        raise HTTPException(
            status_code=400,
            detail="Lucid rejected this API key. Double-check you copied it correctly.",
        )
    except LucidUpstreamError as e:
        raise HTTPException(status_code=502, detail=f"Lucid unreachable: {e}")
    except LucidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        update_user_lucid_credentials(
            user_id=current_user["id"],
            api_key=api_key,
        )
        # Clear any cached status row for this user.
        _token_validation_cache.pop(f"lucid:{current_user['id']}", None)
        return {
            "status": "success",
            "message": "Lucid account linked successfully",
        }
    except Exception as e:
        logger.error(f"Error saving Lucid credentials: {e}")
        raise HTTPException(status_code=500, detail="Failed to save Lucid API key")


@router.get("/lucid/status")
def get_lucid_status(current_user: dict = Depends(get_current_user)):
    """Return whether the user has a stored Lucid API key and (cached) whether
    it's still valid. Does not call Lucid on every hit — uses the same 5-min
    cache pattern as /atlassian/status.

    Response shape:
        { linked: bool, key_valid: bool, linked_at: ISO-8601 | null }
    """
    creds = get_user_lucid_credentials(current_user["id"])
    if not creds or not creds.get("lucid_api_key"):
        return {"linked": False, "key_valid": False, "linked_at": None}

    cache_key = f"lucid:{current_user['id']}"
    cached = _token_validation_cache.get(cache_key)
    if cached and datetime.utcnow() < cached[1]:
        key_valid = cached[0]
    else:
        try:
            LucidAPIService(creds["lucid_api_key"]).test_connection()
            key_valid = True
        except InvalidLucidKeyError:
            key_valid = False
        except Exception:
            # Network errors / 5xx — assume key is still good, don't make user re-link
            key_valid = True
        _token_validation_cache[cache_key] = (
            key_valid,
            datetime.utcnow() + timedelta(minutes=TOKEN_CACHE_TTL_MINUTES),
        )

    return {
        "linked": True,
        "key_valid": key_valid,
        "linked_at": (
            creds["lucid_linked_at"].isoformat()
            if creds.get("lucid_linked_at") else None
        ),
    }


@router.delete("/lucid/unlink")
def unlink_lucid_account(current_user: dict = Depends(get_current_user)):
    """Drop the user's stored Lucid API key. Idempotent — succeeds even if
    no key was on file."""
    clear_user_lucid_credentials(current_user["id"])
    _token_validation_cache.pop(f"lucid:{current_user['id']}", None)
    return {"status": "success", "message": "Lucid account unlinked"}


# ============================================================
# Harness CI/CD integration — store PAT + workspace in DB
# ============================================================

class LinkHarnessRequest(BaseModel):
    pat: str = Field(..., min_length=1)
    account_id: str = Field(default="")
    org_id: str = Field(default="")
    project_id: str = Field(default="")


@router.post("/harness/link")
def link_harness_account(
    req: LinkHarnessRequest,
    current_user: dict = Depends(get_current_user),
):
    """Persist the user's Harness PAT (KMS-encrypted) and workspace identifiers.
    When org_id / project_id are omitted the existing values are preserved."""
    if not req.org_id or not req.project_id:
        existing = get_user_harness_credentials(current_user["id"])
        org_id = req.org_id or (existing.get("harness_org_id", "") if existing else "")
        project_id = req.project_id or (existing.get("harness_project_id", "") if existing else "")
    else:
        org_id = req.org_id
        project_id = req.project_id
    update_user_harness_credentials(
        current_user["id"],
        req.pat,
        req.account_id,
        org_id,
        project_id,
    )
    logger.info(f"[HARNESS] Linked for user {current_user['id']}")
    return {"status": "success", "message": "Harness account linked"}


@router.get("/harness/status")
def get_harness_status(current_user: dict = Depends(get_current_user)):
    """Return whether the user has a stored Harness PAT, and the associated
    workspace identifiers (account / org / project). The PAT itself is never
    returned to the client."""
    creds = get_user_harness_credentials(current_user["id"])
    if not creds or not creds.get("harness_pat"):
        return {"linked": False}
    linked_at = creds.get("harness_linked_at")
    return {
        "linked": True,
        "account_id": creds.get("harness_account_id"),
        "org_id": creds.get("harness_org_id"),
        "project_id": creds.get("harness_project_id"),
        "linked_at": linked_at.isoformat() if linked_at else None,
    }


@router.get("/harness/credentials")
def get_harness_credentials_for_owner(current_user: dict = Depends(get_current_user)):
    """Return the FULL Harness credentials (including decrypted PAT) for the
    authenticated owner.

    The Harness page calls this when its in-browser sessionStorage cache is
    empty but `/harness/status` reports `linked=true` — e.g. the user
    connected on one machine and then opened the app on another, or just
    closed the tab. Without this endpoint, the page can't make Harness API
    calls and the user is forced to re-enter their PAT from My Profile
    even though it's already stored server-side.

    Security:
      • Caller is authenticated via the same JWT pipeline as every other
        endpoint, so we only ever hand the PAT back to its owner.
      • Kept as a separate route from /status so the default page-load
        traffic doesn't ferry the secret across the wire — only the
        Harness page on first mount fetches it.
    """
    creds = get_user_harness_credentials(current_user["id"])
    if not creds or not creds.get("harness_pat"):
        raise HTTPException(
            status_code=404,
            detail="No Harness credentials on file. Connect Harness in Profile first.",
        )
    return {
        "pat": creds["harness_pat"],
        "account_id": creds.get("harness_account_id"),
        "org_id": creds.get("harness_org_id"),
        "project_id": creds.get("harness_project_id"),
    }


@router.delete("/harness/unlink")
def unlink_harness_account(current_user: dict = Depends(get_current_user)):
    """Drop the user's stored Harness credentials. Idempotent."""
    clear_user_harness_credentials(current_user["id"])
    logger.info(f"[HARNESS] Unlinked for user {current_user['id']}")
    return {"status": "success", "message": "Harness account unlinked"}


# ============================================================
# GitHub integration — store PAT in DB (KMS-encrypted)
# ============================================================

class LinkGithubRequest(BaseModel):
    pat: str = Field(..., min_length=1, description="GitHub Personal Access Token")


@router.post("/github/link")
def link_github_account(
    req: LinkGithubRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate and persist the user's GitHub PAT (KMS-encrypted)."""
    pat = req.pat.strip()
    if not pat:
        raise HTTPException(status_code=400, detail="GitHub PAT is required")
    try:
        resp = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=400, detail="Invalid GitHub token. Please check and try again.")
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=400, detail=f"GitHub returned {resp.status_code}. Please check your token.")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[GITHUB] Validation network error: {e} — accepting token anyway")

    update_user_github_credentials(current_user["id"], pat)
    logger.info(f"[GITHUB] Linked for user {current_user['id']}")
    return {"status": "success", "message": "GitHub account linked"}


@router.get("/github/status")
def get_github_status(current_user: dict = Depends(get_current_user)):
    """Return whether the user has a stored GitHub PAT. PAT is never returned."""
    creds = get_user_github_credentials(current_user["id"])
    if not creds or not creds.get("github_pat"):
        return {"linked": False}
    linked_at = creds.get("github_linked_at")
    return {
        "linked": True,
        "linked_at": linked_at.isoformat() if linked_at else None,
    }


@router.delete("/github/unlink")
def unlink_github_account(current_user: dict = Depends(get_current_user)):
    """Drop the user's stored GitHub PAT. Idempotent."""
    clear_user_github_credentials(current_user["id"])
    logger.info(f"[GITHUB] Unlinked for user {current_user['id']}")
    return {"status": "success", "message": "GitHub account unlinked"}


# ============================================================
# Bitbucket integration — store email + App Password in DB
# ============================================================

class LinkBitbucketRequest(BaseModel):
    email: str = Field(..., min_length=1, description="Atlassian login email")
    # Naming kept as `app_password` for back-compat with stored data + legacy
    # callers. Atlassian's API tokens (with scopes) are now the supported
    # credential — App Passwords are deprecated — and they use the exact same
    # HTTP Basic Auth flow, so no change is needed beyond field labelling.
    app_password: str = Field(
        ...,
        min_length=1,
        description="Atlassian API token with Bitbucket scopes (read:user / read:workspace / read:repository / write:repository / write:pullrequest). Legacy App Passwords still work but are being retired.",
    )


@router.post("/bitbucket/link")
def link_bitbucket_account(
    req: LinkBitbucketRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate and persist the user's Bitbucket credentials (KMS-encrypted).

    Atlassian deprecated Bitbucket App Passwords in favour of scoped API tokens.
    Both work with HTTP Basic Auth so the validation path is unchanged; only
    the credential the user pastes has changed.
    """
    email = req.email.strip()
    api_token = req.app_password.strip()
    if not email or not api_token:
        raise HTTPException(status_code=400, detail="Email and API token are required")

    svc = BitbucketService(email, api_token)
    ok, error_msg = svc.test_connection()
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=error_msg or "Invalid Bitbucket credentials. Please check your email and API token (scopes: read:user, read:workspace, read:repository, write:repository, write:pullrequest).",
        )

    update_user_bitbucket_credentials(current_user["id"], email, api_token)
    logger.info(f"[BITBUCKET] Linked for user {current_user['id']}")
    return {"status": "success", "message": "Bitbucket account linked"}


@router.get("/bitbucket/status")
def get_bitbucket_status(current_user: dict = Depends(get_current_user)):
    """Return whether the user has stored Bitbucket credentials. The token is never returned."""
    creds = get_user_bitbucket_credentials(current_user["id"])
    if not creds or not creds.get("bitbucket_app_password"):
        return {"linked": False}
    linked_at = creds.get("bitbucket_linked_at")
    return {
        "linked": True,
        "email": creds.get("bitbucket_email"),
        "linked_at": linked_at.isoformat() if linked_at else None,
    }


@router.delete("/bitbucket/unlink")
def unlink_bitbucket_account(current_user: dict = Depends(get_current_user)):
    """Drop the user's stored Bitbucket credentials. Idempotent."""
    clear_user_bitbucket_credentials(current_user["id"])
    logger.info(f"[BITBUCKET] Unlinked for user {current_user['id']}")
    return {"status": "success", "message": "Bitbucket account unlinked"}


@router.get("/jira/projects")
def list_jira_projects(current_user: dict = Depends(get_current_user)):
    """
    List all accessible Jira projects for the linked Atlassian account
    
    Returns:
        List of Jira projects with key, name, id, and type
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        projects = jira_service.get_projects()
        return {"projects": projects}
    
    except Exception as e:
        logger.error(f"Error fetching Jira projects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/spaces")
def list_confluence_spaces(
    start: int = 0,
    limit: int = 100,
    search: str = "",
    current_user: dict = Depends(get_current_user),
):
    """
    List accessible Confluence spaces with pagination for lazy loading.

    Query params:
        start:  offset for pagination (default 0)
        limit:  page size (default 100)
        search: optional text filter on key/name
    """
    credentials = get_user_atlassian_credentials(current_user['id'])

    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )

    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )

        result = confluence_service.get_spaces_page(start=start, limit=limit, search=search)
        return result   # { spaces: [...], hasMore: bool }

    except Exception as e:
        logger.error(f"Error fetching Confluence spaces: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/pages")
def list_confluence_pages(
    space_key: str = "SO",
    current_user: dict = Depends(get_current_user),
):
    """
    Exhaustively fetch every page AND every folder in a Confluence space,
    preserving the folder hierarchy as it exists in Confluence's UI.

    Replaces the legacy v1 path which:
      - capped at 200 pages server-side (so spaces with >200 pages silently
        truncated to the first 200 in oldest-first order — newly-created
        pages didn't show up)
      - missed pages nested inside Confluence Folders (a 2024 content type
        the v1 /rest/api/content endpoint doesn't enumerate)
      - returned a flat list with no folder grouping

    Returns:
        {
            "tree":     [<top-level nodes>],   # nested children inline
            "results":  [<every page, flat>],  # kept for old callers
            "stats": {
                "pages": N, "folders": M, "top_level": K, "max_depth": D,
                "orphaned": O, "list_calls": H
            }
        }

    Each node carries `type` ("page"|"folder"), `id`, `title`, `parentId`,
    `parentType`, `_links.webui`, and `children` (the items nested under it,
    sorted alphabetically). Pages inside folders appear as children of
    those folders; pages at the space root appear at the top of the tree.

    Per-batch progress + final totals are logged at INFO level
    (see [get_space_tree] prefix in the service logs) so we can confirm
    everything was fetched.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        bundle = confluence_service.get_space_tree(space_key=space_key)
        logger.info(
            f"[/confluence/pages] space={space_key} returned "
            f"pages={bundle['stats']['pages']} folders={bundle['stats']['folders']} "
            f"top_level={bundle['stats']['top_level']} "
            f"max_depth={bundle['stats']['max_depth']} "
            f"orphaned={bundle['stats']['orphaned']} "
            f"http_calls={bundle['stats']['list_calls']}"
        )
        return {
            "tree":    bundle["tree"],
            "results": bundle["flat"],
            "stats":   bundle["stats"],
        }
    except Exception as e:
        logger.error(f"Error fetching Confluence pages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/space-pages")
def list_all_confluence_space_pages(
    space_key: str,
    current_user: dict = Depends(get_current_user),
):
    """
    List EVERY page in a Confluence space (no 200-page cap).

    Uses the same v2 cursor-paginated fetch the RAG ingestion flow uses
    (ConfluenceService.get_space_pages with max_pages=None) so large spaces
    return in full. Metadata only (with_body=False) — page bodies are fetched
    on demand per selection via /confluence/pages/{page_id}.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        pages = confluence_service.get_space_pages(
            space_key=space_key, max_pages=None, with_body=False
        )
        results = [
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "webui": (p.get("_links") or {}).get("webui", ""),
            }
            for p in pages
            if p.get("id") and p.get("title")
        ]
        logger.info(
            f"[confluence/space-pages] space={space_key} returned {len(results)} pages"
        )
        return {"results": results, "space_key": space_key, "count": len(results)}
    except Exception as e:
        logger.error(f"Error fetching all Confluence pages for space '{space_key}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/pages-by-label")
def list_confluence_pages_by_label(
    space_key: str,
    label: str = "code-documentation",
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """
    List Confluence pages in a space tagged with a label (newest first).
    Used by the BRD Sync page to enumerate published code documentation pages.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        # Prefer the content tree under the "Code Documentation" parent page (instant after publish);
        # fall back to CQL search only when no anchor parent exists.
        parent = confluence_service.find_page_by_title(space_key, "Code Documentation")
        if parent and parent.get("id"):
            raw = confluence_service.list_children_with_label(
                parent_id=parent["id"], label=label, limit=limit
            )
        else:
            raw = confluence_service.search_pages_by_label(
                space_key=space_key, label=label, limit=limit
            )
        results = []
        for p in raw:
            history = p.get("history", {}) or {}
            version = p.get("version", {}) or {}
            labels = [
                lbl.get("name") for lbl in (p.get("metadata", {}).get("labels", {}) or {}).get("results", [])
                if lbl.get("name")
            ]
            results.append({
                "page_id": p.get("id"),
                "title": p.get("title"),
                "web_url": f"{confluence_service.base_url}{p.get('_links', {}).get('webui', '')}",
                "created": history.get("createdDate"),
                "last_modified": version.get("when"),
                "version": version.get("number", 1),
                "labels": labels,
            })
        return {"results": results, "label": label, "space_key": space_key}
    except Exception as e:
        logger.error(f"Error listing pages by label '{label}' in space '{space_key}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confluence/pages/{page_id}")
def get_confluence_page(
    page_id: str,
    expand: str = "body.storage,version,ancestors",
    current_user: dict = Depends(get_current_user),
):
    """
    Get a Confluence page by ID using the current user's linked Atlassian credentials.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        return confluence_service.get_content_page_by_id(page_id=page_id, expand=expand)
    except Exception as e:
        logger.error(f"Error fetching Confluence page {page_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ConfluencePagesBulkRequest(BaseModel):
    page_ids: List[str]
    # Whether to strip HTML server-side and return plain text (cheaper for the
    # client to consume; matches the BRD pipeline's needs). When False the raw
    # storage HTML is returned and the client strips it.
    plain_text: bool = True


def _strip_confluence_html(html: str) -> str:
    """Best-effort HTML -> plain text. Mirrors the recipe the design flow uses
    (`<[^>]+>` strip + entity replace + whitespace collapse)."""
    import re
    txt = re.sub(r"<[^>]+>", " ", html or "")
    txt = (
        txt.replace("&nbsp;", " ")
           .replace("&amp;", "&")
           .replace("&lt;", "<")
           .replace("&gt;", ">")
    )
    return re.sub(r"\s+", " ", txt).strip()


@router.post("/confluence/pages-bulk")
def get_confluence_pages_bulk(
    body: ConfluencePagesBulkRequest,
    current_user: dict = Depends(get_current_user),
):
    """Fetch many Confluence pages' bodies in PARALLEL server-side.

    The frontend used to fan out one HTTP round-trip per page (Promise.all over
    the per-page endpoint), which is rate-limited by single-page latency × N.
    This endpoint fetches up to 10 pages concurrently against Confluence and
    returns them in input order. Failures don't kill the batch — the failing
    page comes back with an `error` field and the rest succeed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )

    confluence_service = ConfluenceService(
        credentials["atlassian_domain"],
        credentials["atlassian_email"],
        credentials["atlassian_api_token"],
    )

    page_ids = list(body.page_ids or [])
    if not page_ids:
        return {"results": [], "count": 0}

    def _one(pid: str) -> Dict:
        try:
            p = confluence_service.get_content_page_by_id(
                page_id=pid, expand="body.storage,version"
            )
            title = p.get("title") or pid
            html = ((p.get("body") or {}).get("storage") or {}).get("value") or ""
            text = _strip_confluence_html(html) if body.plain_text else ""
            return {
                "id": pid,
                "title": title,
                "html": "" if body.plain_text else html,
                "text": text if body.plain_text else "",
            }
        except Exception as e:
            return {"id": pid, "title": pid, "html": "", "text": "", "error": str(e)[:300]}

    by_id: Dict[str, Dict] = {}
    # 10 workers is a sensible default for Confluence (servers tolerate it
    # comfortably and the gain plateaus beyond ~8-12 concurrent fetches).
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_one, pid): pid for pid in page_ids}
        for fut in as_completed(futs):
            res = fut.result()
            by_id[res["id"]] = res

    ordered = [by_id[pid] for pid in page_ids if pid in by_id]
    errors = sum(1 for r in ordered if r.get("error"))
    logger.info(
        f"[confluence/pages-bulk] fetched {len(ordered)}/{len(page_ids)} pages "
        f"({errors} errors) plain_text={body.plain_text}"
    )
    return {"results": ordered, "count": len(ordered), "errors": errors}


@router.get("/jira/issues/{project_key}")
def get_jira_issues(
    project_key: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch Jira issues for a specific project
    
    Args:
        project_key: The Jira project key (e.g., 'PROJ', 'DEV')
        
    Returns:
        List of Jira issues from the specified project
    """
    logger.info(f"Fetching Jira issues for project_key: '{project_key}' (user: {current_user['id']})")
    
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        logger.info(f"Using Jira domain: {credentials['atlassian_domain']}")
        issues = jira_service.get_project_issues(project_key)
        logger.info(f"Successfully fetched {len(issues)} issues for project {project_key}")
        return {"issues": issues, "total": len(issues)}
    
    except Exception as e:
        logger.error(f"Error fetching Jira issues for project {project_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jira/boards/{project_key}")
def get_jira_boards(
    project_key: str,
    current_user: dict = Depends(get_current_user)
):
    """Fetch Jira boards for a specific project"""
    logger.info(f"Fetching Jira boards for project_key: '{project_key}' (user: {current_user['id']})")

    credentials = get_user_atlassian_credentials(current_user['id'])

    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )

    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )

        boards = jira_service.get_boards(project_key)
        logger.info(f"Successfully fetched {len(boards)} boards for project {project_key}")
        return {"boards": boards, "total": len(boards)}

    except Exception as e:
        logger.error(f"Error fetching Jira boards for project {project_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confluence/upload-brd")
def upload_brd_to_confluence(
    request: UploadBRDToConfluenceRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Upload BRD from S3 to Confluence
    
    Creates a new Confluence page with the BRD content from S3.
    The page will be created in the Confluence space linked to the project.
    
    Args:
        request: Contains brd_id and project_id
        
    Returns:
        Confluence page details including page ID and web URL
    """
    logger.info(f"Uploading BRD {request.brd_id} to Confluence for project {request.project_id}")
    
    # 1. Get user's Atlassian credentials
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    # 2. Get project to find Confluence space key
    project = get_project(request.project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if not project.get('confluence_space_key'):
        raise HTTPException(
            status_code=400,
            detail="No Confluence space linked to this project. Please link a Confluence space in project settings."
        )
    
    confluence_space_key = project['confluence_space_key']
    
    # 3. Fetch BRD from S3
    try:
        s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        bucket_name = S3_BUCKET_NAME
        
        # Try to fetch JSON structure first
        json_key = f"brds/{request.brd_id}/brd_structure.json"
        
        brd_json = None

        # brd_structure.json is the CANONICAL key the unified BRD
        # agent writes to. Both lambda_brd_generator (Phase 2 commit
        # 11 parallel path) and lambda_brd_from_history now write
        # this canonical key, so the legacy BRD_{id}.json fallback
        # is gone (deleted Phase 5 commit 4 -- the S3 backfill in
        # migrations/add_brd_structure_previous_versions.py covered
        # any historical BRDs missing the canonical key).
        #
        # The text fallback below stays as a last-ditch resort for
        # BRDs that pre-date the structured JSON era entirely.
        try:
            logger.info(f"Fetching BRD from S3: s3://{bucket_name}/{json_key}")
            response = s3_client.get_object(Bucket=bucket_name, Key=json_key)
            brd_json = json.loads(response['Body'].read().decode('utf-8'))
            logger.info(f"Successfully loaded brd_structure.json with {len(brd_json.get('sections', []))} sections")
        except Exception as e:
            logger.warning(f"Could not load brd_structure.json: {e}")

        # Last-ditch fallback: parse text file into structured format.
        # Pre-dates the structured-JSON era; should be rare.
        if not brd_json or not brd_json.get('sections'):
            txt_key = f"brds/{request.brd_id}/BRD_{request.brd_id}.txt"
            logger.info(f"Falling back to text: s3://{bucket_name}/{txt_key}")
            response = s3_client.get_object(Bucket=bucket_name, Key=txt_key)
            brd_text = response['Body'].read().decode('utf-8')
            brd_json = _parse_brd_text_to_structure(brd_text)
    
    except Exception as e:
        logger.error(f"Error fetching BRD from S3: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch BRD from S3: {str(e)}"
        )
    
    # 4. Convert BRD to Confluence format
    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        # Convert BRD JSON to Confluence storage format
        confluence_content = confluence_service.convert_brd_to_confluence_storage(brd_json)
        
        # Generate page title
        if request.page_title:
            page_title = request.page_title
        else:
            # Use project name + timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            page_title = f"BRD - {project.get('project_name', 'Untitled')} - {timestamp}"
        
        logger.info(f"Creating Confluence page: '{page_title}' in space '{confluence_space_key}'")
        
        # 5. Create Confluence page
        page_result = confluence_service.create_page(
            space_key=confluence_space_key,
            title=page_title,
            content=confluence_content
        )
        
        logger.info(f"Successfully created Confluence page: {page_result['web_url']}")
        
        return {
            "status": "success",
            "message": "BRD uploaded to Confluence successfully",
            "confluence_page": {
                "id": page_result['id'],
                "title": page_result['title'],
                "web_url": page_result['web_url'],
                "space_key": confluence_space_key
            }
        }
    
    except Exception as e:
        logger.error(f"Error creating Confluence page: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Confluence page: {str(e)}"
        )


@router.post("/confluence/upload-sad")
def upload_sad_to_confluence(
    request: UploadSADToConfluenceRequest,
    current_user: dict = Depends(get_current_user),
):
    """Push a generated SAD (Software Architecture Document) to Confluence.

    Mirrors /confluence/upload-brd: reads the SAD JSON the SAD-orchestrator
    Lambda persisted to S3, converts each section's content blocks to
    Confluence storage XHTML, creates the page in the project's linked space,
    and uploads each diagram block's PNG/SVG as a page attachment so the
    inline `<ac:image>` references resolve.
    """
    logger.info(
        f"Uploading SAD for session {request.session_id} to Confluence (project={request.project_id})"
    )

    # 1. Ownership — the session must belong to the caller.
    session = get_design_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Design session not found")
    if session.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="You don't have access to this session")

    # 2. Atlassian credentials.
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first.",
        )

    # 3. Project + Confluence space key.
    project = get_project(request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    confluence_space_key = project.get("confluence_space_key")
    if not confluence_space_key:
        raise HTTPException(
            status_code=400,
            detail="No Confluence space linked to this project. Please link a Confluence space in project settings.",
        )

    # 4. Read SAD JSON from S3.
    s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    sad_key = f"sessions/{request.session_id}/sad/sad_structure.json"
    try:
        sad_obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=sad_key)
        sad_data = json.loads(sad_obj["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Failed to read SAD from s3://{S3_BUCKET_NAME}/{sad_key}: {e}")
        raise HTTPException(
            status_code=404,
            detail="SAD has not been generated for this session. Generate the SAD before pushing to Confluence.",
        )

    # 5. Collect diagram blocks and pre-fetch their bytes from S3. SVGs are
    # rasterised to PNG (Confluence renders inline PNG/JPEG reliably; SVG via
    # attachment is unreliable). Each unique s3_key maps to one attachment.
    try:
        import cairosvg  # type: ignore
    except Exception:
        cairosvg = None

    diagrams: Dict[str, Dict] = {}  # s3_key -> {filename, bytes, content_type}
    for section in sad_data.get("sections", []) or []:
        for block in section.get("content", []) or []:
            if block.get("type") != "diagram":
                continue
            s3_key = block.get("s3_key") or ""
            if not s3_key or s3_key in diagrams:
                continue
            try:
                body = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)["Body"].read()
            except Exception as e:
                logger.warning(f"SAD push: could not fetch diagram {s3_key}: {e}")
                continue
            lower = s3_key.lower()
            if lower.endswith(".svg"):
                if not cairosvg:
                    logger.warning(
                        f"SAD push: cairosvg unavailable; skipping SVG diagram {s3_key}"
                    )
                    continue
                try:
                    png_buf = io.BytesIO()
                    cairosvg.svg2png(bytestring=body, write_to=png_buf, output_width=1200)
                    body = png_buf.getvalue()
                    content_type = "image/png"
                    filename_ext = ".png"
                except Exception as e:
                    logger.warning(f"SAD push: SVG->PNG conversion failed for {s3_key}: {e}")
                    continue
            elif lower.endswith(".png"):
                content_type = "image/png"
                filename_ext = ".png"
            elif lower.endswith((".jpg", ".jpeg")):
                content_type = "image/jpeg"
                filename_ext = ".jpg"
            else:
                logger.warning(f"SAD push: unsupported diagram extension for {s3_key}; skipping")
                continue

            base = os.path.basename(s3_key).rsplit(".", 1)[0] or "diagram"
            safe_base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
            filename = f"{safe_base}{filename_ext}"
            diagrams[s3_key] = {
                "filename": filename,
                "bytes": body,
                "content_type": content_type,
            }

    # 6. Build Confluence storage XHTML referencing the attachment filenames.
    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        diagram_filenames = {k: v["filename"] for k, v in diagrams.items()}
        confluence_content = confluence_service.convert_sad_to_confluence_storage(
            sad_data, diagram_filenames=diagram_filenames
        )

        if request.page_title:
            page_title = request.page_title
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            session_name = session.get("name") or "Untitled"
            page_title = f"SAD - {project.get('project_name', 'Untitled')} - {session_name} - {timestamp}"

        logger.info(
            f"Creating Confluence page: '{page_title}' in space '{confluence_space_key}' "
            f"({len(sad_data.get('sections', []))} sections, {len(diagrams)} diagrams)"
        )

        page_result = confluence_service.create_page(
            space_key=confluence_space_key,
            title=page_title,
            content=confluence_content,
        )
        page_id = page_result["id"]

        # 7. Upload diagrams as page attachments. Best-effort per attachment —
        # one failure shouldn't tear down the whole push; the page already
        # exists with placeholder rendering at worst.
        for s3_key, info in diagrams.items():
            try:
                confluence_service.create_attachment(
                    page_id=page_id,
                    filename=info["filename"],
                    file_bytes=info["bytes"],
                    content_type=info["content_type"],
                )
            except Exception as e:
                logger.warning(
                    f"SAD push: attachment upload failed for {info['filename']} on page {page_id}: {e}"
                )

        logger.info(f"Successfully created Confluence page: {page_result['web_url']}")

        return {
            "status": "success",
            "message": "SAD uploaded to Confluence successfully",
            "confluence_page": {
                "id": page_result["id"],
                "title": page_result["title"],
                "web_url": page_result["web_url"],
                "space_key": confluence_space_key,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Confluence page for SAD: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Confluence page: {str(e)}",
        )


# ============================================
# BITBUCKET DIRECT CREDENTIAL ENDPOINTS
# ============================================

class BitbucketDirectRequest(BaseModel):
    email: str = Field(..., description="Atlassian login email (from id.atlassian.com)")
    api_token: str = Field(..., description="Bitbucket-scoped API token")

@router.post("/bitbucket/connect-direct")
def bitbucket_connect_direct(
    request: BitbucketDirectRequest,
    _: dict = Depends(verify_azure_token),
):
    """Test Bitbucket credentials directly without requiring a linked Atlassian account."""
    try:
        svc = BitbucketService(request.email, request.api_token)
        ok, error_msg = svc.test_connection()
        if not ok:
            return {"linked": False, "error": error_msg}
        user_profile = svc.get_user()
        workspaces = svc.get_workspaces()
        return {
            "linked": True,
            "username": user_profile.get("username") or user_profile.get("account_id"),
            "display_name": user_profile.get("display_name"),
            "account_id": user_profile.get("account_id"),
            "workspaces": workspaces,
        }
    except Exception as e:
        logger.error(f"Bitbucket direct connect failed: {e}")
        return {"linked": False, "error": str(e)}


# Three previous endpoints were here:
#   GET /bitbucket/repositories-direct/{workspace}
#   GET /bitbucket/branches-direct/{workspace}/{repo_slug}
#   GET /bitbucket/fetch-files-direct/{workspace}/{repo_slug}
# They accepted `email` and `api_token` as URL query parameters, which leak
# credentials into browser history, server access logs, and Referer headers.
# They had no remaining callers — the frontend migrated to the
# *-stored endpoints below (which load the user's DB-stored Bitbucket
# credentials by authenticated user_id), and `connect-direct` already
# accepts test credentials in the POST body. Removed entirely; new
# credential-bearing endpoints must use POST + body or DB-stored credentials.


# ============================================================
# Stored-credential Bitbucket endpoints
# Credentials come from the user's My Profile connection — no
# email / token needed in the request.
# ============================================================

def _get_stored_bitbucket_service(user_id: str) -> "BitbucketService":
    """Load stored Bitbucket credentials from DB and return a ready BitbucketService."""
    creds = get_user_bitbucket_credentials(user_id)
    if not creds or not creds.get("bitbucket_app_password"):
        raise HTTPException(
            status_code=404,
            detail="Bitbucket not connected. Please link your account in My Profile → Integrations → Bitbucket.",
        )
    return BitbucketService(creds["bitbucket_email"], creds["bitbucket_app_password"])


@router.get("/bitbucket/repositories-stored/{workspace}")
def list_bitbucket_repositories_stored(
    workspace: str,
    current_user: dict = Depends(get_current_user),
):
    """List repositories using credentials stored in DB (connected via My Profile)."""
    try:
        svc = _get_stored_bitbucket_service(current_user["id"])
        repos = svc.get_repositories(workspace)
        return {"repositories": repos}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bitbucket/branches-stored/{workspace}/{repo_slug}")
def list_bitbucket_branches_stored(
    workspace: str,
    repo_slug: str,
    current_user: dict = Depends(get_current_user),
):
    """List branches using credentials stored in DB."""
    try:
        svc = _get_stored_bitbucket_service(current_user["id"])
        branches = svc.get_branches(workspace, repo_slug)
        return {"branches": branches}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bitbucket/fetch-files-stored/{workspace}/{repo_slug}")
def fetch_bitbucket_files_stored(
    workspace: str,
    repo_slug: str,
    ref: str = "main",
    path: str = "",
    current_user: dict = Depends(get_current_user),
):
    """Fetch Terraform files using credentials stored in DB."""
    try:
        svc = _get_stored_bitbucket_service(current_user["id"])
        files = svc.get_files_bulk(workspace, repo_slug, ref=ref, path=path, extensions=[".tf", ".tfvars", ".hcl"])
        return {"files": files, "count": len(files)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


