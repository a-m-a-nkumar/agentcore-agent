"""
FastAPI Router for Test Scenario Generation
Generates test scenario documents from Confluence BRD pages using Claude (Bedrock).
Supports pushing the result back to Confluence as a new page.
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import asyncio
import logging
import boto3
import json
import os
import re
import uuid
from datetime import datetime
from html import unescape
from botocore.config import Config

from auth import verify_azure_token
from db_helper import get_user_atlassian_credentials, create_or_update_user, get_project
from services.confluence_service import ConfluenceService
from services.github_service import GitHubService

router = APIRouter(prefix="/api/test", tags=["test"])
logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
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
    content: str  # Markdown or Gherkin content from the editor
    parent_page_id: Optional[str] = None
    source_scenario_page: Optional[str] = None  # Title of the source BRD scenario page
    coverage_summary: Optional[str] = None  # JSON string of coverage data


class FeatureFile(BaseModel):
    filename: str
    content: str


class PushToGitHubRequest(BaseModel):
    project_id: str
    github_token: str = Field(..., description="GitHub PAT with repo scope")
    repo_url: str = Field(..., description="GitHub repository URL or owner/repo")
    feature_files: List[FeatureFile]
    branch: str = "test/auto-generated"
    base_path: str = "tests/features"
    create_pr: bool = True


class ParseScenariosRequest(BaseModel):
    confluence_page_id: str = Field(..., description="Confluence page ID of the test scenario document")
    project_id: str = Field(..., description="Project ID")


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


def _strip_trailing_notes(content: str) -> str:
    """Remove trailing placeholder/continuation notes Claude adds when hitting token limit."""
    patterns = [
        r'\n+\[[^\]]{10,}\]\s*$',
        r'\n+\([^)]{10,}\)\s*$',
        r'\n+[^\n]{0,300}(continue|remaining|length limit|token limit|same format|same detailed format|subsequent scenario|following the same|proceed with|would you like|I can add|I have covered)[^\n]*\s*$',
    ]
    result = content
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    return result.rstrip()


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
6. Keep Happy Path steps as a numbered list (max 5 steps)
7. Keep Edge Cases and Negative Cases as bullet lists (max 3 bullets each)
8. Use professional QA language — be precise and unambiguous
9. Output ONLY the markdown document — no preamble, no commentary, nothing outside the document
10. NEVER truncate, summarise, or add placeholder notes like "[Continue with...]" or "[Due to length...]" — you MUST complete every single scenario fully
11. Be concise in each scenario — short, precise sentences only. Do not pad with unnecessary explanation.
"""

    bedrock_client = _get_bedrock_client()
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
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
    content = _strip_trailing_notes(content)
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


