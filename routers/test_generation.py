"""
FastAPI Router for Test Scenario Generation
Generates test scenario documents from Confluence BRD pages using Claude (Bedrock).
Supports pushing the result back to Confluence as a new page.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
import logging
import boto3
import json
import os
import re
from html import unescape
from botocore.config import Config

from auth import verify_azure_token
from db_helper import get_user_atlassian_credentials, create_or_update_user, get_project
from services.confluence_service import ConfluenceService

router = APIRouter(prefix="/api/test", tags=["test"])
logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_READ_TIMEOUT = int(os.getenv("BEDROCK_READ_TIMEOUT", "300"))


# ============================================
# AUTHENTICATION DEPENDENCY
# ============================================

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
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
# REQUEST / RESPONSE MODELS
# ============================================

class GenerateTestScenariosRequest(BaseModel):
    confluence_page_id: str = Field(..., description="Confluence page ID of the BRD")
    project_id: str = Field(..., description="Project ID")


class PushToConfluenceRequest(BaseModel):
    project_id: str
    page_title: str
    content: str  # Markdown content from the editor
    parent_page_id: Optional[str] = None


# ============================================
# HELPER FUNCTIONS
# ============================================

def strip_html_tags(html_content: str) -> str:
    """Remove HTML tags and extract plain text from Confluence content"""
    text = re.sub(r'<[^>]+>', ' ', html_content)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _get_bedrock_client():
    """Get Bedrock runtime client with extended read timeout"""
    config = Config(
        read_timeout=BEDROCK_READ_TIMEOUT,
        connect_timeout=30,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    return boto3.client("bedrock-runtime", region_name=AWS_REGION, config=config)


def markdown_to_confluence_storage(markdown: str) -> str:
    """
    Convert markdown to Confluence storage format (HTML).
    Handles headings, bullet lists, ordered lists, tables, bold, horizontal rules, and paragraphs.
    """
    lines = markdown.split('\n')
    html_parts = []
    in_ul = False
    in_ol = False
    in_table = False
    table_header_done = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            html_parts.append('</ul>')
            in_ul = False
        if in_ol:
            html_parts.append('</ol>')
            in_ol = False

    def close_table():
        nonlocal in_table, table_header_done
        if in_table:
            html_parts.append('</tbody></table>')
            in_table = False
            table_header_done = False

    def apply_inline(text: str) -> str:
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
        return text

    for line in lines:
        # Horizontal rule
        if re.match(r'^-{3,}$', line.strip()) or re.match(r'^\*{3,}$', line.strip()):
            close_lists()
            close_table()
            html_parts.append('<hr/>')
            continue

        # Headings (#### first so ### doesn't match it)
        if line.startswith('#### '):
            close_lists(); close_table()
            html_parts.append(f'<h4>{apply_inline(line[5:].strip())}</h4>')
        elif line.startswith('### '):
            close_lists(); close_table()
            html_parts.append(f'<h3>{apply_inline(line[4:].strip())}</h3>')
        elif line.startswith('## '):
            close_lists(); close_table()
            html_parts.append(f'<h2>{apply_inline(line[3:].strip())}</h2>')
        elif line.startswith('# '):
            close_lists(); close_table()
            html_parts.append(f'<h1>{apply_inline(line[2:].strip())}</h1>')

        # Markdown table row (| col | col |)
        elif line.strip().startswith('|'):
            # Skip separator rows like |---|---|
            if re.match(r'^\|[\s\-|:]+\|$', line.strip()):
                table_header_done = True
                continue
            close_lists()
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if not in_table:
                html_parts.append('<table><tbody>')
                in_table = True
                table_header_done = False
                tag = 'th'
            else:
                tag = 'th' if not table_header_done else 'td'
            row_html = ''.join(f'<{tag}>{apply_inline(c)}</{tag}>' for c in cells)
            html_parts.append(f'<tr>{row_html}</tr>')
            if tag == 'th':
                table_header_done = True

        # Unordered list
        elif line.startswith('- ') or line.startswith('* '):
            close_table()
            if in_ol:
                html_parts.append('</ol>')
                in_ol = False
            if not in_ul:
                html_parts.append('<ul>')
                in_ul = True
            item = apply_inline(line[2:].strip())
            html_parts.append(f'<li>{item}</li>')

        # Ordered list (1. 2. 3.)
        elif re.match(r'^\d+\.\s', line):
            close_table()
            if in_ul:
                html_parts.append('</ul>')
                in_ul = False
            if not in_ol:
                html_parts.append('<ol>')
                in_ol = True
            item = apply_inline(re.sub(r'^\d+\.\s', '', line).strip())
            html_parts.append(f'<li>{item}</li>')

        # Blank line
        elif line.strip() == '':
            close_lists()
            close_table()

        # Regular paragraph
        else:
            close_lists()
            close_table()
            text = apply_inline(line.strip())
            if text:
                html_parts.append(f'<p>{text}</p>')

    close_lists()
    close_table()

    return ''.join(html_parts)


def generate_test_scenarios_with_bedrock(brd_content: str, page_title: str) -> str:
    """
    Use Bedrock (Claude) to generate a Test Scenario document from BRD content.
    Returns a markdown string.
    """
    plain_text = strip_html_tags(brd_content)

    prompt = f"""You are a senior QA analyst with 10+ years of experience writing test documentation for enterprise software.
