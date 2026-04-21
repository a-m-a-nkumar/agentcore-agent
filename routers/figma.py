"""
Figma Design Prompt Router
Generates Figma-design-focused prompts from a Jira story + Confluence RAG context.
Returns plain JSON (no SSE streaming) — same pattern as design.py.
"""

import json
import os
import logging
import boto3
from botocore.config import Config as BotoConfig

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from auth import verify_azure_token
from db_helper import create_or_update_user, update_user_figma_credentials, get_user_figma_credentials
from services.search_service import search_service
from services.figma_service import FigmaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/figma", tags=["figma"])

# ─── Bedrock client ───────────────────────────────────────────────────────────

_bedrock_client = None


def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        region = os.getenv("AWS_REGION", os.getenv("BEDROCK_REGION", "us-east-1"))
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 2}),
        )
    return _bedrock_client


FIGMA_MODEL_ID = os.getenv("FIGMA_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
FIGMA_MAX_TOKENS = int(os.getenv("FIGMA_MAX_TOKENS", "8192"))


# ─── Auth dependency ──────────────────────────────────────────────────────────

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    user_id = token_data.get("oid") or token_data.get("sub")
    email = (
        token_data.get("preferred_username")
        or token_data.get("email")
        or token_data.get("upn")
    )
    name = token_data.get("name")
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")
    try:
        return create_or_update_user(user_id, email, name)
    except Exception as e:
        logger.error(f"[FIGMA] Auth error: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ─── Bedrock helper ───────────────────────────────────────────────────────────

def _invoke_claude(
    system_prompt: str,
    user_message: str,
    model_id: str = FIGMA_MODEL_ID,
    max_tokens: int = FIGMA_MAX_TOKENS,
) -> str:
    """Call Bedrock Claude synchronously and return the full text response."""
    bedrock = _get_bedrock()
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
        "temperature": 0.5,
    }
    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"]
    except Exception as e:
        logger.error(f"[FIGMA] Bedrock invoke error: {e}")
        raise HTTPException(status_code=502, detail=f"AI model error: {str(e)}")


# ─── Figma system prompt ──────────────────────────────────────────────────────

FIGMA_SYSTEM_PROMPT = """You are a senior UX/UI design consultant and Figma expert. You will be given:
1. A Jira story with title, description, type, priority, and acceptance criteria
2. Relevant project context retrieved from Confluence documentation

Your task is to produce a single, comprehensive, immediately-usable Figma design prompt.
The output must be a standalone prompt that a designer (or AI tool) can paste directly
into Figma AI, Figma plugins, or any design AI tool to generate high-fidelity screens
or wireframes.

═══════════════════════════════════════════════════════════════
WHAT TO EXTRACT AND SYNTHESISE
═══════════════════════════════════════════════════════════════

From the Jira story, extract:
  - The user-facing feature or interaction being built
  - The acceptance criteria that constrain the UI behaviour
  - The issue type (Story → new screen/flow; Bug → existing UI fix; Task → component/pattern)
  - Priority signals (High → include edge cases and error states)

From the Confluence context, extract:
  - Existing design system tokens (colour palette, typography, spacing)
  - Component library references (button variants, form patterns, navigation style)
  - Brand guidelines or product principles that affect visual decisions
  - Existing screen patterns or user flow descriptions
  - Any mentioned personas or user roles that affect layout decisions

If the Confluence context does not mention a design system, infer standard Material 3 /
shadcn-style conventions and mark them as (inferred).

═══════════════════════════════════════════════════════════════
PROMPT STRUCTURE TO OUTPUT — fill every section completely
═══════════════════════════════════════════════════════════════

=============================================================
FIGMA DESIGN PROMPT
Story: [Jira key] — [Story title]
=============================================================

DESIGN BRIEF
  Feature:      [What the user can do — one sentence, action-oriented]
  User Role:    [Who is performing this action, based on story/context]
  Entry Point:  [How the user arrives at this screen/flow]
  Exit Points:  [Where the user goes after completing the action]

SCREENS TO DESIGN
  [List each screen/state as a numbered item]
  1. [Screen name] — [Purpose, key elements visible]
  2. [Screen name] — [Purpose, key elements visible]
  (Include: default state, loading state, empty state, error state, success state)

COMPONENT REQUIREMENTS
  Navigation:   [Nav bar / sidebar / breadcrumb pattern]
  Forms:        [Input fields, validation messages, labels — if applicable]
  CTAs:         [Primary and secondary button labels and placement]
  Data Display: [Tables, cards, lists — if applicable]
  Feedback:     [Toast notifications, inline errors, progress indicators]

LAYOUT SPECIFICATIONS
  Breakpoints:  [Desktop (1440px), Tablet (768px), Mobile (375px) — which to design first]
  Grid:         [Column count, gutter, margin]
  Spacing:      [Base unit and key spacing values from design system]
  Hierarchy:    [Describe the visual hierarchy: primary action, secondary info, metadata]

DESIGN SYSTEM TOKENS
  Primary colour:     [hex or token name]
  Secondary colour:   [hex or token name]
  Background:         [hex or token name]
  Surface:            [hex or token name]
  Typography scale:   [heading → body → caption sizes and weights]
  Border radius:      [buttons, cards, modals]
  Elevation/Shadow:   [shadow tokens for cards/modals]

USER FLOW (for this story)
  [Numbered steps describing the exact interaction sequence]
  1. User sees ...
  2. User clicks/taps ...
  3. System responds with ...
  4. User completes ...

ACCEPTANCE CRITERIA → UI MAPPING
  [For each acceptance criterion, describe the specific UI element or behaviour it maps to]
  - [AC item] → [UI element/behaviour]

EDGE CASES AND ERROR STATES TO INCLUDE
  - [List each error state, empty state, or edge case with the expected UI treatment]

FIGMA-SPECIFIC INSTRUCTIONS
  - Create components for all repeating elements (list items, form fields, buttons)
  - Use Auto Layout for all containers to ensure responsiveness
  - Apply design tokens as Figma variables (not hard-coded hex values)
  - Create a dedicated frame for each screen state listed above
  - Group related frames in a Figma Section labelled "[Story Key] — [Story Title]"
  - Annotate each frame with the acceptance criterion it satisfies

=============================================================
END OF PROMPT
=============================================================

Output ONLY the filled prompt block starting with the === header line.
Do NOT add preamble, explanation, or commentary before or after it.
Zero placeholders should remain — if information is not available, write (not specified)
or (inferred from [source]) so the designer knows the confidence level."""


# ─── Request / Response models ────────────────────────────────────────────────

class JiraStoryInput(BaseModel):
    key: str
    title: str
    description: str
    type: Optional[str] = "Story"
    priority: Optional[str] = "medium"
    acceptance_criteria: Optional[str] = ""


class FigmaPromptRequest(BaseModel):
    project_id: str
    jira_story: JiraStoryInput
    max_chunks: Optional[int] = 5


class ConfluenceSource(BaseModel):
    title: str
    url: Optional[str] = ""
    source_id: str


class FigmaPromptResponse(BaseModel):
    prompt: str
    sources: List[Dict[str, Any]] = []


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/generate-prompt", response_model=FigmaPromptResponse)
async def generate_figma_prompt(
    request: FigmaPromptRequest,
    _current_user: dict = Depends(get_current_user),
):
    """
    Retrieve relevant Confluence context via semantic search, then call Claude
    on Bedrock to generate a Figma-design-focused prompt from the Jira story.
    """
    story = request.jira_story

    # 1. Build search query from story data
    parts = [story.title, story.description]
    if story.acceptance_criteria:
        parts.append(story.acceptance_criteria)
    search_query = " ".join(p.strip() for p in parts if p.strip())

    # 2. RAG: search Confluence only (synchronous — search_service is not async)
    try:
        results = search_service.semantic_search(
            project_id=request.project_id,
            query=search_query,
            limit=request.max_chunks,
            source_type="confluence",
            include_context=True,
        )
    except Exception as e:
        logger.warning(f"[FIGMA] Search error (continuing with no context): {e}")
        results = []

    # 3. Format context block
    if results:
        context_parts = []
        for i, r in enumerate(results, 1):
            context_parts.append(
                f"<confluence_source_{i}>\n"
                f"Title: {r['title']}\n"
                f"Content:\n{r['content']}\n"
                f"</confluence_source_{i}>"
            )
        context_block = "\n\n".join(context_parts)
    else:
        context_block = (
            "(No Confluence context found for this project — "
            "use sensible design defaults and mark assumptions as (inferred).)"
        )

    # 4. Build user message
    ac_text = story.acceptance_criteria.strip() if story.acceptance_criteria else "Not specified"
    user_message = f"""Generate a complete Figma design prompt for the following Jira story.

JIRA STORY
  Key:         {story.key}
  Type:        {story.type}
  Priority:    {story.priority}
  Title:       {story.title}
  Description: {story.description}
  Acceptance Criteria:
{ac_text}

CONFLUENCE CONTEXT (retrieved from project documentation):
{context_block}

Output ONLY the filled prompt block starting with the === header line. Zero placeholders remaining."""

    # 5. Deduplicate sources by source_id for the UI
    seen_ids: set = set()
    sources = []
    for r in results:
        if r["source_id"] not in seen_ids:
            seen_ids.add(r["source_id"])
            sources.append({
                "title": r["title"],
                "url": r.get("url", ""),
                "source_id": r["source_id"],
            })

    logger.info(f"[FIGMA] Generating prompt for story {story.key} in project {request.project_id}")
    for i, r in enumerate(results, 1):
        logger.info(f"[FIGMA] Chunk {i}: '{r['title']}' (source_id={r['source_id']}, similarity={r['similarity']:.3f}, url={r.get('url', 'N/A')})")
    prompt = _invoke_claude(FIGMA_SYSTEM_PROMPT, user_message)
    logger.info(f"[FIGMA] Prompt generated ({len(prompt)} chars), {len(sources)} unique sources")

    return FigmaPromptResponse(prompt=prompt, sources=sources)


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "figma"}


