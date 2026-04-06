import os
import uuid
import hashlib
import json
import re
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from docx import Document
import io
import jwt
from jwt import PyJWKClient
from functools import wraps
from typing import Optional, List
import requests
from datetime import datetime

# Import API routers
from routers.projects import router as projects_router
from routers.sessions import router as sessions_router
from routers.integrations import router as integrations_router
from routers.sync import router as sync_router
from routers.jira_generation import router as jira_generation_router
from routers.orchestration import router as orchestration_router

# Import database helpers for session persistence
from db_helper import save_project_brd_session
from services.s3_service import s3_put_object, get_s3_client

load_dotenv(override=True)

# When using AWS_PROFILE (SSO), clear any stale STS credentials from the environment
# so boto3 resolves credentials via the SSO profile instead of expired keys
if os.getenv("AWS_PROFILE"):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        os.environ.pop(key, None)

app = FastAPI(root_path=os.getenv("ROOT_PATH", ""))

# Load BMAD config (optional prompt overlay)
def _load_bmad_config():
    try:
        config_path = os.path.join(os.path.dirname(__file__), "bmad_agent_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

BMAD_CONFIG = _load_bmad_config()

def _build_bmad_prompt(base_prompt: str, workflow_key: str = "create-prd") -> str:
    """
    Prepend BMAD persona/principles/workflow prompt to the base prompt if config is available.
    Falls back to the base prompt unchanged if config is missing.
    """
    if not BMAD_CONFIG:
        return base_prompt

    persona = BMAD_CONFIG.get("agent", {}).get("persona", "").strip()
    principles = BMAD_CONFIG.get("agent", {}).get("principles", [])
    workflow = BMAD_CONFIG.get("workflows", {}).get(workflow_key, {})
    workflow_prompt = workflow.get("prompt", "").strip()

    parts = []
    if persona:
        parts.append(persona)
    if principles:
        parts.append("PRINCIPLES:\n" + "\n".join(f"- {p}" for p in principles))
    if workflow_prompt:
        parts.append(f"WORKFLOW: {workflow_key}\n{workflow_prompt}")
    parts.append(base_prompt)

    return "\n\n".join(parts)

# Add CORS middleware to allow frontend on localhost:8080
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:5173", "http://127.0.0.1:8080", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "Authorization", "Content-Type"],
    expose_headers=["*"],
)

# Register API routers
app.include_router(projects_router)
app.include_router(sessions_router)
app.include_router(integrations_router)
app.include_router(sync_router)
app.include_router(orchestration_router)
app.include_router(jira_generation_router)

# Add request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests for debugging"""
    if (request.url.path.startswith("/api/upload-transcript") or 
        request.url.path.startswith("/api/chat") or 
        request.url.path.startswith("/api/analyst-chat")):
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        print(f"\n[REQUEST] {request.method} {request.url.path}")
        print(f"[REQUEST] Authorization header present: {bool(auth_header)}")
        if auth_header:
            print(f"[REQUEST] Auth header (first 30 chars): {auth_header[:30]}...")
        else:
            print(f"[REQUEST] All headers: {list(request.headers.keys())}")
    
    response = await call_next(request)
    return response

# Configuration (from .env)
AGENT_ARN = os.getenv("AGENT_ARN", "arn:aws:bedrock-agentcore:us-east-1:590184044598:runtime/pm_agent-uDlkiNFagv")
ANALYST_AGENT_ARN = os.getenv("ANALYST_AGENT_ARN", "arn:aws:bedrock-agentcore:us-east-1:590184044598:runtime/analyst_agent-JAa3wMFKOK")
REGION = os.getenv("AWS_REGION", "us-east-1")

# Log agent ARNs on startup
print(f"\n[CONFIG] Agent ARN: {AGENT_ARN}")
print(f"[CONFIG] Analyst Agent ARN: {ANALYST_AGENT_ARN}")
print(f"[CONFIG] Region: {REGION}\n")

# Azure AD Configuration
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")

# Import authentication functions
from auth import (
    verify_azure_token,
    store_user_identity_in_agentcore,
    check_brd_access_via_agentcore,
    grant_brd_access_via_agentcore,
    revoke_brd_access_via_agentcore,
    extract_user_groups,
    compute_allowed_modules,
    require_module,
    BUSINESS_GROUP_OID,
    TECH_GROUP_OID,
    GROUP_MODULE_MAP,
)

# Setup templates
templates = Jinja2Templates(directory="templates")

# Exception handler for validation errors
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log detailed validation errors"""
    print(f"\n[VALIDATION ERROR] Path: {request.url.path}")
    print(f"[VALIDATION ERROR] Details: {json.dumps(exc.errors(), indent=2)}")
    try:
        body = await request.json()
        print(f"[VALIDATION ERROR] Body: {json.dumps(body, indent=2)}")
    except Exception:
        print(f"[VALIDATION ERROR] Body: <could not parse json>")
        
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": "Check server logs for details"},
    )

# Helper function to check AWS credentials
def check_aws_credentials():
    """Check if AWS credentials are valid"""
    try:
        sts_client = boto3.client('sts', region_name=REGION)
        identity = sts_client.get_caller_identity()
        return True, identity
    except Exception as e:
        return False, str(e)

def get_agent_core_client():
    """Get a fresh AgentCore client with increased timeout for long-running operations"""
    from botocore.config import Config
    # Increase timeout to 5 minutes (300 seconds) for BRD generation
    config = Config(
        read_timeout=300,
        connect_timeout=10,
        retries={'max_attempts': 3}
    )
    return boto3.client('bedrock-agentcore', region_name=REGION, config=config)

def get_lambda_client():
    """Get a fresh Lambda client with extended timeout for long-running Lambda functions"""
    from botocore.config import Config
    # Increase timeout to 15 minutes (900 seconds) - max Lambda execution time
    config = Config(
        read_timeout=900,
        connect_timeout=60,
        retries={'max_attempts': 0}  # Don't retry on timeout - Lambda is already processing
    )
    return boto3.client('lambda', region_name=REGION, config=config)

def get_agentcore_identity_client():
    """Get AgentCore Identity client"""
    return boto3.client('bedrock-agentcore', region_name=REGION)

# -------------------------
# Azure AD Token Verification
# -------------------------



# -------------------------
# Authentication Decorator
# -------------------------

async def get_current_user(request: Request) -> dict:
    """FastAPI dependency to get current authenticated user"""
    # Get authorization header (case-insensitive)
    authorization = request.headers.get("authorization") or request.headers.get("Authorization")
    
    if not authorization:
        print(f"[AUTH] Authorization header missing. Headers: {list(request.headers.keys())}")
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    if not authorization.startswith("Bearer "):
        print(f"[AUTH] Invalid authorization header format: {authorization[:20]}...")
        raise HTTPException(status_code=401, detail="Invalid authorization header format")
    
    token = authorization.replace("Bearer ", "").strip()
    print(f"[AUTH] Token received (first 20 chars): {token[:20]}...")
    
    try:
        user_info = verify_azure_token(token)
        print(f"[AUTH] Token verified successfully for user: {user_info.get('email') or user_info.get('preferred_username')}")
    except HTTPException as e:
        print(f"[AUTH] Token verification failed: {e.detail}")
        raise
    except Exception as e:
        print(f"[AUTH] Unexpected error during token verification: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")
    
    user_id = user_info.get("oid") or user_info.get("sub")
    email = user_info.get("email") or user_info.get("preferred_username")
    name = user_info.get("name")

    print(f"[AUTH] User ID: {user_id}, Email: {email}")

    # Extract Azure AD group memberships and compute module access
    groups = extract_user_groups(user_info)
    allowed_modules = compute_allowed_modules(groups)
    print(f"[AUTH] Groups: {groups}, Allowed modules: {allowed_modules}")

    # Store user identity in AgentCore if not exists
    try:
        store_user_identity_in_agentcore(
            user_id=user_id,
            email=email,
            name=name or ""
        )
    except Exception as e:
        print(f"[AUTH] Warning: Failed to store user identity in AgentCore: {e}")
        # Don't fail the request if identity storage fails

    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "token": token,
        "groups": groups,
        "allowed_modules": allowed_modules,
    }

def render_brd_json_to_text(brd_data: dict) -> str:
    """Render structured BRD JSON into readable plain text (matches lambda_brd_chat.py format).
    Skips # In Scope and # Out of Scope as separate sections - they are subsections of Scope."""
    # Check if BRD uses sections format (newer format)
    if "sections" in brd_data:
        sections = brd_data.get("sections", [])
        lines = []
        lines.append("Business Requirements Document (BRD)")
        lines.append("")

        has_doc_title = False
        start_idx = 0
        if sections:
            first_title = (sections[0].get("title", "") or "").lower()
            if "ai-powered" in first_title or "brd" in first_title or (
                len(first_title) < 30 and not re.match(r'^\d+\.', first_title)
            ):
                has_doc_title = True
                start_idx = 1
                lines.append(sections[0].get("title", ""))
                lines.append("")

        section_counter = 1
        idx = start_idx
        while idx < len(sections):
            section = sections[idx]
            title = section.get("title", f"Section {section_counter}")
            title_lower = (title or "").lower().strip()
            if title.strip().startswith("#") and ("in scope" in title_lower or "out of scope" in title_lower):
                idx += 1
                continue
            title_clean = re.sub(r'^\d+\.\s*', '', title).strip()
            lines.append(f"{section_counter}. {title_clean}")
            lines.append("")

            content_blocks = list(section.get("content", []))
            if "scope" in title_lower and (not content_blocks or len(content_blocks) == 0):
                for i in (1, 2):
                    sub_idx = idx + i
                    if sub_idx < len(sections):
                        sub = sections[sub_idx]
                        sub_title = (sub.get("title", "") or "").lower()
                        if "# in scope" in sub_title:
                            content_blocks.append({"type": "paragraph", "text": "### In Scope"})
                            content_blocks.extend(sub.get("content", []))
                        elif "# out of scope" in sub_title:
                            content_blocks.append({"type": "paragraph", "text": "### Out of Scope"})
                            content_blocks.extend(sub.get("content", []))

            for block in content_blocks:
                block_type = block.get("type") if isinstance(block, dict) else None
                if block_type == "paragraph":
                    lines.append(block.get("text", "").strip())
                    lines.append("")
                elif block_type == "bullet":
                    for item in block.get("items", []):
                        lines.append(f"- {item}")
                    lines.append("")
                elif block_type == "table":
                    rows = block.get("rows", [])
                    if rows:
                        header = rows[0]
                        header_line = " | ".join(str(col) for col in header)
                        lines.append(header_line)
                        lines.append("-" * len(header_line))
                        for row in rows[1:]:
                            lines.append(" | ".join(str(col) for col in row))
                    lines.append("")
            section_counter += 1
            idx += 1
        return "\n".join(line.rstrip() for line in lines).rstrip() + "\n"
    
    # Fallback: Try to render as plain text if it's already text
    if isinstance(brd_data, str):
        return brd_data
    
    # Fallback: Convert to JSON string if all else fails
    return json.dumps(brd_data, indent=2, ensure_ascii=False)

def clean_markdown_text(text: str) -> str:
    """Remove markdown syntax from text"""
    if not text:
        return ""
    
    # Remove markdown headers (# ## ###) - but preserve the text after
    # Match: # Text or ## Text or ### Text
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    
    # Remove horizontal rules (---) on their own line
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    
    # Remove bold markdown (**text** or __text__) - handle nested cases
    # Match **text** but not ***text*** (that's bold+italic)
    text = re.sub(r'\*\*([^*]+?)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+?)__', r'\1', text)
    
    # Remove italic markdown (*text* or _text_) - but preserve list markers (- item)
    # Only match if not at start of line with space after
    text = re.sub(r'(?<!^)(?<!\n)(?<!\s)\*([^*\n\s]+?)\*(?!\s)', r'\1', text)
    text = re.sub(r'(?<!^)(?<!\n)(?<!\s)_([^_\n\s]+?)_(?!\s)', r'\1', text)
    
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    
    # Remove markdown table separators (|---|---| or |---|)
    text = re.sub(r'^\|?[\s\-|:]+\|?\s*$', '', text, flags=re.MULTILINE)
    
    # Clean up extra whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    return text

def parse_markdown_table(text: str):
    """Parse markdown table format into rows"""
    import re
    lines = text.strip().split('\n')
    rows = []
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('|---'):
            continue
        
        # Split by | and clean up
        cells = [cell.strip() for cell in line.split('|')]
        # Remove empty first/last cells from split
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    
    return rows if rows else None