def extract_scenarios_with_bedrock(raw_content: str, page_title: str) -> dict:
    """
    Use Bedrock (Claude) to extract structured test scenarios from raw
    Confluence page content and generate a clean Gherkin prompt.
    Returns { scenarios: [...], prompt: "..." }
    """
    plain_text = strip_html_tags(raw_content)

    extraction_prompt = f"""You are a senior QA engineer. I will give you the raw text content of a test scenario document from Confluence titled "{page_title}".

Your task:
1. Extract ONLY fully defined test scenarios from this document. A fully defined scenario has a structured format with fields like Scenario ID, Requirement Ref, Priority, Objective, Preconditions, Happy Path steps, Edge Cases, and Negative Cases. Do NOT extract items that are merely listed in scope sections, bullet lists, or placeholder notes.
2. For each fully defined scenario, extract its ID (like TS-001, SC-01, etc.), title/name, and a one-line description (the Objective or Description field).
3. Then generate a detailed, implementation-level Gherkin generation prompt that a developer will paste into their AI IDE (Cursor, Copilot, Claude Code, etc.).

IMPORTANT CONTEXT: The prompt will be used INSIDE an AI IDE that already has the codebase open. The AI IDE can see all the code in the project automatically. So the prompt must NOT ask the user to paste code — instead it should tell the AI to analyse the code in the current project/workspace.

RAW DOCUMENT CONTENT:
{plain_text}

RESPOND WITH EXACTLY THIS JSON FORMAT (no markdown fences, no commentary):
{{
  "scenarios": [
    {{
      "id": "TS-001",
      "name": "Language Detection and Response",
      "description": "Verify that the system correctly detects and responds in all 12 supported languages"
    }},
    {{
      "id": "TS-002",
      "name": "Real-time Sentiment Detection",
      "description": "Verify that the system accurately analyzes customer sentiment in real-time"
    }}
  ],
  "prompt": "You are a senior QA automation expert specializing in BDD test case generation.\\n\\nYOUR TASK:\\nAnalyse ALL the code in this project (services, controllers, routes, models, utils — everything) and generate implementation-level test cases in Gherkin format (.feature file syntax).\\n\\nBRD TEST SCENARIOS:\\nBelow are test scenarios derived from the BRD \\"{page_title}\\". These define WHAT needs to be tested at a business level:\\n\\n  TS-001: Language Detection and Response\\n    → Verify that the system correctly detects and responds in all 12 supported languages\\n  TS-002: Real-time Sentiment Detection\\n    → Verify that the system accurately analyzes customer sentiment in real-time\\n\\nCRITICAL INSTRUCTIONS:\\n\\n1. CODE-FIRST APPROACH: Scan the entire codebase first. Identify which features are actually implemented. ONLY generate test cases for scenarios whose functionality EXISTS in the code. If a scenario's feature is not implemented, SKIP it entirely.\\n\\n2. IMPLEMENTATION-LEVEL GHERKIN: Do NOT write generic business-level Gherkin. Your Given/When/Then steps MUST reference actual implementation details found in the code:\\n   - Real API endpoints (e.g., POST /api/v1/detect-language)\\n   - Real function/service names (e.g., LanguageDetectionService)\\n   - Real request/response fields (e.g., \\"detected_language\\", \\"confidence_score\\")\\n   - Real database models or schemas if relevant\\n   - Real error codes and messages from the codebase\\n\\n3. TAG each test case with its scenario ID: @TS-XXX @regression\\n\\n4. For each covered scenario, generate:\\n   - Happy path (main success flow with real data)\\n   - Edge cases (boundary values, empty inputs, max lengths, concurrent requests)\\n   - Negative/error conditions (invalid inputs, service failures, timeout handling)\\n\\n5. OUTPUT FORMAT — valid Gherkin (.feature file), one feature per scenario:\\n\\n   @TS-001 @regression\\n   Feature: Language Detection and Response\\n\\n     Background:\\n       Given the language detection service is running\\n       And the NLP models for all 12 languages are loaded\\n\\n     Scenario: Successfully detect Spanish input\\n       When I send a POST request to \\"/api/v1/detect-language\\" with body:\\n         \\"\\"\\"\\n         {{\\"text\\": \\"Hola, necesito ayuda con mi pedido\\"}}\\n         \\"\\"\\"\\n       Then the response status should be 200\\n       And the response field \\"detected_language\\" should be \\"es\\"\\n       And the response field \\"confidence\\" should be greater than 0.95\\n\\n     Scenario: Reject unsupported language\\n       When I send a POST request to \\"/api/v1/detect-language\\" with body:\\n         \\"\\"\\"\\n         {{\\"text\\": \\"unsupported text\\"}}\\n         \\"\\"\\"\\n       Then the response status should be 422\\n       And the response field \\"error\\" should contain \\"unsupported_language\\"\\n\\n6. COVERAGE SUMMARY — at the end, provide:\\n   - ✅ Covered: List each TS-ID, what code implements it, and how many test cases generated\\n   - ❌ Skipped: List each TS-ID that was skipped and WHY (feature not found in code)\\n   - 📊 Overall: X of Y scenarios covered\\n\\nIMPORTANT REMINDERS:\\n- Do NOT hallucinate endpoints or functions that don't exist in the code\\n- Do NOT generate test cases for features that aren't implemented\\n- Every Given/When/Then step should be traceable to actual code\\n- Use realistic test data that matches the codebase's data models\\n- If the project uses specific testing frameworks or patterns, follow those conventions"
}}

RULES:
- ONLY extract scenarios that are FULLY DEFINED with structured fields (Scenario ID, Objective, Preconditions, Happy Path, etc.)
- Do NOT extract items that only appear in "Test Scope" sections, bullet lists, or placeholder notes like "[Continue with additional scenarios for FR-03 through FR-22...]"
- Do NOT extract requirement references (FR-XX) that are merely listed but lack a complete scenario definition with steps
- If the document says "TS-001: Language Detection" with a full table of fields, Objective, Preconditions, Happy Path — that IS a scenario. If it just says "FR-05: Platform Integrations" in a scope list — that is NOT a scenario.
- The "prompt" field must be a COMPLETE, ready-to-use prompt string with ONLY the fully defined scenarios injected into it
- The prompt MUST follow the detailed implementation-level format shown above — NOT the shorter generic format
- The prompt must NOT ask the user to paste or provide code — the AI IDE already has the code open
- The prompt must say "Analyse ALL the code in this project" and use the CODE-FIRST APPROACH instruction
- The prompt must instruct the AI to write IMPLEMENTATION-LEVEL Gherkin referencing real endpoints, functions, fields, error codes
- The prompt must include the Background section example showing real API endpoint usage
- Use \\n for newlines in the JSON string values
- Include the exact scenario IDs from the document (TS-001, SC-01, etc.)
- The prompt must instruct the AI to tag Gherkin output with @TS-XXX or @SC-XX tags
- The coverage summary must use the emoji format: ✅ Covered, ❌ Skipped, 📊 Overall
- Do NOT include "[PASTE YOUR CODE BELOW]" or similar — the AI IDE handles code context automatically
- Output ONLY valid JSON, nothing else
"""

    bedrock_client = _get_bedrock_client()
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": extraction_prompt}]
    }

    logger.info(f"Calling Bedrock to extract scenarios from: {page_title}")
    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(request_body)
    )
    response_body = json.loads(response['body'].read())
    content_text = response_body['content'][0]['text']
    logger.info(f"Bedrock extraction response length: {len(content_text)} chars")

    # Parse JSON response — strip markdown fences if present
    cleaned = content_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)

    result = json.loads(cleaned)
    return result