You have been given a Business Requirements Document (BRD) and must produce a professional Test Scenario document.

BRD Title: {page_title}

BRD Content:
{plain_text}

---

Generate a complete Test Scenario document in clean markdown. Follow the EXACT structure and formatting below.

---

# Test Scenarios: {page_title}

**Document Version:** 1.0
**Based On:** {page_title} (BRD)
**Status:** Draft

---

## 1. Overview

Provide 2-3 sentences summarising the purpose of this test scenario document and what system/feature it covers.

---

## 2. Test Scope

List the functional areas and features that ARE in scope for testing. Use a bullet list. Be specific — reference module names, user roles, and key flows from the BRD.

---

## 3. Out of Scope

List what is explicitly NOT covered by these test scenarios (e.g., performance testing, third-party integrations not in BRD, infrastructure). Use a bullet list.

---

## 4. Assumptions & Dependencies

List any assumptions made while writing these scenarios (e.g., test data exists, environment is configured, user accounts are pre-created). Use a bullet list.

---

## 5. Test Scenarios

For EACH distinct functional requirement or feature area identified in the BRD, create a subsection. Within each subsection, write one or more test scenarios using the template below.

Numbering: TS-001, TS-002, TS-003 ... sequentially across the ENTIRE document (do not restart per section).

Use this EXACT template for every scenario:

### [Feature / Module Name from BRD]

#### TS-XXX: [Clear, action-oriented scenario title]

| Field | Details |
|---|---|
| **Scenario ID** | TS-XXX |
| **Requirement Ref** | [FR-XXX or section reference from the BRD] |
| **Priority** | High / Medium / Low |
| **Actor / Role** | [Who performs this action, e.g., End User, Admin, System] |

**Objective:** One sentence describing what this scenario verifies.

**Preconditions:**
- [Condition 1 that must be true before the test starts]
- [Condition 2]

**Happy Path (Expected Flow):**
1. [Step 1 — use present tense, be specific about inputs and actions]
2. [Step 2]
3. [Expected result / system response]

**Edge Cases:**
- [Boundary value or unusual but valid input]
- [Another edge case specific to this scenario]

**Negative / Error Cases:**
- [Invalid input or forbidden action and the expected error/response]
- [Another negative case]

**Expected Outcome:** A brief statement of what success looks like for this scenario.

---

RULES — follow all of these strictly:
1. Create AT LEAST one scenario for every functional requirement mentioned in the BRD
2. Group scenarios under the feature/module they belong to
3. TS-IDs are sequential across the whole document (TS-001, TS-002, TS-003 ...)
4. Reference actual field names, user roles, data values, and business rules from the BRD — do not be generic
5. Use the Markdown table for the scenario metadata fields
6. Keep Happy Path steps as a numbered list
7. Keep Edge Cases and Negative Cases as bullet lists
8. Use professional QA language — be precise and unambiguous
9. Output ONLY the markdown document — no preamble, no commentary, nothing outside the document
"""

    bedrock_client = _get_bedrock_client()
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 16000,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}]
    }

    logger.info(f"Calling Bedrock to generate test scenarios for: {page_title}")
    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body)
    )
    response_body = json.loads(response['body'].read())
    content = response_body['content'][0]['text']
    logger.info(f"Bedrock response received, length: {len(content)} characters")
    return content


# ============================================
# API ENDPOINTS
# ============================================

@router.post("/generate-from-confluence")
async def generate_test_scenarios(
    request: GenerateTestScenariosRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Generate a Test Scenario document from a Confluence BRD page.
    Returns editable markdown text.
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(status_code=400, detail="Atlassian account not linked. Please link your account first.")

    project = get_project(request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        page_data = confluence_service.get_page_content(request.confluence_page_id)
        logger.info(f"Fetched BRD page: {page_data['title']}")
    except Exception as e:
        logger.error(f"Error fetching Confluence page: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch Confluence page: {str(e)}")

    try:
        markdown_content = generate_test_scenarios_with_bedrock(
            page_data['content'],
            page_data['title']
        )
        return {
            "page_title": page_data['title'],
            "content": markdown_content
        }
    except Exception as e:
        logger.error(f"Error generating test scenarios: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate test scenarios: {str(e)}")


@router.post("/push-to-confluence")
async def push_test_scenarios_to_confluence(
    request: PushToConfluenceRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Push the (edited) test scenario markdown document as a new Confluence page.
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(status_code=400, detail="Atlassian account not linked.")

    project = get_project(request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    space_key = project.get('confluence_space_key')
    if not space_key:
        raise HTTPException(status_code=400, detail="Project has no Confluence space configured.")

    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        confluence_html = markdown_to_confluence_storage(request.content)
        page = confluence_service.create_page(
            space_key=space_key,
            title=request.page_title,
            content=confluence_html,
            parent_id=request.parent_page_id
        )
        logger.info(f"Created Confluence page: {page['title']} (ID: {page['id']})")
        return {
            "page_id": page['id'],
            "page_title": page['title'],
            "web_url": page['web_url']
        }
    except Exception as e:
        logger.error(f"Error pushing to Confluence: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to push to Confluence: {str(e)}")