def render_brd_json_to_docx(brd_data) -> bytes:
    """Render structured BRD JSON or text into DOCX format with clean formatting
    
    Args:
        brd_data: Can be a dict (JSON structure) or str (plain text)
    """
    doc = Document()
    
    # Add title
    doc.add_heading('Business Requirements Document (BRD)', 0)
    
    # Check if BRD uses sections format (newer format)
    if isinstance(brd_data, dict) and "sections" in brd_data:
        sections = brd_data.get("sections", [])
        
        for idx, section in enumerate(sections, start=1):
            # Add section title as heading (clean markdown)
            section_title = section.get("title", f"Section {idx}")
            section_title = clean_markdown_text(section_title)
            doc.add_heading(section_title, level=1)
            
            # Process content blocks
            for block in section.get("content", []):
                block_type = block.get("type")
                
                if block_type == "paragraph":
                    # Add paragraph text (clean markdown headers but keep formatting)
                    text = block.get("text", "").strip()
                    if text:
                        # Clean headers only
                        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
                        
                        # Check if paragraph contains markdown table (multi-line)
                        if '\n' in text and '|' in text and text.count('|') >= 2:
                            # Multi-line table in paragraph
                            lines_in_text = text.split('\n')
                            table_lines = []
                            regular_lines = []
                            
                            for txt_line in lines_in_text:
                                if '|' in txt_line and txt_line.count('|') >= 2 and not txt_line.strip().startswith('---'):
                                    table_lines.append(txt_line)
                                elif not txt_line.strip().startswith('---'):
                                    regular_lines.append(txt_line)
                            
                            # Process table if found
                            if table_lines:
                                table_data = parse_markdown_table('\n'.join(table_lines))
                                if table_data and len(table_data) > 0:
                                    max_cols = max(len(row) for row in table_data)
                                    table = doc.add_table(rows=len(table_data), cols=max_cols)
                                    table.style = 'Light Grid Accent 1'
                                    for row_idx, row_data in enumerate(table_data):
                                        for col_idx, cell_data in enumerate(row_data):
                                            if col_idx < len(table.rows[row_idx].cells):
                                                cell = table.rows[row_idx].cells[col_idx]
                                                cell.text = clean_markdown_text(str(cell_data))
                                    if len(table_data) > 0:
                                        header_cells = table.rows[0].cells
                                        for cell in header_cells:
                                            for paragraph in cell.paragraphs:
                                                for run in paragraph.runs:
                                                    run.bold = True
                            
                            # Process regular lines
                            for reg_line in regular_lines:
                                cleaned = clean_markdown_text(reg_line)
                                if cleaned:
                                    doc.add_paragraph(cleaned)
                        # Check if it's a single-line markdown table
                        elif '|' in text and text.count('|') >= 2:
                            table_rows = parse_markdown_table(text)
                            if table_rows and len(table_rows) > 0:
                                # Create Word table
                                max_cols = max(len(row) for row in table_rows)
                                table = doc.add_table(rows=len(table_rows), cols=max_cols)
                                table.style = 'Light Grid Accent 1'
                                
                                for row_idx, row_data in enumerate(table_rows):
                                    for col_idx, cell_data in enumerate(row_data):
                                        if col_idx < len(table.rows[row_idx].cells):
                                            cell = table.rows[row_idx].cells[col_idx]
                                            # Clean markdown from cell text
                                            cell.text = clean_markdown_text(str(cell_data))
                                
                                # Make header row bold
                                if len(table_rows) > 0:
                                    header_cells = table.rows[0].cells
                                    for cell in header_cells:
                                        for paragraph in cell.paragraphs:
                                            for run in paragraph.runs:
                                                run.bold = True
                            else:
                                # Not a table, just clean text
                                cleaned_text = clean_markdown_text(text)
                                if cleaned_text:
                                    doc.add_paragraph(cleaned_text)
                        else:
                            # Regular paragraph, clean markdown
                            cleaned_text = clean_markdown_text(text)
                            if cleaned_text:
                                doc.add_paragraph(cleaned_text)
                
                elif block_type == "bullet":
                    # Add bullet list (preserve markdown formatting)
                    items = block.get("items", [])
                    if items:
                        for item in items:
                            cleaned_item = clean_markdown_text(str(item))
                            if cleaned_item:
                                doc.add_paragraph(cleaned_item, style='List Bullet')
                
                elif block_type == "table":
                    # Add table
                    rows = block.get("rows", [])
                    if rows:
                        # Create table with appropriate dimensions
                        table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                        table.style = 'Light Grid Accent 1'
                        
                        # Populate table
                        for row_idx, row_data in enumerate(rows):
                            for col_idx, cell_data in enumerate(row_data):
                                if col_idx < len(table.rows[row_idx].cells):
                                    cell = table.rows[row_idx].cells[col_idx]
                                    # Clean markdown from cell text
                                    cell.text = clean_markdown_text(str(cell_data))
                        
                        # Make header row bold if it's the first row
                        if len(rows) > 0:
                            header_cells = table.rows[0].cells
                            for cell in header_cells:
                                for paragraph in cell.paragraphs:
                                    for run in paragraph.runs:
                                        run.bold = True
            
            # Add spacing between sections
            doc.add_paragraph("")
    
    # Fallback: If it's plain text, parse and clean it
    elif isinstance(brd_data, str):
        # Try to parse as markdown and convert
        lines = brd_data.split('\n')
        current_paragraph = []
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            if not line:
                if current_paragraph:
                    text = ' '.join(current_paragraph)
                    cleaned = clean_markdown_text(text)
                    if cleaned:
                        doc.add_paragraph(cleaned)
                    current_paragraph = []
                i += 1
                continue
            
            # Check for markdown table
            if '|' in line and line.count('|') >= 2 and not line.startswith('|---'):
                if current_paragraph:
                    text = ' '.join(current_paragraph)
                    cleaned = clean_markdown_text(text)
                    if cleaned:
                        doc.add_paragraph(cleaned)
                    current_paragraph = []
                
                # Collect table rows
                table_rows = []
                j = i
                while j < len(lines) and ('|' in lines[j] or lines[j].strip().startswith('---')):
                    if not lines[j].strip().startswith('---'):
                        table_rows.append(lines[j])
                    j += 1
                
                if table_rows:
                    table_data = parse_markdown_table('\n'.join(table_rows))
                    if table_data and len(table_data) > 0:
                        # Determine max columns
                        max_cols = max(len(row) for row in table_data)
                        table = doc.add_table(rows=len(table_data), cols=max_cols)
                        table.style = 'Light Grid Accent 1'
                        for row_idx, row_data in enumerate(table_data):
                            for col_idx, cell_data in enumerate(row_data):
                                if col_idx < len(table.rows[row_idx].cells):
                                    cell = table.rows[row_idx].cells[col_idx]
                                    # Clean markdown from cell text
                                    cell.text = clean_markdown_text(str(cell_data))
                        # Make header row bold
                        if len(table_data) > 0:
                            header_cells = table.rows[0].cells
                            for cell in header_cells:
                                for paragraph in cell.paragraphs:
                                    for run in paragraph.runs:
                                        run.bold = True
                
                i = j  # Skip processed table lines
            else:
                # Regular line - check for markdown headers
                if line.startswith('#'):
                    # It's a header - add previous paragraph if any
                    if current_paragraph:
                        text = ' '.join(current_paragraph)
                        cleaned = clean_markdown_text(text)
                        if cleaned:
                            doc.add_paragraph(cleaned)
                        current_paragraph = []
                    
                    # Determine header level
                    header_level = 0
                    while header_level < len(line) and line[header_level] == '#':
                        header_level += 1
                    
                    # Extract header text
                    header_text = line[header_level:].strip()
                    cleaned_header = clean_markdown_text(header_text)
                    if cleaned_header:
                        # Use appropriate heading level (max level 3 for Word)
                        level = min(header_level, 3)
                        doc.add_heading(cleaned_header, level=level)
                else:
                    # Regular line
                    if line and not line.startswith('---'):
                        current_paragraph.append(line)
                i += 1
        
        # Add remaining paragraph
        if current_paragraph:
            text = ' '.join(current_paragraph)
            cleaned = clean_markdown_text(text)
            if cleaned:
                doc.add_paragraph(cleaned)
    
    # Save to bytes
    docx_bytes = io.BytesIO()
    doc.save(docx_bytes)
    docx_bytes.seek(0)
    return docx_bytes.read()