@router.post("/parse-scenarios")
async def parse_scenarios_from_confluence(
    request: ParseScenariosRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Extract structured test scenarios from a Confluence page using Bedrock.
    Returns parsed scenario list + a ready-to-use Gherkin generation prompt.
    """
    credentials = get_user_atlassian_credentials(current_user['id'])
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(status_code=400, detail="Atlassian account not linked.")

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
        logger.info(f"Fetched scenario page: {page_data['title']}")
    except Exception as e:
        logger.error(f"Error fetching Confluence page: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch Confluence page: {str(e)}")

    try:
        result = extract_scenarios_with_bedrock(
            page_data['content'],
            page_data['title']
        )
        return {
            "page_title": page_data['title'],
            "scenarios": result.get("scenarios", []),
            "prompt": result.get("prompt", ""),
        }
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Bedrock JSON response: {e}")
        raise HTTPException(status_code=500, detail="AI returned invalid format. Please try again.")
    except Exception as e:
        logger.error(f"Error extracting scenarios: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse scenarios: {str(e)}")


def _gherkin_to_confluence_html(gherkin: str, source_page: str = None, coverage: str = None) -> str:
    """
    Convert Gherkin text to well-formatted Confluence storage HTML.
    Wraps in a code macro for readability and adds metadata panel.
    """
    parts = []

    # Metadata panel
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    parts.append('<ac:structured-macro ac:name="info"><ac:rich-text-body>')
    parts.append(f'<p><strong>Generated:</strong> {now}</p>')
    if source_page:
        parts.append(f'<p><strong>Source BRD Scenarios:</strong> {source_page}</p>')
    if coverage:
        parts.append(f'<p><strong>Coverage:</strong> {coverage}</p>')
    parts.append('</ac:rich-text-body></ac:structured-macro>')

    # Gherkin content in a code block macro
    parts.append(
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">gherkin</ac:parameter>'
        '<ac:plain-text-body><![CDATA['
    )
    parts.append(gherkin)
    parts.append(']]></ac:plain-text-body></ac:structured-macro>')

    return ''.join(parts)


@router.post("/push-to-confluence")
async def push_test_scenarios_to_confluence(
    request: PushToConfluenceRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Push Gherkin test cases or test scenario markdown to a new Confluence page.
    Includes metadata: source BRD page, coverage summary, generation date.
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

        # Detect if content is Gherkin (has Feature:/Scenario: keywords) vs markdown
        is_gherkin = bool(re.search(r'^\s*(Feature|Scenario):', request.content, re.MULTILINE))

        if is_gherkin:
            confluence_html = _gherkin_to_confluence_html(
                request.content,
                source_page=request.source_scenario_page,
                coverage=request.coverage_summary,
            )
        else:
            confluence_html = markdown_to_confluence_storage(request.content)

        existing_page = confluence_service.find_page_by_title(space_key, request.page_title)
        if existing_page:
            page = confluence_service.update_page(
                page_id=existing_page['id'],
                title=request.page_title,
                content=confluence_html,
                current_version=existing_page['version']['number']
            )
            logger.info(f"Updated existing Confluence page: {page['title']} (ID: {page['id']})")
        else:
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


# ============================================
# GITHUB PUSH ENDPOINT
# ============================================

@router.post("/push-to-github")
async def push_feature_files_to_github(
    request: PushToGitHubRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Push .feature files to a GitHub repository.
    Creates a branch, commits the files, and optionally opens a PR.
    """
    project = get_project(request.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not request.feature_files:
        raise HTTPException(status_code=400, detail="No feature files provided")

    try:
        github_service = GitHubService(request.github_token)

        # Validate token
        user_info = github_service.test_connection()
        logger.info(f"GitHub authenticated as: {user_info['login']}")

        result = github_service.push_feature_files(
            repo_url=request.repo_url,
            feature_files=[ff.dict() for ff in request.feature_files],
            branch=request.branch,
            base_path=request.base_path,
            create_pr=request.create_pr,
        )

        return {
            "success": True,
            "branch": result["branch"],
            "branch_url": result["branch_url"],
            "files": result["files"],
            "pr_url": result.get("pr_url"),
            "pr_number": result.get("pr_number"),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error pushing to GitHub: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to push to GitHub: {str(e)}")


# ============================================
# MCP INTERNAL ENDPOINTS (API Key Auth)
# ============================================

# In-memory session store for MCP test sessions
_test_sessions: dict = {}           # session_id → { project_id, scenarios, prompt, gherkin, ... }
_project_events: dict = {}          # project_id → asyncio.Event


def _validate_api_key(x_api_key: str) -> str:
    """Validate API key and return associated project_id."""
    internal_keys_str = os.environ.get("INTERNAL_API_KEYS", "{}")
    try:
        valid_keys = json.loads(internal_keys_str)
    except json.JSONDecodeError:
        logger.error("Failed to parse INTERNAL_API_KEYS from env")
        valid_keys = {}

    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return valid_keys[x_api_key]  # returns project_id


class ListPagesInternalRequest(BaseModel):
    project_id: Optional[str] = None
    filter: Optional[str] = "test scenario"


class ParseScenariosInternalRequest(BaseModel):
    confluence_page_id: str
    project_id: Optional[str] = None


class SubmitGherkinInternalRequest(BaseModel):
    project_id: Optional[str] = None
    gherkin: str
    session_id: Optional[str] = None


@router.post("/list-pages-internal")
async def list_pages_internal(
    request: ListPagesInternalRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    List Confluence pages for a project. Used by MCP tool.
    Resolves Atlassian credentials from the project owner.
    """
    key_project_id = _validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get project owner's Atlassian credentials
    owner_id = project.get("user_id")
    if not owner_id:
        raise HTTPException(status_code=400, detail="Project has no owner")

    credentials = get_user_atlassian_credentials(owner_id)
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(status_code=400, detail="Project owner has no linked Atlassian account")

    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        space_key = project.get("confluence_space_key", "SO")
        all_pages = confluence_service.get_content_pages(space_key=space_key, limit=500)

        # Filter to test scenario pages if filter provided
        filter_term = (request.filter or "").lower()
        if filter_term:
            pages = [
                {"id": p["id"], "title": p["title"]}
                for p in all_pages
                if filter_term in p.get("title", "").lower()
            ]
        else:
            pages = [{"id": p["id"], "title": p["title"]} for p in all_pages]

        return {"pages": pages, "total": len(pages)}
    except Exception as e:
        logger.error(f"Error listing pages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-scenarios-internal")
async def parse_scenarios_internal(
    request: ParseScenariosInternalRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Parse test scenarios from a Confluence page. Used by MCP tool.
    Returns session_id + prompt for the AI IDE to generate .feature files.
    """
    key_project_id = _validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    owner_id = project.get("user_id")
    if not owner_id:
        raise HTTPException(status_code=400, detail="Project has no owner")

    credentials = get_user_atlassian_credentials(owner_id)
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(status_code=400, detail="Project owner has no linked Atlassian account")

    try:
        confluence_service = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        page_data = confluence_service.get_page_content(request.confluence_page_id)
        logger.info(f"[MCP] Fetched scenario page: {page_data['title']}")
    except Exception as e:
        logger.error(f"[MCP] Error fetching Confluence page: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch Confluence page: {str(e)}")

    try:
        result = extract_scenarios_with_bedrock(
            page_data["content"],
            page_data["title"],
        )

        session_id = str(uuid.uuid4())
        _test_sessions[session_id] = {
            "project_id": project_id,
            "page_title": page_data["title"],
            "scenarios": result.get("scenarios", []),
            "prompt": result.get("prompt", ""),
            "gherkin": None,
        }

        return {
            "session_id": session_id,
            "page_title": page_data["title"],
            "scenarios": result.get("scenarios", []),
            "prompt": result.get("prompt", ""),
        }
    except Exception as e:
        logger.error(f"[MCP] Error extracting scenarios: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse scenarios: {str(e)}")


@router.post("/submit-gherkin-internal")
async def submit_gherkin_internal(
    request: SubmitGherkinInternalRequest,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """
    Submit generated Gherkin from AI IDE back to the platform.
    Signals the SSE listener so the frontend auto-populates.
    """
    key_project_id = _validate_api_key(x_api_key)
    project_id = request.project_id or key_project_id

    session_id = request.session_id or str(uuid.uuid4())

    # Store gherkin in session
    if session_id in _test_sessions:
        _test_sessions[session_id]["gherkin"] = request.gherkin
    else:
        _test_sessions[session_id] = {
            "project_id": project_id,
            "gherkin": request.gherkin,
        }

    # Signal the SSE listener for this project
    if project_id not in _project_events:
        _project_events[project_id] = asyncio.Event()
    _project_events[project_id].set()

    logger.info(f"[MCP] Gherkin submitted for project {project_id}, session {session_id}")
    return {"session_id": session_id, "status": "received"}


@router.get("/listen/{project_id}")
async def listen_for_test_cases(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    SSE endpoint for the frontend to listen for test cases from MCP.
    Uses Azure AD auth (this is called by the frontend, not MCP).
    """
    async def event_generator():
        if project_id not in _project_events:
            _project_events[project_id] = asyncio.Event()

        event = _project_events[project_id]

        try:
            while True:
                # Wait for event with timeout for heartbeat
                try:
                    await asyncio.wait_for(event.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                    continue

                # Event fired — find the session with gherkin for this project
                event.clear()
                for sid, session in _test_sessions.items():
                    if session.get("project_id") == project_id and session.get("gherkin"):
                        yield f"data: {json.dumps({'type': 'gherkin_received', 'gherkin': session['gherkin'], 'session_id': sid})}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        return
        except asyncio.CancelledError:
            logger.info(f"[SSE] Client disconnected from listen/{project_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