# ─── Figma API integration endpoints ─────────────────────────────────────────

class LinkFigmaRequest(BaseModel):
    pat: str
    team_id: str


@router.post("/link")
async def link_figma_account(
    request: LinkFigmaRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate Figma PAT + Team ID then save credentials to the users table."""
    service = FigmaService(request.pat, request.team_id)
    ok, err = service.test_connection()
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    update_user_figma_credentials(current_user["id"], request.pat, request.team_id)
    logger.info(f"[FIGMA] Credentials linked for user {current_user['id']}")
    return {"status": "linked"}


@router.get("/status")
async def get_figma_status(current_user: dict = Depends(get_current_user)):
    """Return whether the user has saved Figma credentials."""
    creds = get_user_figma_credentials(current_user["id"])
    if not creds or not creds.get("figma_pat"):
        return {"linked": False}
    return {
        "linked": True,
        "team_id": creds.get("figma_team_id"),
        "linked_at": str(creds.get("figma_linked_at")) if creds.get("figma_linked_at") else None,
    }


@router.get("/items")
async def get_figma_items(current_user: dict = Depends(get_current_user)):
    """
    Fetch all team projects and their files using stored PAT + Team ID.
    Returns nested list: projects → files (with thumbnail_url, key, last_modified).
    """
    creds = get_user_figma_credentials(current_user["id"])
    if not creds or not creds.get("figma_pat"):
        raise HTTPException(status_code=400, detail="Figma account not linked. Please enter your PAT and Team ID first.")

    service = FigmaService(creds["figma_pat"], creds["figma_team_id"])
    try:
        projects = service.get_team_projects()
        for project in projects:
            project["files"] = service.get_project_files(project["id"])
        logger.info(f"[FIGMA] Fetched {len(projects)} projects for user {current_user['id']}")
        return {"projects": projects}
    except Exception as e:
        logger.error(f"[FIGMA] Fetch items error: {e}")
        raise HTTPException(status_code=502, detail=f"Figma API error: {str(e)}")