# Initialize clients on startup
try:
    # Verify credentials on startup
    creds_valid, creds_info = check_aws_credentials()
    if creds_valid:
        print(f"[APP] ✅ AWS credentials valid. Account: {creds_info.get('Account', 'Unknown')}")
        print(f"[APP] User: {creds_info.get('Arn', 'Unknown')}")
    else:
        print(f"[APP] ⚠️  AWS credentials check failed: {creds_info}")
        print("[APP] Please configure AWS credentials using:")
        print("  - AWS CLI: aws configure")
        print("  - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
        print("  - Or use AWS SSO/credentials file")
    
    # Test client initialization
    test_client = get_agent_core_client()
    print(f"[APP] ✅ AgentCore client initialized successfully")
except Exception as e:
    print(f"[APP] ❌ Failed to initialize AWS clients: {e}")
    print("[APP] Please configure AWS credentials using:")
    print("  - AWS CLI: aws configure")
    print("  - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
    print("  - Or use AWS SSO/credentials file")

def read_docx(file_content):
    doc = Document(io.BytesIO(file_content))
    return "\n".join([p.text for p in doc.paragraphs])

def read_pdf(file_content):
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(file_content))
    return "\n".join([page.extract_text() or "" for page in reader.pages])

def extract_text(file_content, filename):
    """Extract text from .docx, .pdf, or .txt files."""
    lower = filename.lower()
    if lower.endswith(".docx"):
        return read_docx(file_content)
    elif lower.endswith(".pdf"):
        return read_pdf(file_content)
    else:
        return file_content.decode("utf-8", errors="replace")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/generate")
async def generate_brd(
    transcript: UploadFile = File(...),
    template: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    try:
        print("\n" + "="*80)
        print("[APP] Starting BRD generation")
        print("="*80)
        
        # 1. Read files
        transcript_content = await transcript.read()
        template_content = await template.read()
        
        print(f"[APP] Transcript file: {transcript.filename} ({len(transcript_content)} bytes)")
        print(f"[APP] Template file: {template.filename} ({len(template_content)} bytes)")
        
        # 2. Extract text
        if transcript.filename.endswith(".docx"):
            transcript_text = read_docx(transcript_content)
        else:
            transcript_text = transcript_content.decode("utf-8", errors="replace")
            
        template_text = read_docx(template_content)
        
        print(f"[APP] Transcript text: {len(transcript_text)} chars")
        print(f"[APP] Template text: {len(template_text)} chars")
        
        # 3. Prepare Payload (with BMAD persona/workflow overlay if available)
        base_prompt = "Generate a BRD based on the provided template and transcript."
        bmad_prompt = _build_bmad_prompt(base_prompt, workflow_key="create-prd")
        payload_dict = {
            "prompt": bmad_prompt,
            "template": template_text,
            "transcript": transcript_text
        }
        payload_bytes = json.dumps(payload_dict).encode('utf-8')
        
        print(f"[APP] Payload size: {len(payload_bytes)} bytes")
        
        # 4. Invoke Agent
        session_id = str(uuid.uuid4())
        print(f"[APP] Session ID: {session_id}")
        print(f"[APP] Agent ARN: {AGENT_ARN}")
        print(f"[APP] Calling agent...")
        print(f"[APP] Note: BRD generation may take 1-3 minutes. Please wait...")
        
        # Get fresh client to ensure we use latest credentials
        # Increased timeout to 5 minutes for BRD generation
        agent_core_client = get_agent_core_client()
        
        try:
            response = agent_core_client.invoke_agent_runtime(
                agentRuntimeArn=AGENT_ARN,
                runtimeSessionId=session_id,
                payload=payload_bytes,
                qualifier="DEFAULT"
            )
        except Exception as timeout_error:
            if "timeout" in str(timeout_error).lower() or "ReadTimeoutError" in str(type(timeout_error).__name__):
                print(f"[APP] ⚠️  Request timed out. The agent may still be processing.")
                print(f"[APP] This can happen if the BRD is very large or the agent is slow.")
                print(f"[APP] Try checking CloudWatch logs or reducing the transcript/template size.")
                return JSONResponse(status_code=504, content={
                    "error": "Request timeout - agent took too long to respond",
                    "message": "BRD generation is taking longer than expected. The agent may still be processing. Try:\n1. Checking CloudWatch logs\n2. Reducing transcript/template size\n3. Retrying the request",
                    "type": "TimeoutError"
                })
            raise
        
        print(f"[APP] Agent response received")
        
        # 5. Parse Response
        content = []
        for chunk in response.get("response", []):
            content.append(chunk.decode('utf-8'))
            
        full_response_str = ''.join(content)
        
        print(f"[APP] Response length: {len(full_response_str)} chars")
        print(f"[APP] Response preview: {full_response_str[:300]}")
        
        # The agent now returns clean JSON with the BRD
        try:
            # First parse the outer response
            result_json = json.loads(full_response_str)
            print(f"[APP] Parsed as JSON, keys: {list(result_json.keys())}")
            
            # The result field contains the agent's response
            if 'result' in result_json:
                result_str = result_json['result']
                print(f"[APP] Result preview: {result_str[:200]}")
                
                # Agent now returns JSON with {status, brd, brd_id}
                try:
                    agent_data = json.loads(result_str)
                    print(f"[APP] Agent data keys: {list(agent_data.keys())}")
                    
                    if agent_data.get('brd'):
                        print(f"[APP] Found BRD! Length: {len(agent_data['brd'])} chars")
                        brd_id = agent_data.get('brd_id')
                        
                        # Create AgentCore Memory session for this BRD
                        session_id = None
                        if brd_id:
                            try:
                                print(f"[APP] Creating AgentCore Memory session for BRD {brd_id}")
                                # Call Lambda to create session
                                lambda_client = get_lambda_client()
                                session_payload = {
                                    'action': 'create_session',
                                    'brd_id': brd_id,
                                    'template': template_text[:500],  # Truncate for session creation
                                    'transcript': transcript_text[:500]  # Truncate for session creation
                                }
                                session_response = lambda_client.invoke(
                                    FunctionName=os.getenv("LAMBDA_BRD_CHAT", "sdlc-dev-brd-chat"),
                                    InvocationType='RequestResponse',
                                    Payload=json.dumps(session_payload)
                                )
                                session_result = json.loads(session_response['Payload'].read())
                                if session_result.get('statusCode') == 200:
                                    session_body = json.loads(session_result.get('body', '{}'))
                                    session_id = session_body.get('session_id')
                                    print(f"[APP] ✅ Created session: {session_id}")
                                else:
                                    print(f"[APP] ⚠️  Session creation failed, will auto-create on first chat")
                            except Exception as e:
                                print(f"[APP] ⚠️  Failed to create session: {e}, will auto-create on first chat")
                        
                        return JSONResponse(content={
                            'result': agent_data['brd'],
                            'brd_id': brd_id,
                            'session_id': session_id,  # Return session_id to frontend
                            'status': 'success'
                        })
                except json.JSONDecodeError:
                    # If result is not JSON, return as-is
                    pass
            
            return JSONResponse(content=result_json)
            
        except json.JSONDecodeError as e:
            print(f"[APP] JSON decode error: {e}")
            return JSONResponse(content={"result": full_response_str})

    except Exception as e:
        error_msg = str(e)
        print(f"[APP] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        # Check if it's a credentials issue
        if "AccessDeniedException" in error_msg or "security token" in error_msg.lower() or "invalid" in error_msg.lower():
            creds_valid, creds_info = check_aws_credentials()
            if not creds_valid:
                error_msg = f"AWS credentials are invalid or expired. Please refresh your credentials.\n\nTo fix:\n1. Run: aws configure\n2. Or set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n3. Or refresh AWS SSO: aws sso login\n\nError details: {creds_info}"
            else:
                error_msg = f"AWS credentials are valid but access denied. Check IAM permissions for AgentCore.\n\nOriginal error: {error_msg}"
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "message": error_msg,
            "type": "AccessDeniedException" if "AccessDeniedException" in str(e) else "UnknownError"
        })

@app.post("/api/upload-transcript")
async def upload_transcript_to_s3(
    request: Request,
    transcripts: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload one or more transcript files to S3 and return S3 paths"""
    try:
        print("\n" + "="*80)
        print(f"[UPLOAD] Uploading {len(transcripts)} transcript(s) to S3")
        print(f"[UPLOAD] User: {current_user.get('email')} ({current_user.get('user_id')})")
        print("="*80)

        bucket_name = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
        uploaded_files = []

        for transcript in transcripts:
            transcript_id = str(uuid.uuid4())
            transcript_key = f"transcripts/{transcript_id}/{transcript.filename}"
            transcript_content = await transcript.read()

            print(f"[UPLOAD] Uploading to S3: s3://{bucket_name}/{transcript_key}")
            print(f"[UPLOAD] File: {transcript.filename}, Size: {len(transcript_content)} bytes")

            s3_put_object(
                key=transcript_key,
                body=transcript_content,
                content_type=transcript.content_type or "application/octet-stream",
                bucket=bucket_name,
            )
            uploaded_files.append({
                "transcript_id": transcript_id,
                "s3_path": transcript_key,
                "s3_url": f"s3://{bucket_name}/{transcript_key}",
                "filename": transcript.filename
            })

        print(f"[UPLOAD] ✅ Successfully uploaded {len(uploaded_files)} file(s) to S3")

        # Return both multi-file format and single-file backward compat
        result = {
            "success": True,
            "files": uploaded_files,
        }
        # Backward compat: also set top-level fields from first file
        if uploaded_files:
            result["transcript_id"] = uploaded_files[0]["transcript_id"]
            result["s3_path"] = uploaded_files[0]["s3_path"]
            result["s3_url"] = uploaded_files[0]["s3_url"]
            result["filename"] = uploaded_files[0]["filename"]

        return JSONResponse(content=result)

    except Exception as e:
        error_msg = str(e)
        print(f"[UPLOAD] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "message": f"Failed to upload transcript to S3: {error_msg}"
        })

@app.post("/api/generate-from-s3")
async def generate_brd_from_s3(
    transcript_s3_paths: Optional[str] = Form(None),
    transcript_s3_path: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    """Generate BRD from transcript(s) in S3 and template in S3"""
    try:
        print("\n" + "="*80)
        print("[APP] Starting BRD generation from S3")
        print("="*80)

        # Support both plural (new) and singular (backward compat) field names
        raw_paths = transcript_s3_paths or transcript_s3_path or ""
        s3_paths = [p.strip() for p in raw_paths.split(",") if p.strip()]
        if not s3_paths:
            return JSONResponse(status_code=400, content={"error": "No transcript S3 paths provided"})

        s3_client = get_s3_client()
        bucket_name = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")

        # Template path in S3
        template_s3_path_key = "templates/Deluxe_BRD_Template.docx"

        print(f"[APP] Transcript S3 paths ({len(s3_paths)}): {s3_paths}")
        print(f"[APP] Template S3 path: {template_s3_path_key}")

        # 1. Fetch and extract text from all transcript files
        transcript_texts = []
        for s3_path in s3_paths:
            print(f"[APP] Fetching transcript from S3: {s3_path}")
            resp = s3_client.get_object(Bucket=bucket_name, Key=s3_path)
            content = resp['Body'].read()
            print(f"[APP] File: {s3_path}, Size: {len(content)} bytes")
            text = extract_text(content, s3_path)
            transcript_texts.append(text)

        transcript_text = "\n\n---\n\n".join(transcript_texts)

        # 2. Fetch template from S3
        print(f"[APP] Fetching template from S3...")
        template_response = s3_client.get_object(Bucket=bucket_name, Key=template_s3_path_key)
        template_content = template_response['Body'].read()

        print(f"[APP] Combined transcript text: {len(transcript_text)} chars")
        print(f"[APP] Template file: {len(template_content)} bytes")

        # 3. Extract template text
        template_text = read_docx(template_content)

        print(f"[APP] Transcript text: {len(transcript_text)} chars")
        print(f"[APP] Template text: {len(template_text)} chars")
        
        # 4. Prepare Payload (same as /generate endpoint, with BMAD overlay if available)
        base_prompt = "Generate a BRD based on the provided template and transcript."
        bmad_prompt = _build_bmad_prompt(base_prompt, workflow_key="create-prd")
        payload_dict = {
            "prompt": bmad_prompt,
            "template": template_text,
            "transcript": transcript_text
        }
        payload_bytes = json.dumps(payload_dict).encode('utf-8')
        
        print(f"[APP] Payload size: {len(payload_bytes)} bytes")
        
        # 5. Invoke Agent (same as /generate endpoint)
        session_id = str(uuid.uuid4())
        print(f"[APP] Session ID: {session_id}")
        print(f"[APP] Agent ARN: {AGENT_ARN}")
        print(f"[APP] Calling agent...")
        print(f"[APP] Note: BRD generation may take 1-3 minutes. Please wait...")
        
        agent_core_client = get_agent_core_client()
        
        try:
                        response = agent_core_client.invoke_agent_runtime(
                agentRuntimeArn=AGENT_ARN,
                runtimeSessionId=session_id,
                payload=payload_bytes,
                qualifier="DEFAULT"
            )
        except Exception as timeout_error:
            if "timeout" in str(timeout_error).lower() or "ReadTimeoutError" in str(type(timeout_error).__name__):
                print(f"[APP] ⚠️  Request timed out. The agent may still be processing.")
                return JSONResponse(status_code=504, content={
                    "error": "Request timeout - agent took too long to respond",
                    "message": "BRD generation is taking longer than expected. The agent may still be processing.",
                    "type": "TimeoutError"
                })
            raise
        
        print(f"[APP] Agent response received")
        
        # 6. Parse Response (same as /generate endpoint)
        content = []
        for chunk in response.get("response", []):
            content.append(chunk.decode('utf-8'))
            
        full_response_str = ''.join(content)
        
        print(f"[APP] Response length: {len(full_response_str)} chars")
        print(f"[APP] Response preview: {full_response_str[:300]}")
        
        try:
            result_json = json.loads(full_response_str)
            print(f"[APP] Parsed as JSON, keys: {list(result_json.keys())}")
            
            if 'result' in result_json:
                result_str = result_json['result']
                print(f"[APP] Result preview: {result_str[:200]}")
                
                try:
                    agent_data = json.loads(result_str)
                    print(f"[APP] Agent data keys: {list(agent_data.keys())}")
                    
                    if agent_data.get('brd'):
                        print(f"[APP] Found BRD! Length: {len(agent_data['brd'])} chars")
                        brd_id = agent_data.get('brd_id')
                        
                        # Create AgentCore Memory session for this BRD
                        session_id_memory = None
                        if brd_id:
                            try:
                                print(f"[APP] Creating AgentCore Memory session for BRD {brd_id}")
                                lambda_client = get_lambda_client()
                                session_payload = {
                                    'action': 'create_session',
                                    'brd_id': brd_id,
                                    'template': template_text[:500],
                                    'transcript': transcript_text[:500]
                                }
                                session_response = lambda_client.invoke(
                                    FunctionName=os.getenv("LAMBDA_BRD_CHAT", "sdlc-dev-brd-chat"),
                                    InvocationType='RequestResponse',
                                    Payload=json.dumps(session_payload)
                                )
                                session_result = json.loads(session_response['Payload'].read())
                                if session_result.get('statusCode') == 200:
                                    session_body = json.loads(session_result.get('body', '{}'))
                                    session_id_memory = session_body.get('session_id')
                                    print(f"[APP] ✅ Created session: {session_id_memory}")
                                else:
                                    print(f"[APP] ⚠️  Session creation failed, will auto-create on first chat")
                            except Exception as e:
                                print(f"[APP] ⚠️  Failed to create session: {e}, will auto-create on first chat")
                        
                        # Persist BRD session to project for future restoration
                        if project_id and brd_id:
                            try:
                                save_project_brd_session(
                                    project_id=project_id,
                                    brd_id=brd_id,
                                    agentcore_session_id=session_id_memory
                                )
                                print(f"[APP] ✅ Persisted BRD session to project {project_id}")
                            except Exception as e:
                                print(f"[APP] ⚠️  Failed to persist BRD session: {e}")
                        
                        return JSONResponse(content={
                            'result': agent_data['brd'],
                            'brd_id': brd_id,
                            'session_id': session_id_memory,
                            'status': 'success'
                        })
                except json.JSONDecodeError:
                    pass
            
            return JSONResponse(content=result_json)
            
        except json.JSONDecodeError as e:
            print(f"[APP] JSON decode error: {e}")
            return JSONResponse(content={"result": full_response_str})

    except Exception as e:
        error_msg = str(e)
        print(f"[APP] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        if "AccessDeniedException" in error_msg or "security token" in error_msg.lower() or "invalid" in error_msg.lower():
            creds_valid, creds_info = check_aws_credentials()
            if not creds_valid:
                error_msg = f"AWS credentials are invalid or expired. Please refresh your credentials.\n\nTo fix:\n1. Run: aws configure\n2. Or set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n3. Or refresh AWS SSO: aws sso login\n\nError details: {creds_info}"
            else:
                error_msg = f"AWS credentials are valid but access denied. Check IAM permissions for AgentCore.\n\nOriginal error: {error_msg}"
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "message": error_msg,
            "type": "AccessDeniedException" if "AccessDeniedException" in str(e) else "UnknownError"
        })

@app.post("/api/chat")
async def chat_with_agent(
    message: str = Form(...),
    brd_id: str = Form(...),
    session_id: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    try:
        print(f"\n[CHAT] Message: {message}")
        print(f"[CHAT] BRD ID: {brd_id}")
        print(f"[CHAT] Session ID: {session_id} (length: {len(session_id)})")
        
        # Ensure brd_id is valid (not "none")
        if brd_id == "none" or not brd_id:
            return JSONResponse(status_code=400, content={
                "error": "BRD ID is required for chat. Please generate a BRD first.",
                "result": "Error: No BRD ID provided. Please upload a transcript and generate a BRD first."
            })
        
        # Ensure session_id is valid (not "none")
        if session_id == "none" or not session_id:
            # Generate a session ID based on BRD ID for consistency
            session_id = f"brd-session-{brd_id}"
            print(f"[CHAT] Session ID was 'none', generated: {session_id}")
        
        # Format the message to be clear for the agent
        # Include session_id in the payload so the agent can pass it to the Lambda
        # The agent entrypoint will extract brd_id and pass it to chat_with_brd tool
        formatted_message = message.strip()
        
        # Include session_id in payload so agent can use it when calling chat_with_brd
        payload_dict = {
            "prompt": formatted_message,
            "brd_id": brd_id,
            "session_id": session_id,  # Pass session_id so agent can use it
        }
        payload_bytes = json.dumps(payload_dict).encode('utf-8')
        
        print(f"[CHAT] Payload: {payload_dict}")
        print(f"[CHAT] Calling agent...")
        
        # Get fresh client to ensure we use latest credentials
        agent_core_client = get_agent_core_client()
        
        # Use the consistent session_id as runtimeSessionId so that conversation
        # events are stored in AgentCore Memory under this session and can be
        # retrieved later by /api/brd-history.
        print(f"[CHAT] Using runtimeSessionId: {session_id}")

        response = agent_core_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            runtimeSessionId=session_id,
            payload=payload_bytes,
            qualifier="DEFAULT"
        )
        
        content = []
        for chunk in response.get("response", []):
            content.append(chunk.decode('utf-8'))
            
        full_response_str = ''.join(content)
        print(f"[CHAT] Raw response: {full_response_str[:500]}")
        print(f"[CHAT] Raw response type: {type(full_response_str)}")
        print(f"[CHAT] Raw response length: {len(full_response_str)}")
        
        # Parse the agent response to extract the actual text content
        final_text = None
        try:
            result_json = json.loads(full_response_str)
            print(f"[CHAT] Parsed JSON, keys: {list(result_json.keys()) if isinstance(result_json, dict) else 'Not a dict'}")
            print(f"[CHAT] Parsed JSON type: {type(result_json)}")

            # AgentCore returns responses in format: {'role': 'assistant', 'content': [{'text': '...'}]}
            # Extract the text from the content array
            extracted_text = None

            if isinstance(result_json, dict):
                # Check for 'content' field with text
                if 'content' in result_json:
                    content_list = result_json['content']
                    if isinstance(content_list, list) and len(content_list) > 0:
                        first_content = content_list[0]
                        if isinstance(first_content, dict) and 'text' in first_content:
                            extracted_text = first_content['text']

                # Also check for 'result' field (some responses use this)
                if not extracted_text and 'result' in result_json:
                    result_value = result_json['result']
                    if isinstance(result_value, str):
                        extracted_text = result_value
                    elif isinstance(result_value, dict):
                        # Try to extract from nested result
                        if 'content' in result_value:
                            content_list = result_value['content']
                            if isinstance(content_list, list) and len(content_list) > 0:
                                first_content = content_list[0]
                                if isinstance(first_content, dict) and 'text' in first_content:
                                    extracted_text = first_content['text']

                # Check for direct 'text' or 'message' fields
                if not extracted_text:
                    extracted_text = result_json.get('text') or result_json.get('message') or result_json.get('response')

            # Determine final_text from extraction results
            if extracted_text:
                print(f"[CHAT] ✅ Extracted text successfully: {extracted_text[:200]}")
                print(f"[CHAT] Extracted text type: {type(extracted_text)}")
                print(f"[CHAT] Extracted text length: {len(extracted_text)}")
                if not isinstance(extracted_text, str):
                    extracted_text = str(extracted_text)
                final_text = extracted_text
            else:
                print(f"[CHAT] Could not extract text, trying to format response")
                if isinstance(result_json, dict):
                    for key in ['text', 'message', 'content', 'result', 'response', 'answer']:
                        if key in result_json:
                            value = result_json[key]
                            if isinstance(value, str) and value.strip():
                                final_text = value
                                break
                            elif isinstance(value, list) and len(value) > 0:
                                if isinstance(value[0], dict) and 'text' in value[0]:
                                    final_text = value[0]['text']
                                    break

                if not final_text:
                    final_text = json.dumps(result_json, indent=2) if isinstance(result_json, dict) else full_response_str

        except json.JSONDecodeError:
            print(f"[CHAT] Response is not JSON, returning as text")
            final_text = full_response_str

        return JSONResponse(content={
            "result": final_text,
            "response": final_text,
            "session_id": session_id
        })

    except Exception as e:
        error_msg = str(e)
        print(f"[CHAT] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        # Check if it's a credentials issue
        if "AccessDeniedException" in error_msg or "security token" in error_msg.lower() or "invalid" in error_msg.lower():
            creds_valid, creds_info = check_aws_credentials()
            if not creds_valid:
                error_msg = f"AWS credentials are invalid or expired. Please refresh your credentials.\n\nTo fix:\n1. Run: aws configure\n2. Or set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n3. Or refresh AWS SSO: aws sso login\n\nError details: {creds_info}"
            else:
                error_msg = f"AWS credentials are valid but access denied. Check IAM permissions for AgentCore.\n\nOriginal error: {error_msg}"
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "result": f"Error: {error_msg}",
            "type": "AccessDeniedException" if "AccessDeniedException" in str(e) else "UnknownError"
        })

def extract_text_from_analyst_response(response_str: str) -> tuple[str, str]:
    """
    Extract plain text message and session_id from analyst agent's JSON response.
    Handles both direct analyst agent responses and AgentCore-wrapped responses.
    Returns: (message_text, session_id)
    """
    print(f"[extract_text] Called with response length: {len(response_str) if response_str else 0}")
    
    if not response_str or not isinstance(response_str, str):
        print(f"[extract_text] Response is None or not string")
        return None, None
    
    response_trimmed = response_str.strip()
    if not response_trimmed.startswith('{'):
        print(f"[extract_text] Response doesn't start with '{{', returning as plain text")
        return response_str, None
    
    print(f"[extract_text] Response starts with '{{'', attempting JSON parse")
    print(f"[extract_text] First 200 chars: {response_trimmed[:200]}")
    
    try:
        parsed = json.loads(response_trimmed)
        print(f"[extract_text] JSON parse successful, type: {type(parsed)}")
        
        if isinstance(parsed, dict):
            print(f"[extract_text] Parsed dict keys: {list(parsed.keys())}")
            
            # Case 1: Direct analyst agent response: {"result": "...", "session_id": "...", "message": "..."}
            has_message = 'message' in parsed
            has_result_and_session = ('result' in parsed and 'session_id' in parsed)
            print(f"[extract_text] has_message: {has_message}, has_result_and_session: {has_result_and_session}")
            
            if has_message or has_result_and_session:
                message_text = parsed.get('message') or parsed.get('result')
                session_id = parsed.get('session_id')
                print(f"[extract_text] Extracted message_text type: {type(message_text)}, length: {len(message_text) if isinstance(message_text, str) else 'N/A'}")
                print(f"[extract_text] Extracted session_id: {session_id}")
                
                if message_text and isinstance(message_text, str):
                    print(f"[extract_text] ✅ Returning message_text (string)")
                    return message_text, session_id
                elif message_text:
                    # If message_text is not a string, convert it
                    print(f"[extract_text] ✅ Returning message_text (converted to string)")
                    return str(message_text), session_id
                else:
                    print(f"[extract_text] ⚠️ message_text is None or empty")
            
            # Case 2: AgentCore wrapped response: {"result": "{\"result\": \"...\", \"session_id\": \"...\", \"message\": \"...\"}"}
            if 'result' in parsed:
                result_value = parsed.get('result')
                if isinstance(result_value, str) and result_value.strip().startswith('{'):
                    # Try to parse the nested JSON string
                    try:
                        nested_parsed = json.loads(result_value)
                        if isinstance(nested_parsed, dict):
                            nested_message = nested_parsed.get('message') or nested_parsed.get('result')
                            nested_session_id = nested_parsed.get('session_id')
                            if nested_message and isinstance(nested_message, str):
                                return nested_message, nested_session_id
                    except json.JSONDecodeError:
                        pass
            
            # Case 3: Check for content array format: {"content": [{"text": "..."}]}
            if 'content' in parsed and isinstance(parsed['content'], list):
                content_list = parsed['content']
                if len(content_list) > 0:
                    first_content = content_list[0]
                    if isinstance(first_content, dict) and 'text' in first_content:
                        return first_content['text'], parsed.get('session_id')
    except json.JSONDecodeError:
        pass
    except Exception as e:
        print(f"[extract_text_from_analyst_response] Error: {e}")
    
    return None, None

@app.post("/api/analyst-chat")
async def analyst_chat(
    message: str = Form(...),
    session_id: str = Form(...),
    project_id: str = Form(None),
    current_user: dict = Depends(get_current_user)
):
    """Chat with the Business Analyst agent for requirements gathering"""
    try:
        print(f"\n[ANALYST-CHAT] Message: {message}")
        print(f"[ANALYST-CHAT] Session ID: {session_id}")
        print(f"[ANALYST-CHAT] Project ID: {project_id}")
        print(f"[ANALYST-CHAT] User ID: {current_user.get('user_id', 'unknown')}")
        
        # PROJECT-SCOPED SESSION MANAGEMENT
        # Generate a deterministic session ID based on project_id + user_id
        # This ensures each project gets its own session, and switching projects creates a new session
        user_id = current_user.get('user_id', 'unknown')
        
        # Normalize project_id (handle None, empty string, whitespace)
        if project_id:
            project_id = project_id.strip() if isinstance(project_id, str) else str(project_id)
            if not project_id or project_id == "none":
                project_id = None
        
        # Validate session_id
        if not session_id or session_id == "none":
            # This shouldn't happen with correct frontend, but fallback safety
            import uuid
            runtime_session_id = str(uuid.uuid4())
            print(f"[ANALYST-CHAT] ⚠️ No session_id provided, created new: {runtime_session_id}")
        else:
            runtime_session_id = session_id
            print(f"[ANALYST-CHAT] ✅ Using provided session ID: {runtime_session_id}")
        
        formatted_message = message.strip()
        
        # Build payload for analyst agent
        payload_dict = {
            "prompt": formatted_message,
            "session_id": runtime_session_id,  # Always use the computed session_id
        }
        if project_id and project_id != "none":
            payload_dict["project_id"] = project_id
        
        payload_bytes = json.dumps(payload_dict).encode('utf-8')
        
        print(f"[ANALYST-CHAT] Payload: {payload_dict}")
        print(f"[ANALYST-CHAT] Calling analyst agent...")
        print(f"[ANALYST-CHAT] Analyst Agent ARN: {ANALYST_AGENT_ARN}")
        
        # Get fresh client
        agent_core_client = get_agent_core_client()
        
        try:
            response = agent_core_client.invoke_agent_runtime(
                agentRuntimeArn=ANALYST_AGENT_ARN,
                runtimeSessionId=runtime_session_id,
                payload=payload_bytes,
                qualifier="DEFAULT"
            )
            print(f"[ANALYST-CHAT] ✅ Successfully called analyst agent")
        except Exception as invoke_error:
            print(f"[ANALYST-CHAT] ❌ Error invoking analyst agent: {invoke_error}")
            import traceback
            traceback.print_exc()
            # Check if it's a health check error
            if "health check" in str(invoke_error).lower() or "RuntimeClientError" in str(type(invoke_error).__name__):
                return JSONResponse(status_code=503, content={
                    "error": "Analyst agent is not available",
                    "result": "The analyst agent runtime is not responding. Please check if the agent is deployed and healthy.",
                    "type": "AgentUnavailable"
                })
            raise
        
        content = []
        for chunk in response.get("response", []):
            content.append(chunk.decode('utf-8'))
            
        full_response_str = ''.join(content)
        print(f"[ANALYST-CHAT] ========== DEBUG START ==========")
        print(f"[ANALYST-CHAT] Raw response type: {type(full_response_str)}")
        print(f"[ANALYST-CHAT] Raw response length: {len(full_response_str)}")
        print(f"[ANALYST-CHAT] Raw response first 500 chars: {full_response_str[:500]}")
        print(f"[ANALYST-CHAT] Raw response last 100 chars: {full_response_str[-100:]}")
        
        # Parse the agent response
        try:
            result_json = json.loads(full_response_str)
            print(f"[ANALYST-CHAT] ✅ Successfully parsed JSON (first parse)")
            print(f"[ANALYST-CHAT] result_json type: {type(result_json)}")
            
            # CRITICAL FIX: Handle double-encoded JSON
            # If json.loads() returns a string, it means we have double-encoded JSON
            if isinstance(result_json, str):
                print(f"[ANALYST-CHAT] ⚠️ First parse returned a string - double-encoded JSON detected!")
                print(f"[ANALYST-CHAT] Attempting second parse...")
                try:
                    result_json = json.loads(result_json)
                    print(f"[ANALYST-CHAT] ✅ Successfully parsed JSON (second parse)")
                    print(f"[ANALYST-CHAT] result_json type after second parse: {type(result_json)}")
                except json.JSONDecodeError as e:
                    print(f"[ANALYST-CHAT] ❌ Second parse failed: {e}")
                    # If second parse fails, treat the string as the final response
                    pass
            
            print(f"[ANALYST-CHAT] result_json keys: {list(result_json.keys()) if isinstance(result_json, dict) else 'Not a dict'}")
            
            extracted_text = None
            extracted_brd_id = None
            response_session_id = None
            
            if isinstance(result_json, dict):
                print(f"[ANALYST-CHAT] result_json IS a dict")
                print(f"[ANALYST-CHAT] Checking for 'message' field...")
                if 'message' in result_json:
                    print(f"[ANALYST-CHAT] 'message' field exists, type: {type(result_json['message'])}")
                    print(f"[ANALYST-CHAT] 'message' first 100 chars: {str(result_json['message'])[:100]}")
                print(f"[ANALYST-CHAT] Checking for 'result' field...")
                if 'result' in result_json:
                    print(f"[ANALYST-CHAT] 'result' field exists, type: {type(result_json['result'])}")
                    print(f"[ANALYST-CHAT] 'result' first 100 chars: {str(result_json['result'])[:100]}")
                print(f"[ANALYST-CHAT] Checking for 'session_id' field...")
                if 'session_id' in result_json:
                    print(f"[ANALYST-CHAT] 'session_id' field exists: {result_json['session_id']}")
                # Extract session_id from top level
                response_session_id = result_json.get('session_id')
                
                # SIMPLIFIED APPROACH (matching BRD agent logic):
                # Just extract text from message, result, or text fields directly
                extracted_text = result_json.get('message') or result_json.get('result') or result_json.get('text')
                
                if extracted_text and isinstance(extracted_text, str):
                    print(f"[ANALYST-CHAT] ✅ Extracted text from top-level field: {len(extracted_text)} chars")
                    print(f"[ANALYST-CHAT] First 100 chars: {extracted_text[:100]}")
                    
                    # Early return - we found the text, no need for complex parsing
                    # Determine final session_id
                    if not response_session_id or response_session_id == "none":
                        if runtime_session_id:
                            response_session_id = runtime_session_id
                        else:
                            response_session_id = session_id if session_id and session_id != "none" else str(uuid.uuid4())
                    
                    return JSONResponse(content={
                        "result": extracted_text,
                        "response": extracted_text,
                        "session_id": response_session_id
                    })
                
                
                # FIRST: Try to extract session_id from the response (analyst agent returns it as JSON string)
                # The analyst agent returns: {"result": "...", "session_id": "...", "message": "..."}
                # But AgentCore might wrap it, so check multiple levels
                
                # CRITICAL: Check if result is a JSON string FIRST - this is the most common case
                # The analyst agent returns: {"result": "{\"result\": \"...\", \"session_id\": \"...\", \"message\": \"...\"}"}
                print(f"[ANALYST-CHAT] Checking result_json keys: {list(result_json.keys())}")
                if 'result' in result_json:
                    result_value = result_json.get('result')
                    print(f"[ANALYST-CHAT] result_value type: {type(result_value)}")
                    print(f"[ANALYST-CHAT] result_value length: {len(result_value) if isinstance(result_value, str) else 'N/A'}")
                    print(f"[ANALYST-CHAT] result_value first 200 chars: {result_value[:200] if isinstance(result_value, str) else result_value}")
                    
                    if isinstance(result_value, str):
                        # Try to parse it as JSON - this is the agent's JSON response
                        try:
                            parsed_result = json.loads(result_value)
                            print(f"[ANALYST-CHAT] ✅ Successfully parsed result_value as JSON")
                            print(f"[ANALYST-CHAT] parsed_result keys: {list(parsed_result.keys()) if isinstance(parsed_result, dict) else 'Not a dict'}")
                            
                            if isinstance(parsed_result, dict):
                                # Extract session_id from nested JSON
                                if not response_session_id:
                                    response_session_id = parsed_result.get('session_id')
                                    if response_session_id:
                                        print(f"[ANALYST-CHAT] ✅ Found session_id in nested JSON string: {response_session_id}")
                                
                                # CRITICAL: Extract the actual message text from the nested JSON
                                # Priority: message > result > text
                                nested_message = (parsed_result.get('message') or 
                                                parsed_result.get('result') or 
                                                parsed_result.get('text'))
                                print(f"[ANALYST-CHAT] nested_message: {nested_message[:100] if nested_message else 'None'}")
                                
                                if nested_message and isinstance(nested_message, str):
                                    # This is the actual text we want to return
                                    extracted_text = nested_message
                                    print(f"[ANALYST-CHAT] ✅ Extracted message from nested JSON result field: {len(extracted_text)} chars")
                                    print(f"[ANALYST-CHAT] First 100 chars: {extracted_text[:100]}")
                                else:
                                    print(f"[ANALYST-CHAT] ⚠️ nested_message is None or not a string. Type: {type(nested_message)}")
                        except json.JSONDecodeError as e:
                            # Not JSON, might be plain text - use as-is
                            print(f"[ANALYST-CHAT] result field is not JSON, using as text: {e}")
                            if not extracted_text:
                                extracted_text = result_value
                        except Exception as e:
                            print(f"[ANALYST-CHAT] Error parsing result field: {e}")
                            import traceback
                            traceback.print_exc()
                            if not extracted_text:
                                extracted_text = result_value
                    else:
                        print(f"[ANALYST-CHAT] ⚠️ result_value is not a string, it's: {type(result_value)}")
                else:
                    print(f"[ANALYST-CHAT] ⚠️ 'result' key not found in result_json")
                
                # Handle nested structure: {"result": {"role": "assistant", "content": [{"text": "..."}]}}
                if 'result' in result_json and isinstance(result_json['result'], dict):
                    result_obj = result_json['result']
                    # Check if result has 'content' array
                    if 'content' in result_obj and isinstance(result_obj['content'], list):
                        content_list = result_obj['content']
                        if len(content_list) > 0:
                            first_content = content_list[0]
                            if isinstance(first_content, dict) and 'text' in first_content:
                                extracted_text = first_content['text']
                    # Also check if result has direct 'text' field
                    if not extracted_text and 'text' in result_obj:
                        extracted_text = result_obj['text']
                
                # Handle direct 'content' array at root level
                if not extracted_text and 'content' in result_json:
                    content_list = result_json['content']
                    if isinstance(content_list, list) and len(content_list) > 0:
                        first_content = content_list[0]
                        if isinstance(first_content, dict) and 'text' in first_content:
                            extracted_text = first_content['text']
                
                # Check for BRD ID in response
                if extracted_text:
                    import re
                    brd_id_match = re.search(r'BRD ID:\s*([a-f0-9-]+)', extracted_text, re.IGNORECASE)
                    if brd_id_match:
                        extracted_brd_id = brd_id_match.group(1)
                        print(f"[ANALYST-CHAT] Found BRD ID in response: {extracted_brd_id}")
                
                # Fallback: check for direct 'result', 'text', or 'message' fields (as strings)
                if not extracted_text:
                    result_value = result_json.get('result')
                    if isinstance(result_value, str):
                        # Check if it's a JSON string
                        try:
                            parsed = json.loads(result_value)
                            if isinstance(parsed, dict):
                                # Extract text from nested JSON structure
                                extracted_text = parsed.get('message') or parsed.get('result') or parsed.get('text') or result_value
                            else:
                                extracted_text = result_value
                        except:
                            # Not JSON, use as-is
                            extracted_text = result_value
                    elif isinstance(result_value, dict) and 'text' in result_value:
                        extracted_text = result_value['text']
                    else:
                        extracted_text = result_json.get('text') or result_json.get('message') or result_json.get('result')
            
            # Use extracted text or fallback to full response
            final_response = extracted_text or full_response_str
            print(f"[ANALYST-CHAT] After initial extraction - extracted_text: {extracted_text[:100] if extracted_text else 'None'}")
            print(f"[ANALYST-CHAT] After initial extraction - final_response type: {type(final_response)}, length: {len(final_response) if final_response else 0}")
            
            # CRITICAL: If final_response is still a JSON string, parse it and extract the text
            # This handles cases where the agent returns JSON strings that weren't parsed earlier
            if final_response and isinstance(final_response, str):
                final_response_trimmed = final_response.strip()
                # Check if it looks like a JSON string (starts with { and contains result/message fields)
                if (final_response_trimmed.startswith('{') and 
                    ('"result"' in final_response_trimmed or "'result'" in final_response_trimmed or
                     '"message"' in final_response_trimmed or "'message'" in final_response_trimmed)):
                    print(f"[ANALYST-CHAT] ⚠️ final_response is still a JSON string, attempting to parse...")
                    try:
                        parsed_final = json.loads(final_response_trimmed)
                        if isinstance(parsed_final, dict):
                            # Extract the actual message text from the JSON
                            # Priority: message > result > text
                            extracted_from_json = (parsed_final.get('message') or 
                                                  parsed_final.get('result') or 
                                                  parsed_final.get('text'))
                            if extracted_from_json and isinstance(extracted_from_json, str):
                                final_response = extracted_from_json
                                print(f"[ANALYST-CHAT] ✅ Extracted text from JSON string in final_response: {len(final_response)} chars")
                                print(f"[ANALYST-CHAT] First 200 chars of extracted text: {final_response[:200]}")
                            else:
                                print(f"[ANALYST-CHAT] ⚠️ Could not extract text from parsed JSON. Keys: {list(parsed_final.keys())}")
                            # Also update session_id if found
                            if not response_session_id:
                                response_session_id = parsed_final.get('session_id')
                    except json.JSONDecodeError as e:
                        # Not valid JSON, keep as-is
                        print(f"[ANALYST-CHAT] Could not parse final_response as JSON: {e}")
                        pass
                    except Exception as e:
                        print(f"[ANALYST-CHAT] Error parsing final_response: {e}")
                        import traceback
                        traceback.print_exc()
                        pass
            
            # Final validation: ensure final_response is not a JSON string
            if final_response and isinstance(final_response, str):
                if final_response.strip().startswith('{') and ('"result"' in final_response or '"message"' in final_response):
                    print(f"[ANALYST-CHAT] ⚠️ WARNING: final_response is still a JSON string after all parsing attempts!")
                    print(f"[ANALYST-CHAT] First 300 chars: {final_response[:300]}")
            
            print(f"[ANALYST-CHAT] Final response length: {len(final_response) if final_response else 0} chars")
            print(f"[ANALYST-CHAT] Final response preview: {final_response[:200] if final_response else 'None'}...")
            
            # Try to extract session_id from the extracted text if it's a JSON string
            # The analyst agent returns: {"result": "...", "session_id": "...", "message": "..."}
            # But AgentCore wraps it in: {"result": {"role": "assistant", "content": [{"text": "<JSON string>"}]}}
            if not response_session_id and extracted_text:
                print(f"[ANALYST-CHAT] Attempting to parse extracted_text as JSON (first 200 chars): {extracted_text[:200]}")
                try:
                    # Check if extracted_text is a JSON string
                    extracted_text_trimmed = extracted_text.strip()
                    if extracted_text_trimmed.startswith('{'):
                        parsed_text = json.loads(extracted_text_trimmed)
                        if isinstance(parsed_text, dict):
                            response_session_id = parsed_text.get('session_id')
                            if response_session_id:
                                print(f"[ANALYST-CHAT] ✅ Found session_id in extracted text JSON: {response_session_id}")
                                # Update final_response to use the message/result from parsed JSON
                                if 'message' in parsed_text:
                                    final_response = parsed_text.get('message')
                                elif 'result' in parsed_text:
                                    result_val = parsed_text.get('result')
                                    if isinstance(result_val, str):
                                        final_response = result_val
                                    else:
                                        final_response = extracted_text  # Keep original if result is not a string
                            else:
                                print(f"[ANALYST-CHAT] Parsed JSON but no session_id found. Keys: {list(parsed_text.keys())}")
                    else:
                        print(f"[ANALYST-CHAT] Extracted text doesn't start with '{{', not JSON")
                except json.JSONDecodeError as json_err:
                    # Not JSON, that's fine
                    print(f"[ANALYST-CHAT] Extracted text is not valid JSON: {json_err}")
                except Exception as e:
                    print(f"[ANALYST-CHAT] Error parsing extracted text as JSON: {e}")
                    import traceback
                    traceback.print_exc()
            
            # CRITICAL FIX: ALWAYS use runtime_session_id (deterministic from project_id + user_id)
            # Ignore any session_id from the agent's response - we control session management
            # This ensures consistent sessions across messages for the same project
            if runtime_session_id:
                response_session_id = runtime_session_id
                print(f"[ANALYST-CHAT] ✅ Using deterministic runtime_session_id: {response_session_id}")
            else:
                # Fallback: use session_id from agent response if runtime_session_id is not set
                # (This should rarely happen, but handle it gracefully)
                if response_session_id and response_session_id != "none":
                    print(f"[ANALYST-CHAT] ⚠️ runtime_session_id not set, using agent's session_id: {response_session_id}")
                else:
                    # Last resort: use request session_id or create new one
                    response_session_id = session_id if session_id and session_id != "none" else str(uuid.uuid4())
                    print(f"[ANALYST-CHAT] ⚠️ No valid session_id found, using: {response_session_id}")
            
            # If session_id found, log it
            if response_session_id:
                print(f"[ANALYST-CHAT] ✅ Final session_id to return: {response_session_id}")
            else:
                # If not found in response, use the one from request (if valid)
                if session_id and session_id != "none":
                    response_session_id = session_id
                    print(f"[ANALYST-CHAT] Using session_id from request: {response_session_id}")
                else:
                    # ALWAYS use runtime_session_id as fallback (it's always set)
                    if runtime_session_id:
                        response_session_id = runtime_session_id
                        print(f"[ANALYST-CHAT] ✅ Using runtime_session_id as session_id: {response_session_id}")
                    else:
                        # This should never happen, but just in case
                        print(f"[ANALYST-CHAT] ⚠️ ERROR: runtime_session_id is None! This should not happen.")
                        print(f"[ANALYST-CHAT] session_id from request: {session_id}")
                        print(f"[ANALYST-CHAT] Full response keys: {list(result_json.keys()) if isinstance(result_json, dict) else 'Not a dict'}")
                        response_session_id = "none"
            
            # Final safety check: ALWAYS use runtime_session_id if response_session_id is still "none"
            if not response_session_id or response_session_id == "none":
                if runtime_session_id:
                    response_session_id = runtime_session_id
                    print(f"[ANALYST-CHAT] ✅ Final fallback: Using runtime_session_id: {response_session_id}")
                else:
                    print(f"[ANALYST-CHAT] ⚠️ CRITICAL ERROR: Both response_session_id and runtime_session_id are invalid!")
                    print(f"[ANALYST-CHAT] This should never happen. Creating emergency session_id...")
                    response_session_id = str(uuid.uuid4())
                    print(f"[ANALYST-CHAT] Created emergency session_id: {response_session_id}")
            
            return JSONResponse(content={
                "result": final_response,
                "response": final_response,
                "session_id": response_session_id,
                "brd_id": extracted_brd_id
            })
            
        except json.JSONDecodeError:
            # If it's not JSON, return the raw string
            print(f"[ANALYST-CHAT] Response is not JSON, returning as text")
            # ALWAYS use runtime_session_id if available (it should always be set)
            if runtime_session_id:
                fallback_session_id = runtime_session_id
                print(f"[ANALYST-CHAT] ✅ Using runtime_session_id for non-JSON response: {fallback_session_id}")
            else:
                fallback_session_id = session_id if session_id and session_id != "none" else str(uuid.uuid4())
                print(f"[ANALYST-CHAT] ⚠️ runtime_session_id not available, using: {fallback_session_id}")
            return JSONResponse(content={
                "result": full_response_str,
                "response": full_response_str,
                "session_id": fallback_session_id
            })

    except Exception as e:
        error_msg = str(e)
        print(f"[ANALYST-CHAT] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        # Check if it's a credentials issue
        if "AccessDeniedException" in error_msg or "security token" in error_msg.lower():
            creds_valid, creds_info = check_aws_credentials()
            if not creds_valid:
                error_msg = f"AWS credentials are invalid or expired. Please refresh your credentials.\n\nError details: {creds_info}"
            else:
                error_msg = f"AWS credentials are valid but access denied. Check IAM permissions for AgentCore.\n\nOriginal error: {error_msg}"
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "result": f"Error: {error_msg}",
            "type": "AccessDeniedException" if "AccessDeniedException" in str(e) else "UnknownError"
        })

@app.get("/api/analyst-history/{session_id}")
async def get_analyst_history(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get conversation history for an analyst session from AgentCore Memory"""
    try:
        print(f"\n[ANALYST-HISTORY] Fetching history for session: {session_id}")
        print(f"[ANALYST-HISTORY] User: {current_user.get('email', 'unknown')}")

        # Get AgentCore Memory configuration
        AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'sdlc_dev_agentcore_memory-VF74Yf64ZB')
        AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'analyst-session')

        # Get AgentCore client
        agentcore_client = get_agent_core_client()

        print(f"[ANALYST-HISTORY] Calling AgentCore Memory...")
        print(f"[ANALYST-HISTORY] Memory ID: {AGENTCORE_MEMORY_ID}")
        print(f"[ANALYST-HISTORY] Session ID: {session_id}")
        print(f"[ANALYST-HISTORY] Actor ID: {AGENTCORE_ACTOR_ID}")

        # Fetch events from AgentCore Memory
        response = agentcore_client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=99
        )

        events = response.get("events", [])
        print(f"[ANALYST-HISTORY] Retrieved {len(events)} events from AgentCore Memory")

        # Parse events into messages
        messages = []
        for event in events:
            payload_list = event.get("payload", [])
            for payload_item in payload_list:
                conv_data = payload_item.get("conversational")
                if not conv_data:
                    continue

                text_content = conv_data.get("content", {}).get("text")
                if not text_content:
                    continue

                role = conv_data.get("role", "assistant").lower()
                messages.append({
                    'content': text_content,
                    'isBot': role == 'assistant',
                    'timestamp': ''  # Frontend will generate timestamp
                })

        # Reverse messages to show in chronological order (oldest first)
        messages.reverse()

        print(f"[ANALYST-HISTORY] ✅ Parsed {len(messages)} messages")

        return JSONResponse(content={
            'messages': messages,
            'session_id': session_id,
            'count': len(messages)
        })

    except Exception as e:
        error_msg = str(e)
        print(f"[ANALYST-HISTORY] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()

        return JSONResponse(status_code=500, content={
            'error': error_msg,
            'messages': []
        })

@app.post("/api/analyst-generate-brd")
async def analyst_generate_brd(
    session_id: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """Generate BRD from analyst conversation history stored in AgentCore Memory"""
    try:
        print(f"\n[ANALYST-GENERATE-BRD] Session ID received: {session_id}")
        
        # Get AgentCore Memory client
        agentcore_client = get_agent_core_client()
        memory_id = os.getenv("AGENTCORE_MEMORY_ID", "sdlc_dev_agentcore_memory-VF74Yf64ZB")
        actor_id = os.getenv("AGENTCORE_ACTOR_ID", "analyst-session")
        
        # Get conversation history from AgentCore Memory
        print(f"[ANALYST-GENERATE-BRD] Retrieving conversation history from AgentCore Memory...")
        print(f"[ANALYST-GENERATE-BRD] Session ID: {session_id}")
        print(f"[ANALYST-GENERATE-BRD] Memory ID: {memory_id}")
        print(f"[ANALYST-GENERATE-BRD] Actor ID: {actor_id}")
        
        try:
            # First, try to list sessions to see what exists
            try:
                list_sessions_response = agentcore_client.list_sessions(
                    memoryId=memory_id,
                    actorId=actor_id,
                    maxResults=10
                )
                sessions = list_sessions_response.get('sessions', [])
                print(f"[ANALYST-GENERATE-BRD] Found {len(sessions)} sessions:")
                for sess in sessions:
                    print(f"[ANALYST-GENERATE-BRD]   - Session ID: {sess.get('sessionId')}, Created: {sess.get('creationTime')}")
            except Exception as list_err:
                error_str = str(list_err)
                print(f"[ANALYST-GENERATE-BRD] Could not list sessions: {list_err}")
                # If actor doesn't exist, try to create a session to create the actor
                if "not found" in error_str.lower() or "ResourceNotFoundException" in error_str:
                    print(f"[ANALYST-GENERATE-BRD] Actor {actor_id} not found, attempting to create session to initialize actor...")
                    try:
                        # Create a temporary session to initialize the actor
                        temp_session_id = f"temp-{session_id}"
                        agentcore_client.create_session(
                            memoryId=memory_id,
                            sessionId=temp_session_id,
                            actorId=actor_id
                        )
                        print(f"[ANALYST-GENERATE-BRD] ✅ Created temporary session to initialize actor")
                        # Now try to use the actual session
                        try:
                            agentcore_client.create_session(
                                memoryId=memory_id,
                                sessionId=session_id,
                                actorId=actor_id
                            )
                            print(f"[ANALYST-GENERATE-BRD] ✅ Created session {session_id} with actor {actor_id}")
                        except Exception as create_err:
                            if "already exists" not in str(create_err).lower() and "ConflictException" not in str(type(create_err).__name__):
                                print(f"[ANALYST-GENERATE-BRD] ⚠️ Could not create session: {create_err}")
                    except Exception as init_err:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ Could not initialize actor: {init_err}")
            
            # List events from AgentCore Memory for this session
            events_response = agentcore_client.list_events(
                memoryId=memory_id,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=100
            )
            events = events_response.get("events", [])
            print(f"[ANALYST-GENERATE-BRD] Retrieved {len(events)} events from AgentCore Memory")
            
            if not events:
                return JSONResponse(status_code=400, content={
                    "error": "No conversation history found",
                    "message": f"No conversation history found for session {session_id}. Please send at least one message in the analyst agent chat first."
                })
            
            # Format conversation history
            conversation_messages = []
            for event in events:
                payload = event.get("payload", [])
                for item in payload:
                    if "conversational" in item:
                        conv = item["conversational"]
                        role = conv.get("role", "USER")
                        content = conv.get("content", {})
                        text = content.get("text", "")
                        if text:
                            conversation_messages.append({
                                "role": role,
                                "content": text
                            })
            
            if not conversation_messages:
                return JSONResponse(status_code=400, content={
                    "error": "No conversation messages found",
                    "message": "The session exists but contains no conversation messages."
                })
            
            print(f"[ANALYST-GENERATE-BRD] Formatted {len(conversation_messages)} conversation messages")
            
            # Format conversation as text transcript
            conversation_text = ""
            for msg in conversation_messages:
                role_label = "User" if msg["role"] == "USER" else "Analyst"
                conversation_text += f"{role_label}: {msg['content']}\n\n"
            
            print(f"[ANALYST-GENERATE-BRD] Conversation transcript length: {len(conversation_text)} chars")
            
            # Invoke brd_from_history_lambda
            lambda_client = get_lambda_client()
            lambda_function_name = os.getenv("LAMBDA_BRD_FROM_HISTORY", "sdlc-dev-brd-from-history")
            
            print(f"[ANALYST-GENERATE-BRD] Invoking Lambda: {lambda_function_name}")
            
            # Generate BRD ID
            brd_id = str(uuid.uuid4())
            
            lambda_payload = {
                "session_id": session_id,
                "brd_id": brd_id
            }
            
            lambda_response = lambda_client.invoke(
                FunctionName=lambda_function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(lambda_payload)
            )
            
            # Parse Lambda response
            response_payload = json.loads(lambda_response['Payload'].read())
            
            print(f"[ANALYST-GENERATE-BRD] Lambda response keys: {list(response_payload.keys()) if isinstance(response_payload, dict) else 'Not a dict'}")
            
            # Lambda returns: {'statusCode': 200, 'body': '{"brd_id": "...", ...}'}
            # Need to parse the 'body' field if it exists
            lambda_status_code = lambda_response['StatusCode']
            if isinstance(response_payload, dict) and 'statusCode' in response_payload:
                lambda_status_code = response_payload['statusCode']
            
            if lambda_status_code >= 400:
                error_message = 'Unknown error'
                if isinstance(response_payload, dict):
                    # Check if error is in 'body' (JSON string) or directly in response
                    if 'body' in response_payload:
                        try:
                            body_data = json.loads(response_payload['body'])
                            error_message = body_data.get('error', body_data.get('message', 'Unknown error'))
                        except:
                            error_message = response_payload.get('errorMessage', str(response_payload.get('body', 'Unknown error')))
                    else:
                        error_message = response_payload.get('errorMessage', response_payload.get('error', 'Unknown error'))
                print(f"[ANALYST-GENERATE-BRD] ❌ Lambda error: {error_message}")
                return JSONResponse(status_code=500, content={
                    "error": "BRD generation failed",
                    "message": f"Failed to generate BRD: {error_message}"
                })
            
            # Extract brd_id from response
            # Lambda returns: {'statusCode': 200, 'body': '{"brd_id": "...", ...}'}
            brd_id_from_response = None
            if isinstance(response_payload, dict):
                # First check if brd_id is directly in response_payload
                if 'brd_id' in response_payload:
                    brd_id_from_response = response_payload['brd_id']
                # Otherwise, parse the 'body' field
                elif 'body' in response_payload:
                    try:
                        body_data = json.loads(response_payload['body'])
                        brd_id_from_response = body_data.get('brd_id')
                        print(f"[ANALYST-GENERATE-BRD] ✅ Extracted brd_id from body: {brd_id_from_response}")
                    except json.JSONDecodeError as e:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ Failed to parse body as JSON: {e}")
                        print(f"[ANALYST-GENERATE-BRD] Body content: {response_payload['body'][:200]}")
            
            if brd_id_from_response:
                brd_id = brd_id_from_response
                print(f"[ANALYST-GENERATE-BRD] ✅ BRD generated successfully: {brd_id}")
                return JSONResponse(content={
                    "result": f"BRD generated successfully",
                    "brd_id": brd_id,
                    "session_id": session_id,
                    "message": "BRD has been generated and saved to S3"
                })
            else:
                print(f"[ANALYST-GENERATE-BRD] ⚠️ Lambda response missing brd_id")
                print(f"[ANALYST-GENERATE-BRD] Full response: {response_payload}")
                return JSONResponse(status_code=500, content={
                    "error": "BRD generation incomplete",
                    "message": "BRD generation completed but response format was unexpected"
                })
                
        except Exception as memory_error:
            print(f"[ANALYST-GENERATE-BRD] ❌ Error accessing AgentCore Memory: {memory_error}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={
                "error": "Failed to retrieve conversation history",
                "message": f"Error accessing AgentCore Memory: {str(memory_error)}"
            })
            
    except Exception as e:
        error_msg = str(e)
        print(f"[ANALYST-GENERATE-BRD] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "result": f"Error: {error_msg}",
            "type": "UnknownError"
        })

@app.post("/api/analyst-generate-brd")
async def analyst_generate_brd(
    session_id: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """Generate BRD from analyst conversation history stored in AgentCore Memory"""
    try:
        print(f"\n[ANALYST-GENERATE-BRD] Session ID received: {session_id}")
        
        # Get AgentCore Memory client
        agentcore_client = get_agent_core_client()
        memory_id = os.getenv("AGENTCORE_MEMORY_ID", "sdlc_dev_agentcore_memory-VF74Yf64ZB")
        actor_id = os.getenv("AGENTCORE_ACTOR_ID", "analyst-session")
        
        # If session_id is "none" or empty, try to find the most recent session
        if not session_id or session_id == "none":
            print(f"[ANALYST-GENERATE-BRD] Session ID is 'none', trying to find most recent session...")
            try:
                # Try to list sessions using list_sessions API
                print(f"[ANALYST-GENERATE-BRD] Attempting to find recent sessions using list_sessions...")
                try:
                    list_response = agentcore_client.list_sessions(
                        memoryId=memory_id,
                        actorId=actor_id,
                        maxResults=10
                    )
                    sessions = list_response.get('sessions', [])
                    if sessions:
                        # Sort by creation time to get the most recent
                        from datetime import datetime
                        sessions.sort(key=lambda x: x.get('creationTime', datetime.min.isoformat()), reverse=True)
                        most_recent_session = sessions[0]
                        session_id = most_recent_session.get('sessionId')
                        print(f"[ANALYST-GENERATE-BRD] ✅ Found most recent session: {session_id}")
                    else:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ No sessions found via list_sessions")
                        return JSONResponse(status_code=400, content={
                            "error": "No session found",
                            "message": "Please send at least one message in the analyst agent chat to establish a session, then try generating the BRD again."
                        })
                except Exception as list_error:
                    print(f"[ANALYST-GENERATE-BRD] ⚠️ list_sessions API not available or failed: {list_error}")
                    # Fallback: Try to find sessions by listing events and extracting unique session IDs
                    print(f"[ANALYST-GENERATE-BRD] Attempting fallback: listing events to find sessions...")
                    try:
                        # List recent events to find session IDs
                        events_response = agentcore_client.list_events(
                            memoryId=memory_id,
                            actorId=actor_id,
                            includePayloads=False,
                            maxResults=100
                        )
                        events = events_response.get("events", [])
                        # Extract unique session IDs
                        session_ids = set()
                        for event in events:
                            event_session_id = event.get("sessionId")
                            if event_session_id:
                                session_ids.add(event_session_id)
                        
                        if session_ids:
                            session_id_list = list(session_ids)
                            session_id = session_id_list[0]
                            print(f"[ANALYST-GENERATE-BRD] ✅ Found session via events: {session_id}")
                        else:
                            print(f"[ANALYST-GENERATE-BRD] ⚠️ No analyst sessions found in events")
                            return JSONResponse(status_code=400, content={
                                "error": "No session found",
                                "message": "Please send at least one message in the analyst agent chat to establish a session, then try generating the BRD again."
                            })
                    except Exception as events_error:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ Fallback method also failed: {events_error}")
                        return JSONResponse(status_code=400, content={
                            "error": "Session ID is required",
                            "message": "Please send at least one message in the analyst agent chat to establish a session, then try generating the BRD again."
                        })
            except Exception as e:
                print(f"[ANALYST-GENERATE-BRD] ❌ Error finding session: {e}")
                import traceback
                traceback.print_exc()
                return JSONResponse(status_code=400, content={
                    "error": "Session ID is required",
                    "message": "Please start a conversation with the analyst agent first before generating a BRD"
                })
        
        # Get conversation history from AgentCore Memory
        print(f"[ANALYST-GENERATE-BRD] Retrieving conversation history from AgentCore Memory...")
        print(f"[ANALYST-GENERATE-BRD] Session ID: {session_id}")
        print(f"[ANALYST-GENERATE-BRD] Memory ID: {memory_id}")
        print(f"[ANALYST-GENERATE-BRD] Actor ID: {actor_id}")
        
        # List events from AgentCore Memory for this session
        try:
            events_response = agentcore_client.list_events(
                memoryId=memory_id,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=100
            )
            events = events_response.get("events", [])
            print(f"[ANALYST-GENERATE-BRD] Retrieved {len(events)} events from AgentCore Memory")
            
            if not events:
                return JSONResponse(status_code=400, content={
                    "error": "No conversation history found",
                    "message": f"No conversation history found for session {session_id}. Please send at least one message in the analyst agent chat first."
                })
            
            # Format conversation history
            conversation_messages = []
            for event in events:
                payload = event.get("payload", [])
                for item in payload:
                    if "conversational" in item:
                        conv = item["conversational"]
                        role = conv.get("role", "USER")
                        content = conv.get("content", {})
                        text = content.get("text", "")
                        if text:
                            conversation_messages.append({
                                "role": role,
                                "content": text
                            })
            
            if not conversation_messages:
                return JSONResponse(status_code=400, content={
                    "error": "No conversation messages found",
                    "message": "The session exists but contains no conversation messages."
                })
            
            print(f"[ANALYST-GENERATE-BRD] Formatted {len(conversation_messages)} conversation messages")
            
            # Format conversation as text transcript
            conversation_text = ""
            for msg in conversation_messages:
                role_label = "User" if msg["role"] == "USER" else "Analyst"
                conversation_text += f"{role_label}: {msg['content']}\n\n"
            
            print(f"[ANALYST-GENERATE-BRD] Conversation transcript length: {len(conversation_text)} chars")
            
            # Invoke brd_from_history_lambda
            lambda_client = get_lambda_client()
            lambda_function_name = os.getenv("LAMBDA_BRD_FROM_HISTORY", "sdlc-dev-brd-from-history")
            
            print(f"[ANALYST-GENERATE-BRD] Invoking Lambda: {lambda_function_name}")
            
            # Generate BRD ID
            brd_id = str(uuid.uuid4())
            
            lambda_payload = {
                "session_id": session_id,
                "brd_id": brd_id
            }
            
            lambda_response = lambda_client.invoke(
                FunctionName=lambda_function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(lambda_payload)
            )
            
            # Parse Lambda response
            response_payload = json.loads(lambda_response['Payload'].read())
            
            print(f"[ANALYST-GENERATE-BRD] Lambda response keys: {list(response_payload.keys()) if isinstance(response_payload, dict) else 'Not a dict'}")
            
            # Lambda returns: {'statusCode': 200, 'body': '{"brd_id": "...", ...}'}
            # Need to parse the 'body' field if it exists
            lambda_status_code = lambda_response['StatusCode']
            if isinstance(response_payload, dict) and 'statusCode' in response_payload:
                lambda_status_code = response_payload['statusCode']
            
            if lambda_status_code >= 400:
                error_message = 'Unknown error'
                if isinstance(response_payload, dict):
                    # Check if error is in 'body' (JSON string) or directly in response
                    if 'body' in response_payload:
                        try:
                            body_data = json.loads(response_payload['body'])
                            error_message = body_data.get('error', body_data.get('message', 'Unknown error'))
                        except:
                            error_message = response_payload.get('errorMessage', str(response_payload.get('body', 'Unknown error')))
                    else:
                        error_message = response_payload.get('errorMessage', response_payload.get('error', 'Unknown error'))
                print(f"[ANALYST-GENERATE-BRD] ❌ Lambda error: {error_message}")
                return JSONResponse(status_code=500, content={
                    "error": "BRD generation failed",
                    "message": f"Failed to generate BRD: {error_message}"
                })
            
            # Extract brd_id from response
            # Lambda returns: {'statusCode': 200, 'body': '{"brd_id": "...", ...}'}
            brd_id_from_response = None
            if isinstance(response_payload, dict):
                # First check if brd_id is directly in response_payload
                if 'brd_id' in response_payload:
                    brd_id_from_response = response_payload['brd_id']
                # Otherwise, parse the 'body' field
                elif 'body' in response_payload:
                    try:
                        body_data = json.loads(response_payload['body'])
                        brd_id_from_response = body_data.get('brd_id')
                        print(f"[ANALYST-GENERATE-BRD] ✅ Extracted brd_id from body: {brd_id_from_response}")
                    except json.JSONDecodeError as e:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ Failed to parse body as JSON: {e}")
                        print(f"[ANALYST-GENERATE-BRD] Body content: {response_payload['body'][:200]}")
            
            if brd_id_from_response:
                brd_id = brd_id_from_response
                print(f"[ANALYST-GENERATE-BRD] ✅ BRD generated successfully: {brd_id}")
                return JSONResponse(content={
                    "result": f"BRD generated successfully",
                    "brd_id": brd_id,
                    "session_id": session_id,
                    "message": "BRD has been generated and saved to S3"
                })
            else:
                print(f"[ANALYST-GENERATE-BRD] ⚠️ Lambda response missing brd_id")
                print(f"[ANALYST-GENERATE-BRD] Full response: {response_payload}")
                return JSONResponse(status_code=500, content={
                    "error": "BRD generation incomplete",
                    "message": "BRD generation completed but response format was unexpected"
                })
                
        except Exception as memory_error:
            print(f"[ANALYST-GENERATE-BRD] ❌ Error accessing AgentCore Memory: {memory_error}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={
                "error": "Failed to retrieve conversation history",
                "message": f"Error accessing AgentCore Memory: {str(memory_error)}"
            })
            
    except Exception as e:
        error_msg = str(e)
        print(f"[ANALYST-GENERATE-BRD] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "result": f"Error: {error_msg}",
            "type": "UnknownError"
        })

@app.post("/api/analyst-generate-brd")
async def analyst_generate_brd(
    session_id: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """Generate BRD from analyst conversation history stored in AgentCore Memory"""
    try:
        print(f"\n[ANALYST-GENERATE-BRD] Session ID received: {session_id}")
        
        # Get AgentCore Memory client
        agentcore_client = get_agent_core_client()
        memory_id = os.getenv("AGENTCORE_MEMORY_ID", "sdlc_dev_agentcore_memory-VF74Yf64ZB")
        actor_id = os.getenv("AGENTCORE_ACTOR_ID", "analyst-session")
        
        # Get conversation history from AgentCore Memory
        print(f"[ANALYST-GENERATE-BRD] Retrieving conversation history from AgentCore Memory...")
        print(f"[ANALYST-GENERATE-BRD] Session ID: {session_id}")
        print(f"[ANALYST-GENERATE-BRD] Memory ID: {memory_id}")
        print(f"[ANALYST-GENERATE-BRD] Actor ID: {actor_id}")
        
        try:
            # First, try to list sessions to see what exists
            try:
                list_sessions_response = agentcore_client.list_sessions(
                    memoryId=memory_id,
                    actorId=actor_id,
                    maxResults=10
                )
                sessions = list_sessions_response.get('sessions', [])
                print(f"[ANALYST-GENERATE-BRD] Found {len(sessions)} sessions:")
                for sess in sessions:
                    print(f"[ANALYST-GENERATE-BRD]   - Session ID: {sess.get('sessionId')}, Created: {sess.get('creationTime')}")
            except Exception as list_err:
                error_str = str(list_err)
                print(f"[ANALYST-GENERATE-BRD] Could not list sessions: {list_err}")
                # If actor doesn't exist, try to create a session to create the actor
                if "not found" in error_str.lower() or "ResourceNotFoundException" in error_str:
                    print(f"[ANALYST-GENERATE-BRD] Actor {actor_id} not found, attempting to create session to initialize actor...")
                    try:
                        # Create a temporary session to initialize the actor
                        temp_session_id = f"temp-{session_id}"
                        agentcore_client.create_session(
                            memoryId=memory_id,
                            sessionId=temp_session_id,
                            actorId=actor_id
                        )
                        print(f"[ANALYST-GENERATE-BRD] ✅ Created temporary session to initialize actor")
                        # Now try to use the actual session
                        try:
                            agentcore_client.create_session(
                                memoryId=memory_id,
                                sessionId=session_id,
                                actorId=actor_id
                            )
                            print(f"[ANALYST-GENERATE-BRD] ✅ Created session {session_id} with actor {actor_id}")
                        except Exception as create_err:
                            if "already exists" not in str(create_err).lower() and "ConflictException" not in str(type(create_err).__name__):
                                print(f"[ANALYST-GENERATE-BRD] ⚠️ Could not create session: {create_err}")
                    except Exception as init_err:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ Could not initialize actor: {init_err}")
            
            # List events from AgentCore Memory for this session
            events_response = agentcore_client.list_events(
                memoryId=memory_id,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=100
            )
            events = events_response.get("events", [])
            print(f"[ANALYST-GENERATE-BRD] Retrieved {len(events)} events from AgentCore Memory")
            
            if not events:
                return JSONResponse(status_code=400, content={
                    "error": "No conversation history found",
                    "message": f"No conversation history found for session {session_id}. Please send at least one message in the analyst agent chat first."
                })
            
            # Format conversation history
            conversation_messages = []
            for event in events:
                payload = event.get("payload", [])
                for item in payload:
                    if "conversational" in item:
                        conv = item["conversational"]
                        role = conv.get("role", "USER")
                        content = conv.get("content", {})
                        text = content.get("text", "")
                        if text:
                            conversation_messages.append({
                                "role": role,
                                "content": text
                            })
            
            if not conversation_messages:
                return JSONResponse(status_code=400, content={
                    "error": "No conversation messages found",
                    "message": "The session exists but contains no conversation messages."
                })
            
            print(f"[ANALYST-GENERATE-BRD] Formatted {len(conversation_messages)} conversation messages")
            
            # Format conversation as text transcript
            conversation_text = ""
            for msg in conversation_messages:
                role_label = "User" if msg["role"] == "USER" else "Analyst"
                conversation_text += f"{role_label}: {msg['content']}\n\n"
            
            print(f"[ANALYST-GENERATE-BRD] Conversation transcript length: {len(conversation_text)} chars")
            
            # Invoke brd_from_history_lambda
            lambda_client = get_lambda_client()
            lambda_function_name = os.getenv("LAMBDA_BRD_FROM_HISTORY", "sdlc-dev-brd-from-history")
            
            print(f"[ANALYST-GENERATE-BRD] Invoking Lambda: {lambda_function_name}")
            
            # Generate BRD ID
            brd_id = str(uuid.uuid4())
            
            lambda_payload = {
                "session_id": session_id,
                "brd_id": brd_id
            }
            
        except Exception as e:
            print(f"[ANALYST-GENERATE-BRD] Error retrieving history: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={
                "error": f"Failed to retrieve conversation history: {str(e)}",
                "message": "Could not retrieve conversation history from AgentCore Memory"
            })
        
        # Format conversation as transcript
        transcript_lines = []
        for msg in messages:
            role = msg.get("role", "assistant").capitalize()
            content = msg.get("content", "")
            transcript_lines.append(f"{role}: {content}")
        
        transcript = "\n\n".join(transcript_lines)
        print(f"[ANALYST-GENERATE-BRD] Formatted transcript: {len(transcript)} characters")
        
        # Generate BRD ID
        brd_id = str(uuid.uuid4())
        
        # Get S3 bucket and template path
        s3_bucket = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
        template_s3_key = "templates/Deluxe_BRD_Template.docx"
        
        # Get Lambda client with increased timeout for long-running BRD generation
        from botocore.config import Config
        lambda_config = Config(
            read_timeout=900,  # 15 minutes - max Lambda execution time
            connect_timeout=10,
            retries={'max_attempts': 0}  # Don't retry on timeout
        )
        lambda_client = boto3.client('lambda', region_name=REGION, config=lambda_config)
        # Use lambda_brd_from_history for analyst agent BRD generation
        lambda_function_name = os.getenv("LAMBDA_BRD_FROM_HISTORY", "sdlc-dev-brd-from-history")
        
        # Prepare Lambda payload for lambda_brd_from_history
        # This Lambda expects: conversation_history (list of messages)
        lambda_payload = {
            "conversation_history": messages,  # Pass messages array directly
            "brd_id": brd_id,
            "session_id": session_id
        }
        
        print(f"[ANALYST-GENERATE-BRD] Calling Lambda: {lambda_function_name}")
        print(f"[ANALYST-GENERATE-BRD] BRD ID: {brd_id}")
        print(f"[ANALYST-GENERATE-BRD] Session ID: {session_id}")
        print(f"[ANALYST-GENERATE-BRD] Conversation messages: {len(messages)}")
        
        # Invoke Lambda
        try:
            lambda_response = lambda_client.invoke(
                FunctionName=lambda_function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(lambda_payload)
            )
            
            # Parse Lambda response
            response_payload = json.loads(lambda_response['Payload'].read())
            
            print(f"[ANALYST-GENERATE-BRD] Lambda response keys: {list(response_payload.keys()) if isinstance(response_payload, dict) else 'Not a dict'}")
            
            # Lambda returns: {'statusCode': 200, 'body': '{"brd_id": "...", ...}'}
            # Need to parse the 'body' field if it exists
            lambda_status_code = lambda_response['StatusCode']
            if isinstance(response_payload, dict) and 'statusCode' in response_payload:
                lambda_status_code = response_payload['statusCode']
            
            if lambda_status_code >= 400:
                error_message = 'Unknown error'
                if isinstance(response_payload, dict):
                    # Check if error is in 'body' (JSON string) or directly in response
                    if 'body' in response_payload:
                        try:
                            body_data = json.loads(response_payload['body'])
                            error_message = body_data.get('error', body_data.get('message', 'Unknown error'))
                        except:
                            error_message = response_payload.get('errorMessage', str(response_payload.get('body', 'Unknown error')))
                    else:
                        error_message = response_payload.get('errorMessage', response_payload.get('error', 'Unknown error'))
                print(f"[ANALYST-GENERATE-BRD] ❌ Lambda error: {error_message}")
                return JSONResponse(status_code=500, content={
                    "error": "BRD generation failed",
                    "message": f"Failed to generate BRD: {error_message}"
                })
            
            # Extract brd_id from response
            # Lambda returns: {'statusCode': 200, 'body': '{"brd_id": "...", ...}'}
            brd_id_from_response = None
            if isinstance(response_payload, dict):
                # First check if brd_id is directly in response_payload
                if 'brd_id' in response_payload:
                    brd_id_from_response = response_payload['brd_id']
                # Otherwise, parse the 'body' field
                elif 'body' in response_payload:
                    try:
                        body_data = json.loads(response_payload['body'])
                        brd_id_from_response = body_data.get('brd_id')
                        print(f"[ANALYST-GENERATE-BRD] ✅ Extracted brd_id from body: {brd_id_from_response}")
                    except json.JSONDecodeError as e:
                        print(f"[ANALYST-GENERATE-BRD] ⚠️ Failed to parse body as JSON: {e}")
                        print(f"[ANALYST-GENERATE-BRD] Body content: {response_payload['body'][:200]}")
            
            if brd_id_from_response:
                brd_id = brd_id_from_response
                print(f"[ANALYST-GENERATE-BRD] ✅ BRD generated successfully: {brd_id}")
                return JSONResponse(content={
                    "result": f"BRD generated successfully",
                    "brd_id": brd_id,
                    "session_id": session_id,
                    "message": "BRD has been generated and saved to S3"
                })
            else:
                print(f"[ANALYST-GENERATE-BRD] ⚠️ Lambda response missing brd_id")
                print(f"[ANALYST-GENERATE-BRD] Full response: {response_payload}")
                return JSONResponse(status_code=500, content={
                    "error": "BRD generation incomplete",
                    "message": "BRD generation completed but response format was unexpected"
                })
                
        except Exception as memory_error:
            print(f"[ANALYST-GENERATE-BRD] ❌ Error accessing AgentCore Memory: {memory_error}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={
                "error": "Failed to retrieve conversation history",
                "message": f"Error accessing AgentCore Memory: {str(memory_error)}"
            })

    except Exception as e:
        error_msg = str(e)
        print(f"[ANALYST-GENERATE-BRD] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "result": f"Error: {error_msg}",
            "type": "UnknownError"
        })

@app.get("/api/download-brd/{brd_id}")
async def download_brd(
    brd_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Download a generated BRD document as DOCX - fetches from S3 and converts to DOCX"""
    try:
        print(f"\n[DOWNLOAD-BRD] ========== START ==========")
        print(f"[DOWNLOAD-BRD] BRD ID: {brd_id}")
        print(f"[DOWNLOAD-BRD] User: {current_user.get('user_id', 'unknown')}")
        
        # Get S3 client
        s3_client = get_s3_client()
        bucket_name = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
        
        # BRD is stored as: brds/{brd_id}/BRD_{brd_id}.txt
        s3_key_txt = f"brds/{brd_id}/BRD_{brd_id}.txt"
        
        print(f"[DOWNLOAD-BRD] Fetching TXT from s3://{bucket_name}/{s3_key_txt}")
        
        # Get the BRD TXT file from S3
        try:
            head_response = s3_client.head_object(Bucket=bucket_name, Key=s3_key_txt)
            print(f"[DOWNLOAD] ✅ File exists in S3! Size: {head_response.get('ContentLength', 0)} bytes")
        except ClientError as head_err:
            head_error_code = head_err.response.get('Error', {}).get('Code', 'Unknown')
            print(f"[DOWNLOAD] ⚠️  head_object failed: {head_error_code} - {head_err}")
        
        try:
            # Try to get BRD JSON structure first (preferred for DOCX conversion)
            # Check both possible naming conventions
            json_key = f"brds/{brd_id}/brd_structure.json"
            try:
                try:
                    json_response = s3_client.get_object(Bucket=bucket_name, Key=json_key)
                except ClientError as e:
                    if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
                        json_key = f"brds/{brd_id}/BRD_{brd_id}.json"
                        json_response = s3_client.get_object(Bucket=bucket_name, Key=json_key)
                    else:
                        raise e
                
                # Read with explicit UTF-8 encoding and error handling
                json_body = json_response['Body'].read()
                print(f"[DOWNLOAD] Read {len(json_body)} bytes from JSON file")
                
                # Parse JSON
                try:
                    brd_data = json.loads(json_body)
                except json.JSONDecodeError as je:
                    print(f"[DOWNLOAD] ⚠️ Error parsing BRD JSON: {je}")
                    raise je

                # Convert JSON to DOCX
                docx_bytes = render_brd_json_to_docx(brd_data)
                
                print(f"[DOWNLOAD-BRD] ✅ Converted JSON to DOCX: {len(docx_bytes)} bytes")
                
                from fastapi.responses import Response
                return Response(
                    content=docx_bytes,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={
                        "Content-Disposition": f"attachment; filename=BRD_{brd_id}.docx"
                    }
                )
            except ClientError as json_err:
                json_error_code = json_err.response.get('Error', {}).get('Code', '')
                if json_error_code == 'NoSuchKey':
                    print(f"[DOWNLOAD] ⚠️  BRD JSON not found, trying text file...")
                    # Fallback to text file and convert to DOCX
                    response = s3_client.get_object(Bucket=bucket_name, Key=s3_key_txt)
                    # Read with explicit UTF-8 encoding and error handling
                    text_body = response['Body'].read()
                    print(f"[DOWNLOAD] Read {len(text_body)} bytes from text file")
                    
                    try:
                        brd_text = text_body.decode('utf-8')
                    except UnicodeDecodeError as decode_err:
                        # Try with error handling
                        print(f"[DOWNLOAD] ⚠️ UTF-8 decode error: {decode_err}, trying with error replacement")
                        brd_text = text_body.decode('utf-8', errors='replace')
                    
                    print(f"[DOWNLOAD] ✅ Fetched BRD text from S3 ({len(brd_text)} chars)")
                    print(f"[DOWNLOAD] First 300 chars: {brd_text[:300]}")
                    print(f"[DOWNLOAD] Last 100 chars: {brd_text[-100:]}")
                    
                    # Check if text looks like it's in English (basic check)
                    english_chars = sum(1 for c in brd_text[:500] if c.isascii() and (c.isalpha() or c.isspace() or c in '.,;:!?()-'))
                    total_chars = min(500, len(brd_text))
                    if total_chars > 0:
                        english_ratio = english_chars / total_chars
                        print(f"[DOWNLOAD] English character ratio (first 500 chars): {english_ratio:.2%}")
                        if english_ratio < 0.5:
                            print(f"[DOWNLOAD] ⚠️ WARNING: Text may not be in English! Ratio: {english_ratio:.2%}")
                    
                    # Convert text to DOCX with proper markdown parsing
                    docx_content = render_brd_json_to_docx(brd_text)  # This handles string input
                    
                    print(f"[DOWNLOAD] ✅ Converted text to DOCX ({len(docx_content)} bytes)")
                    
                    from fastapi.responses import Response
                    return Response(
                        content=docx_content,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={
                            "Content-Disposition": f'attachment; filename="BRD_{brd_id}.docx"'
                        }
                    )
                else:
                    raise json_err
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            
            print(f"[DOWNLOAD] ❌ S3 ClientError: Code={error_code}, Message={error_message}")
            print(f"[DOWNLOAD] Full error response: {e.response}")
            
            # Check credentials
            try:
                creds_valid, creds_info = check_aws_credentials()
                if not creds_valid:
                    print(f"[DOWNLOAD] ⚠️  AWS credentials check failed: {creds_info}")
                    return JSONResponse(
                        status_code=403,
                        content={"error": f"AWS credentials invalid or expired. Please refresh credentials. Details: {creds_info}"}
                    )
                else:
                    print(f"[DOWNLOAD] ✅ AWS credentials valid: {creds_info.get('Account', 'Unknown')}")
            except Exception as cred_err:
                print(f"[DOWNLOAD] ⚠️  Could not check credentials: {cred_err}")
            
            # Check if it's an access denied error
            if error_code == 'AccessDeniedException' or '403' in str(error_code) or 'Access Denied' in error_message:
                return JSONResponse(
                    status_code=403,
                    content={"error": f"Access denied to S3 bucket '{bucket_name}'. Please check IAM permissions for s3:GetObject. Error: {error_message}"}
                )
            
            if error_code == 'NoSuchKey':
                print(f"[DOWNLOAD] ❌ BRD text file not found in S3: {s3_key_txt}")
                # Try to get BRD JSON structure and render it to text
                try:
                    print(f"[DOWNLOAD] Attempting to fetch BRD JSON structure from S3...")
                    json_key = f"brds/{brd_id}/brd_structure.json"
                    try:
                        try:
                            json_response = s3_client.get_object(Bucket=bucket_name, Key=json_key)
                        except ClientError as e:
                            if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
                                json_key = f"brds/{brd_id}/BRD_{brd_id}.json"
                                json_response = s3_client.get_object(Bucket=bucket_name, Key=json_key)
                            else:
                                raise e
                        
                        # Read with explicit UTF-8 encoding and error handling
                        json_body = json_response['Body'].read()
                        try:
                            json_text = json_body.decode('utf-8')
                        except UnicodeDecodeError:
                            # Try with error handling
                            json_text = json_body.decode('utf-8', errors='replace')
                            print(f"[DOWNLOAD] ⚠️ Had to use error replacement for JSON decoding (fallback)")
                        
                        brd_json = json.loads(json_text)
                        print(f"[DOWNLOAD] ✅ Found BRD JSON structure, rendering to text...")
                        
                        # Render BRD JSON to text (simplified version of lambda_brd_chat.py render_brd_to_text)
                        brd_text = render_brd_json_to_text(brd_json)
                        
                        # Also save the text file for future downloads
                        try:
                            s3_put_object(
                                key=s3_key_txt,
                                body=brd_text,
                                content_type="text/plain",
                                bucket=bucket_name,
                            )
                            print(f"[DOWNLOAD] ✅ Saved rendered text file to S3 for future downloads")
                        except Exception as save_err:
                            print(f"[DOWNLOAD] ⚠️  Could not save text file: {save_err}")
                        
                        print(f"[DOWNLOAD] ✅ Rendered BRD from JSON ({len(brd_text)} chars)")
                        
                        # Convert to DOCX
                        docx_bytes = render_brd_json_to_docx(brd_json)
                        print(f"[DOWNLOAD] ✅ Generated DOCX ({len(docx_bytes)} bytes)")
                        
                        from fastapi.responses import Response
                        return Response(
                            content=docx_bytes,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={
                                "Content-Disposition": f'attachment; filename="BRD_{brd_id}.docx"'
                            }
                        )
                    except ClientError as json_err:
                        json_error_code = json_err.response.get('Error', {}).get('Code', '')
                        if json_error_code == 'NoSuchKey':
                            print(f"[DOWNLOAD] ❌ BRD JSON structure also not found")
                        else:
                            print(f"[DOWNLOAD] ❌ Error fetching JSON: {json_err}")
                except Exception as render_err:
                    print(f"[DOWNLOAD] ❌ Failed to render BRD from JSON: {render_err}")
                    import traceback
                    traceback.print_exc()
                
                return JSONResponse(
                    status_code=404,
                    content={"error": f"BRD {brd_id} not found in S3. Neither text file nor JSON structure found. The BRD may not have been saved yet."}
                )
            elif error_code == 'AccessDeniedException':
                return JSONResponse(
                    status_code=403,
                    content={"error": f"Access denied to S3 bucket. Please check AWS credentials and IAM permissions. Error: {error_message}"}
                )
            else:
                return JSONResponse(
                    status_code=500,
                    content={"error": f"S3 error ({error_code}): {error_message}"}
                )
        except Exception as e:
            print(f"[DOWNLOAD] ❌ Error fetching from S3: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={
                "error": "Failed to retrieve BRD",
                "message": f"Error accessing S3: {str(e)}"
            })
            
    except Exception as e:
        error_msg = str(e)
        print(f"[DOWNLOAD-BRD] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        
        return JSONResponse(status_code=500, content={
            "error": error_msg,
            "message": f"Error downloading BRD: {error_msg}"
        })

# -------------------------
# Analyst History Endpoint
# -------------------------

@app.get("/api/analyst-history/{session_id}")
async def get_analyst_history(session_id: str, current_user: dict = Depends(get_current_user)):
    """Get conversation history for analyst agent session"""
    try:
        print(f"\n[ANALYST-HISTORY] Retrieving history for session: {session_id}")
        
        # Get AgentCore Memory client
        agentcore_client = get_agent_core_client()
        memory_id = os.getenv("AGENTCORE_MEMORY_ID", "sdlc_dev_agentcore_memory-VF74Yf64ZB")
        actor_id = os.getenv("AGENTCORE_ACTOR_ID", "analyst-session")
        
        messages = []
        
        try:
            # List events from AgentCore Memory
            response = agentcore_client.list_events(
                memoryId=memory_id,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=99
            )
            
            events = response.get("events", [])
            print(f"[ANALYST-HISTORY] Retrieved {len(events)} events")

            for event in events:
                payload_list = event.get("payload", [])
                for payload_item in payload_list:
                    conv_data = payload_item.get("conversational")
                    if not conv_data:
                        continue

                    text_content = conv_data.get("content", {}).get("text")
                    if not text_content:
                        continue

                    role = conv_data.get("role", "assistant").lower()
                    messages.append({
                        "role": role,
                        "content": text_content,
                        "isBot": role == "assistant"
                    })

            messages.reverse()

            print(f"[ANALYST-HISTORY] Returning {len(messages)} messages")

            return JSONResponse(content={
                "messages": messages,
                "session_id": session_id
            })
            
        except Exception as e:
            print(f"[ANALYST-HISTORY] Error retrieving history: {e}")
            # Return empty history instead of error
            return JSONResponse(content={
                "messages": [],
                "session_id": session_id
            })
    
    except Exception as e:
        print(f"[ANALYST-HISTORY] ERROR: {e}")
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "messages": []
        })

# -------------------------
# BRD Chat History Endpoint (my_agent memory)
# -------------------------

def _extract_clean_user_message(text: str) -> str:
    """Extract the clean user message from the enhanced context sent to the LLM.

    The frontend wraps user messages with section context in formats like:
      SECTION N: Title\\n\\n{content}\\n\\nUSER REQUEST: {actual message}\\n\\nIMPORTANT: ...
      BRD CONTEXT:\\n{content}\\n\\nUSER REQUEST: {actual message}
    This extracts just the {actual message} part for clean chat display.
    """
    import re

    # 1. Extract text after "USER REQUEST: " marker (strips the section context prefix)
    marker = "USER REQUEST: "
    idx = text.find(marker)
    if idx != -1:
        clean = text[idx + len(marker):]
    else:
        clean = text

    # 2. Strip trailing IMPORTANT instruction block — use regex to handle any whitespace (\n, \r\n, etc.)
    clean = re.split(r'\s+IMPORTANT:\s+The user is currently viewing', clean, maxsplit=1)[0]

    return clean.strip()


@app.get("/api/brd-history/{session_id}")
async def get_brd_chat_history(
    session_id: str,
    project_id: str = None,
    current_user: dict = Depends(get_current_user)
):
    """Get conversation history for BRD chat from AgentCore Memory.

    Uses the same memory the Lambda writes to (AGENTCORE_MEMORY_ID),
    mirroring the working /api/analyst-history pattern.
    """
    try:
        print(f"\n[BRD-HISTORY] Retrieving history from AgentCore Memory for session: {session_id}")

        agentcore_client = get_agent_core_client()
        memory_id = os.getenv("AGENTCORE_MEMORY_ID", "sdlc_dev_agentcore_memory-VF74Yf64ZB")
        actor_id = os.getenv("AGENTCORE_ACTOR_ID", "brd-session")

        print(f"[BRD-HISTORY] Query params: memoryId={memory_id}, sessionId={session_id}, actorId={actor_id}")

        messages = []

        try:
            response = agentcore_client.list_events(
                memoryId=memory_id,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=99
            )

            events = response.get("events", [])
            print(f"[BRD-HISTORY] AgentCore returned {len(events)} events")

            # Sort events by (eventTimestamp, eventId) oldest first.
            # list_events may return in undefined order. Using eventId as
            # a secondary key ensures correct ordering when timestamps
            # are identical or have low precision.
            def _event_sort_key(e):
                ts = e.get("eventTimestamp")
                if ts is None:
                    ts_str = ""
                else:
                    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                eid = e.get("eventId", "")
                return (ts_str, eid)
            events = sorted(events, key=_event_sort_key)

            for event in events:
                payload_list = event.get("payload", [])
                for payload_item in payload_list:
                    conv_data = payload_item.get("conversational")
                    if not conv_data:
                        continue
                    text_content = conv_data.get("content", {}).get("text")
                    if not text_content:
                        continue
                    role = conv_data.get("role", "assistant").lower()
                    if role == "user":
                        # Strip the enhanced section context; keep only the actual user message
                        clean_text = _extract_clean_user_message(text_content)
                        messages.append({
                            "role": "user",
                            "content": clean_text,
                            "isBot": False
                        })
                    elif role == "assistant":
                        messages.append({
                            "role": "assistant",
                            "content": text_content,
                            "isBot": True
                        })

            # Already sorted oldest-first above — no need to reverse

        except Exception as e:
            print(f"[BRD-HISTORY] AgentCore Memory query failed: {e}")
            import traceback
            traceback.print_exc()

        print(f"[BRD-HISTORY] Returning {len(messages)} messages")

        return JSONResponse(content={
            "messages": messages,
            "session_id": session_id,
            "count": len(messages)
        })

    except Exception as e:
        print(f"[BRD-HISTORY] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "messages": []
        })

# -------------------------
# Access Control Endpoints
# -------------------------

@app.get("/api/brd/access")
async def check_brd_access(current_user: dict = Depends(get_current_user)):
    """Check if current user has BRD access"""
    user_id = current_user["user_id"]
    has_access = check_brd_access_via_agentcore(user_id)
    
    return JSONResponse(content={
        "has_access": has_access,
        "user_id": user_id,
        "email": current_user["email"]
    })

@app.get("/api/user/info")
async def get_user_info(current_user: dict = Depends(get_current_user)):
    """Get current user information including group-based module access"""
    user_id = current_user["user_id"]
    identity_arn = get_user_identity_arn(user_id)

    return JSONResponse(content={
        "user_id": user_id,
        "email": current_user["email"],
        "name": current_user.get("name", ""),
        "identity_arn": identity_arn,
        "groups": current_user.get("groups", []),
        "allowed_modules": current_user.get("allowed_modules", []),
    })

@app.get("/api/support/user-guide")
async def get_support_user_guide(current_user: dict = Depends(get_current_user)):
    """Return the user guide HTML extracted from the MHTML .doc in S3.

    Images embedded in the MHTML are converted to inline base64 data URIs
    so they render correctly in the browser popup.
    """
    import email as email_lib
    import base64 as b64

    s3_client = get_s3_client()
    try:
        response = s3_client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key="support/SDLC_Orchestrator_Userguide_ForTesting.doc",
        )
        raw = response["Body"].read().decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load user guide from S3: {e}")

    msg = email_lib.message_from_string(raw)

    # 1. Build a map of reference → base64 data URI for all non-HTML parts
    image_map: dict[str, str] = {}
    html_content = ""

    for part in msg.walk():
        ct = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue

        if "html" in ct:
            html_content = payload.decode("utf-8", errors="ignore")
            continue

        # Skip multipart wrappers
        if ct.startswith("multipart/"):
            continue

        # Detect image type from payload magic bytes
        mime = ct
        if ct == "application/octet-stream":
            if payload[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif payload[:2] == b"\xff\xd8":
                mime = "image/jpeg"
            elif payload[:4] == b"GIF8":
                mime = "image/gif"
            elif payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                mime = "image/png"  # fallback

        data_uri = f"data:{mime};base64,{b64.b64encode(payload).decode()}"

        # Map by Content-Location (e.g. "file:///C:/abc123")
        loc = part.get("Content-Location", "")
        if loc:
            image_map[loc] = data_uri
            # Also map the bare filename (HTML often uses just "abc123")
            fname = loc.rsplit("/", 1)[-1] if "/" in loc else loc
            if fname:
                image_map[fname] = data_uri

        # Map by Content-ID
        cid = part.get("Content-ID", "").strip("<>")
        if cid:
            image_map[f"cid:{cid}"] = data_uri

    if not html_content:
        raise HTTPException(status_code=500, detail="Could not extract HTML from user guide")

    # 2. Replace image src references with inline data URIs
    # Sort by length descending to avoid partial replacements
    for ref in sorted(image_map, key=len, reverse=True):
        html_content = html_content.replace(ref, image_map[ref])

    # 3. Inject clean styling for the popup
    style_block = """
    <style>
      body, .WordSection1 { font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.7; color: #1a1a1a; }
      h1, h2, h3 { color: #0f4c81; margin-top: 1.5em; margin-bottom: 0.5em; }
      p { margin: 0.6em 0; }
      img { max-width: 100%; height: auto; border-radius: 6px; margin: 12px 0; box-shadow: 0 1px 4px rgba(0,0,0,0.12); }
      table { border-collapse: collapse; width: 100%; margin: 1em 0; }
      td, th { border: 1px solid #ddd; padding: 8px 12px; }
      a { color: #0078d4; }
      ul, ol { margin: 0.5em 0; padding-left: 1.5em; }
      li { margin: 0.3em 0; }
    </style>
    """
    html_content = style_block + html_content

    return JSONResponse(content={"html": html_content})


@app.post("/api/admin/grant-brd-access")
async def grant_brd_access(
    target_user_id: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """Grant BRD access to a user"""
    success = grant_brd_access_via_agentcore(target_user_id)
    return JSONResponse(content={"success": success, "user_id": target_user_id})

@app.post("/api/admin/revoke-brd-access")
async def revoke_brd_access(
    target_user_id: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    """Revoke BRD access from a user"""
    success = revoke_brd_access_via_agentcore(target_user_id)
    return JSONResponse(content={"success": success, "user_id": target_user_id})


# -------------------------
# BRD Read APIs (S3-backed)
# -------------------------

def _load_brd_structure_from_s3(brd_id: str) -> dict:
    """Load the latest BRD structure JSON from S3."""
    s3_client = get_s3_client()
    bucket_name = os.getenv("S3_BUCKET_NAME", "sdlc-orch-dev-us-east-1-app-data")
    key = f"brds/{brd_id}/brd_structure.json"
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        body = response["Body"].read()
        return json.loads(body)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        raise HTTPException(status_code=404, detail=f"BRD structure not found in S3 ({code}): {key}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"BRD structure JSON is invalid: {str(e)}")


def _is_doc_title_section(title: str) -> bool:
    """Detect if the first section is a document title (not a real BRD section).

    Real BRD sections always start with a number like "1. Document Overview".
    The LLM sometimes prepends a project-name section without a number prefix;
    this helper identifies those so ``_iter_user_sections`` can skip them.
    """
    t = (title or "").strip()
    if not t:
        return False
    # If the first section starts with a digit it is a real numbered section.
    if re.match(r"^\d+\.", t):
        return False
    # Everything else (project names, headings without numbers) is a doc title.
    return True


def _iter_user_sections(brd_data: dict):
    """Yield (user_section_number, array_index, title, section_dict) for user-visible sections."""
    sections = brd_data.get("sections", []) if isinstance(brd_data, dict) else []
    if not sections:
        return

    start_idx = 0
    if sections and _is_doc_title_section(sections[0].get("title", "")):
        start_idx = 1

    user_num = 1
    for idx in range(start_idx, len(sections)):
        sec = sections[idx]
        title = (sec.get("title", "") or "").strip()
        title_lower = title.lower()

        # Skip ALL subsections whose title starts with "#"
        # These are sub-headers within a parent section (e.g. "# User Story 1: ...",
        # "# In Scope", "# Out of Scope", "# Acronyms and Abbreviations", "# Appendix")
        # and should not appear as top-level BRD sections.
        if title.startswith("#"):
            continue

        yield user_num, idx, title, sec
        user_num += 1


def _get_user_section_by_number(brd_data: dict, section_number: int) -> dict:
    for user_num, idx, title, sec in _iter_user_sections(brd_data) or []:
        if user_num == section_number:
            return {"array_index": idx, "title": title, "section": sec}
    raise HTTPException(status_code=404, detail=f"Section {section_number} not found")


def _render_section_to_markdown(section_number: int, title: str, section: dict, brd_data: dict) -> str:
    sections = brd_data.get("sections", [])
    content_blocks = list(section.get("content", []) or [])

    # Scope (section 5): merge content from "# In Scope" and "# Out of Scope" if main section is empty
    if (not content_blocks or len(content_blocks) == 0) and "scope" in (title or "").lower():
        merged = []
        # Find the array index of the scope section and look ahead for subsections
        scope_idx = None
        for user_num, idx, t, _sec in _iter_user_sections(brd_data) or []:
            if user_num == section_number:
                scope_idx = idx
                break
        if scope_idx is not None:
            for i in (1, 2):
                sub_idx = scope_idx + i
                if sub_idx < len(sections):
                    sub = sections[sub_idx]
                    sub_title = (sub.get("title", "") or "").lower()
                    if "# in scope" in sub_title:
                        merged.append({"type": "paragraph", "text": "### In Scope"})
                        merged.extend(sub.get("content", []) or [])
                    elif "# out of scope" in sub_title:
                        merged.append({"type": "paragraph", "text": "### Out of Scope"})
                        merged.extend(sub.get("content", []) or [])
        if merged:
            content_blocks = merged

    title_clean = re.sub(r"^\d+\.\s*", "", title or "").strip() or "Untitled"
    md = f"## {section_number}. {title_clean}\n\n"

    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "paragraph":
            md += (block.get("text", "") or "") + "\n\n"
        elif block_type == "bullet":
            for item in block.get("items", []) or []:
                md += f"- {item}\n"
            md += "\n"
        elif block_type == "table":
            rows = block.get("rows", []) or []
            for row in rows:
                md += "| " + " | ".join(str(cell) for cell in row) + " |\n"
            md += "\n"

    return md.strip() + "\n"


@app.get("/api/brd/{brd_id}/structure")
async def api_get_brd_structure(
    brd_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the latest structured BRD JSON from S3 (source of truth for sections)."""
    brd_data = _load_brd_structure_from_s3(brd_id)
    return JSONResponse(content={"brd_id": brd_id, "brd": brd_data})


@app.get("/api/brd/{brd_id}/sections")
async def api_list_brd_sections(
    brd_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return user-visible section numbers + titles (use this to build tabs)."""
    brd_data = _load_brd_structure_from_s3(brd_id)
    sections = [{"number": n, "title": re.sub(r"^\\d+\\.\\s*", "", t).strip() or t} for n, _idx, t, _sec in (_iter_user_sections(brd_data) or [])]
    return JSONResponse(content={"brd_id": brd_id, "sections": sections})


@app.get("/api/brd/{brd_id}/section/{section_number}")
async def api_get_brd_section(
    brd_id: str,
    section_number: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the latest version of a single section from S3.

    This is the safest way to power section-tabs: it always reads the newest `brd_structure.json`.
    """
    brd_data = _load_brd_structure_from_s3(brd_id)
    found = _get_user_section_by_number(brd_data, section_number)
    title = found["title"]
    section = found["section"]
    markdown = _render_section_to_markdown(section_number, title, section, brd_data)
    return JSONResponse(content={
        "brd_id": brd_id,
        "section_number": section_number,
        "title": title,
        "section": section,
        "markdown": markdown,
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
