"""
Design Architecture Router
Provides endpoints for generating architecture prompts (for Lucid Chart)
and draw.io XML diagrams directly from Confluence page content.
Calls Bedrock Claude directly — no guardrails, no BRD session required.
"""

import json
import os
import logging
import boto3
from datetime import datetime
from botocore.config import Config as BotoConfig
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
from auth import verify_azure_token
from db_helper import create_or_update_user, get_user_atlassian_credentials, get_project
from services.confluence_service import ConfluenceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/design", tags=["design"])

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

PROMPT_MODEL_ID    = os.getenv("DESIGN_PROMPT_MODEL_ID",    "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
XML_MODEL_ID       = os.getenv("DESIGN_XML_MODEL_ID",       "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
DOCUMENT_MODEL_ID  = os.getenv("DESIGN_DOCUMENT_MODEL_ID",  "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
PROMPT_MAX_TOKENS  = int(os.getenv("DESIGN_PROMPT_MAX_TOKENS",   "32768"))
XML_MAX_TOKENS     = int(os.getenv("DESIGN_XML_MAX_TOKENS",      "16384"))
DOCUMENT_MAX_TOKENS = int(os.getenv("DESIGN_DOCUMENT_MAX_TOKENS", "8192"))


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
        logger.error(f"[DESIGN] Auth error: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ─── Bedrock helper ───────────────────────────────────────────────────────────

def _invoke_claude(
    system_prompt: str,
    user_message: str,
    model_id: str = XML_MODEL_ID,
    max_tokens: int = XML_MAX_TOKENS,
) -> str:
    """
    Call Bedrock Claude synchronously and return the full text response.
    No guardrails — suitable for technical/architecture content.
    """
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
        logger.error(f"[DESIGN] Bedrock invoke error: {e}")
        raise HTTPException(status_code=502, detail=f"AI model error: {str(e)}")


# ─── Request / Response models ────────────────────────────────────────────────

class GeneratePromptRequest(BaseModel):
    page_contents: List[str]   # Plain-text content of each selected Confluence page


class GeneratePromptResponse(BaseModel):
    prompt: str


class GenerateXMLRequest(BaseModel):
    prompt: str                # The (possibly user-edited) architecture prompt


class GenerateXMLResponse(BaseModel):
    xml: str


class GenerateDocumentRequest(BaseModel):
    xml: str                   # draw.io XML of the architecture diagram
    prompt: str                # Architecture prompt (for project name and context)


class GenerateDocumentResponse(BaseModel):
    document: str              # Markdown architecture document


class PushToConfluenceRequest(BaseModel):
    project_id: str            # Used to look up Confluence credentials
    space_key: str             # Confluence space to create the page in
    title: str                 # Page title
    document: str              # Markdown content


class PushToConfluenceResponse(BaseModel):
    page_url: str
    page_id: str


class ListDiagramsRequest(BaseModel):
    project_id: str
    space_key: str


class DiagramPageInfo(BaseModel):
    page_id: str
    title: str
    page_url: str
    last_modified: str


class ListDiagramsResponse(BaseModel):
    diagrams: List[DiagramPageInfo]


class SaveDiagramRequest(BaseModel):
    project_id: str            # Used to look up Confluence credentials
    space_key: str             # Confluence space to save in
    xml: str                   # draw.io XML
    page_title: str = ""       # Defaults to "Architecture Diagram — <space_key>"


class SaveDiagramResponse(BaseModel):
    page_url: str
    page_id: str


class LoadDiagramRequest(BaseModel):
    project_id: str
    space_key: str
    page_title: str = ""       # Must match the title used when saving
    page_id: str = ""          # If provided, load directly by ID (avoids title-search issues)


class LoadDiagramResponse(BaseModel):
    xml: str
    page_url: str
    page_id: str



# ─── Endpoints ────────────────────────────────────────────────────────────────

ARCHITECTURE_SYSTEM_PROMPT = """
You are a senior AWS solutions architect. You will be given content from one or more
Confluence pages describing a software project. Read everything carefully, then output
a single fully-populated prompt that any AI tool (ChatGPT, Gemini, Claude) can use to
generate a professional draw.io-compatible XML architecture diagram.

Output only the filled prompt — no XML, no explanations, no extra commentary.
If the document does not mention networking, security, or monitoring details,
add the standard AWS components a senior architect would normally include and mark them (inferred).

═══════════════════════════════════════════════════════════════
STEP 1 — UNDERSTAND WHAT TO EXTRACT
═══════════════════════════════════════════════════════════════

Read the document and identify:
  - Project name and what the system does
  - Core features and functional modules (focus on the most important ~20 components)
  - External third-party integrations mentioned
  - Scale, performance, and security context (infer if not stated)

Keep the component list focused. A good target per layer:
  Users / Entry Point       → 1 to 2 components
  Frontend                  → 2 to 3 components
  Backend / Compute         → 4 to 6 components
  Data                      → 3 to 4 components
  Security                  → 2 to 3 components
  Monitoring                → 1 to 2 components
  External integrations     → 1 to 3 components

═══════════════════════════════════════════════════════════════
STEP 2 — TECHNICAL STANDARDS TO APPLY (use these internally)
═══════════════════════════════════════════════════════════════

OFFICIAL AWS ICON SHAPES
Every AWS service must use its official draw.io icon shape.
Never use plain rectangles for AWS services.

  Users / Clients    →  shape=mxgraph.aws4.user;
  CloudFront         →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudfront
  Route 53           →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.route_53
  API Gateway        →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.api_gateway
  ALB                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.application_load_balancer
  Lambda             →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda
  ECS / Fargate      →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.fargate
  EKS                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.eks
  EC2                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2
  S3                 →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3
  RDS                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.rds
  DynamoDB           →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.dynamodb
  ElastiCache        →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.elasticache
  SQS                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sqs
  SNS                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sns
  Cognito            →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cognito
  IAM                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.role
  WAF                →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.waf
  Secrets Manager    →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.secrets_manager
  CloudWatch         →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudwatch
  Bedrock            →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.bedrock
  SageMaker          →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sagemaker
  Kinesis            →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.kinesis
  Step Functions     →  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.step_functions
  VPC container      →  shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc
  Public Subnet      →  shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_public_subnet
  Private Subnet     →  shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_private_subnet

Each icon cell must follow this exact XML format:
  <mxCell id="lambda_fn" value="Order Processor"
    style="outlineConnect=0;fontColor=#232F3E;gradientColor=none;
           strokeColor=none;fillColor=#ED7100;
           shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;
           labelPosition=center;verticalLabelPosition=bottom;
           verticalAlign=top;align=center;html=1;fontSize=11;"
    vertex="1" parent="1">
    <mxGeometry x="500" y="400" width="60" height="60" as="geometry"/>
  </mxCell>

AWS OFFICIAL COLOR PALETTE (mandatory — this is what makes the diagram look professional)
  Compute (Lambda, EC2, ECS, EKS, Fargate)             →  fillColor=#ED7100  (orange)
  Storage (S3, EFS, Glacier)                            →  fillColor=#3F8624  (green)
  Database (RDS, DynamoDB, Aurora, ElastiCache)         →  fillColor=#1A9C3E  (dark green)
  Networking (CloudFront, Route 53, ALB, API GW, VPC)  →  fillColor=#8C4FFF  (purple)
  Security (Cognito, WAF, IAM, Secrets Manager)         →  fillColor=#DD344C  (red)
  Messaging (SQS, SNS, EventBridge, Step Functions)     →  fillColor=#E7157B  (pink)
  AI / ML (Bedrock, SageMaker)                          →  fillColor=#01A88D  (teal)
  Monitoring (CloudWatch, CloudTrail)                   →  fillColor=#E7157B  (pink-orange)
  Users / Clients                                       →  fillColor=#232F3E  (dark)

CONTAINER BACKGROUND COLORS
  VPC container  →  fill=#F0F8FF  stroke=#8C4FFF
  Public Subnet  →  fill=#E8F5E9  stroke=#3F8624
  Private Subnet →  fill=#FFF3E0  stroke=#ED7100
  External Zone  →  fill=#F5F5F5  stroke=#999999

CANVAS AND SPACING RULES
  Canvas size        →  1600px wide x 1000px tall
  Icon size          →  60px x 60px for every AWS service icon
  Horizontal spacing →  minimum 120px between icon centers
  Vertical spacing   →  minimum 100px between rows
  Labels             →  placed below each icon, font size 11px, color #232F3E
  Flow direction     →  left to right, top to bottom
  VPC boundary       →  wraps all compute and data layer components

LAYOUT ZONES (x positions for placing components)
  x=50  to x=200   →  Users and external clients
  x=250 to x=500   →  Entry layer: CloudFront, Route 53, WAF, API Gateway
  x=550 to x=950   →  Compute layer: Lambda, ECS, EKS, Step Functions
  x=1000 to x=1250 →  Data layer: RDS, DynamoDB, ElastiCache, S3
  x=1300 to x=1550 →  Monitoring and external services

ARROW STYLES
  Synchronous call (solid line):
    edgeStyle=orthogonalEdgeStyle;html=1;rounded=1;
    strokeColor=#555555;strokeWidth=1.5;fontSize=10;fontColor=#333333;
    exitX=1;exitY=0.5;entryX=0;entryY=0.5;

  Async / event-driven (dashed line):
    edgeStyle=orthogonalEdgeStyle;html=1;rounded=1;
    strokeColor=#999999;strokeWidth=1.5;dashed=1;fontSize=10;fontColor=#666666;

  Every arrow must have a label: "HTTPS", "Publishes event", "Reads/Writes", "Authenticates"
  Use dashed for: queues, event triggers, async jobs, webhooks
  Use solid for: synchronous API calls, direct reads/writes, user requests
  If an arrow crosses an icon, add mxPoint waypoints to route around it

═══════════════════════════════════════════════════════════════
STEP 3 — OUTPUT THIS PROMPT FULLY POPULATED
═══════════════════════════════════════════════════════════════

=============================================================
PROMPT: GENERATE AWS DRAW.IO ARCHITECTURE DIAGRAM
=============================================================

TASK
Generate a valid draw.io XML file for the AWS architecture described below.
Output raw XML only — no markdown, no explanations, no code fences.

PROJECT
  Name:        [project name]
  Description: [1 to 2 sentence summary]

COMPONENTS (~20 total)
  id | Display Label | AWS Service | icon shape style | fillColor | x | y
  [list all components — one per line]

CONTAINERS
  id | Label | x | y | width | height | fill | stroke
  [list all containers — one per line]

CONNECTIONS
  source id → target id | label | solid or dashed
  [list all connections — one per line]

DIAGRAM TITLE
  Text: "[Project Name] – AWS Architecture"
  Position: x=600, y=20, font 20px bold, color #232F3E

XML RULES (follow exactly)
  1.  Canvas: pageWidth="1600" pageHeight="1000"
  2.  Every icon: 60x60px, label below (verticalLabelPosition=bottom)
  3.  Every icon must include correct fillColor from AWS palette
  4.  Every icon style must include: outlineConnect=0; strokeColor=none;
      labelPosition=center; verticalLabelPosition=bottom;
      verticalAlign=top; align=center; html=1;
  5.  Containers use group style with rounded corners
  6.  All arrows: edgeStyle=orthogonalEdgeStyle; rounded=1; html=1
  7.  Add mxPoint waypoints on arrows that cross icons or containers
  8.  All mxCell ids must be unique snake_case strings
  9.  Never use bare & — write "and" instead
  10. Output raw XML starting with <mxfile ...>

=============================================================
END OF PROMPT
=============================================================

═══════════════════════════════════════════════════════════════
STEP 4 — FINAL CHECKLIST
═══════════════════════════════════════════════════════════════

Before outputting, verify:
  - ~20 most important components selected
  - Every component has the correct AWS icon shape
  - Every component has the correct AWS category fill color
  - x/y positions use the correct layout zones
  - All containers defined with correct colors
  - Every connection has a label and correct arrow type
  - Zero placeholders remaining

Output only the populated prompt. Do not generate XML. Do not explain your choices.
"""


DOCUMENT_SYSTEM_PROMPT = """
You are a senior AWS solutions architect writing formal architecture documentation.
You will be given two inputs:
  1. A draw.io XML file describing an AWS architecture diagram (components, connections, containers)
  2. An architecture prompt that has the project name and description

Read both inputs carefully and produce a complete, professional Architecture Document in Markdown.

═══════════════════════════════════════════════════════════════
WHAT TO EXTRACT FROM THE XML
═══════════════════════════════════════════════════════════════

Parse the XML to identify:
  - All mxCell elements with vertex="1" that represent AWS services (use their `value` label as the component name)
  - All mxCell elements with edge="1" that represent connections between components (use their `value` label as the data flow description)
  - Container/group cells that represent VPC, subnets, or logical layers
  - The overall structure: which layer each component belongs to (entry, compute, data, security, monitoring)

Extract the project name from the architecture prompt.

═══════════════════════════════════════════════════════════════
DOCUMENT STRUCTURE (output exactly this structure)
═══════════════════════════════════════════════════════════════

# [Project Name] — Architecture Document

## 1. Executive Summary
2-3 sentence overview of what the system does, who it serves, and its key architectural characteristics.

## 2. Architecture Overview
Describe the overall architecture pattern (e.g., microservices, serverless, event-driven).
Explain the main layers and how they interact at a high level.

## 3. Components

For each AWS component found in the XML, write a subsection:

### 3.x [Component Label] — [AWS Service Name]
- **Purpose:** What this component does in the system
- **Role in Architecture:** Which layer it belongs to and why it was chosen
- **Key Interactions:** Which other components it connects to and how

Group components by layer:
  - Entry / Networking Layer
  - Compute / Backend Layer
  - Data Layer
  - Security Layer
  - Monitoring and Observability

## 4. Data Flow

Describe the main request/data flows through the system step by step.
Base this on the edge connections found in the XML.
Use numbered steps, e.g.:
  1. User sends HTTPS request → CloudFront
  2. CloudFront routes → API Gateway
  ...

## 5. Security Architecture
List all security components and controls in the system.
Explain how authentication, authorization, and data protection are handled.

## 6. Scalability and Performance
Explain how the architecture scales under load.
Mention any auto-scaling, caching, or load balancing components.

## 7. Monitoring and Observability
Describe how the system is monitored.
List monitoring components and what they observe.

## 8. External Integrations
List any third-party or external services the system integrates with.
Describe the integration pattern for each.

## 9. Architecture Decisions
List 3-5 key architectural decisions made and the reasoning behind each.

═══════════════════════════════════════════════════════════════
OUTPUT RULES
═══════════════════════════════════════════════════════════════

- Output only valid Markdown — no code fences around the entire document
- Use ## for section headings, ### for component subsections
- Use **bold** for field labels like Purpose, Role, etc.
- Use numbered lists for data flows, bullet lists for everything else
- Do not add any preamble or explanation before the # heading
- If a section has no relevant components (e.g., no monitoring found in XML), still include the section but note "Standard AWS CloudWatch monitoring is recommended"
- Write in a professional, technical tone suitable for an engineering team
"""


def _markdown_to_confluence_xhtml(markdown: str) -> str:
    """
    Convert Markdown to Confluence storage format (XHTML subset).
    Handles the most common Markdown patterns used in architecture documents.
    """
    lines = markdown.split("\n")
    html_parts = []
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            html_parts.append("</ul>")
            in_ul = False
        if in_ol:
            html_parts.append("</ol>")
            in_ol = False

    for line in lines:
        # Headings
        if line.startswith("#### "):
            close_lists()
            html_parts.append(f"<h4>{line[5:].strip()}</h4>")
        elif line.startswith("### "):
            close_lists()
            html_parts.append(f"<h3>{line[4:].strip()}</h3>")
        elif line.startswith("## "):
            close_lists()
            html_parts.append(f"<h2>{line[3:].strip()}</h2>")
        elif line.startswith("# "):
            close_lists()
            html_parts.append(f"<h1>{line[2:].strip()}</h1>")
        # Numbered list
        elif re.match(r"^\d+\.\s", line):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                html_parts.append("<ol>")
                in_ol = True
            item = re.sub(r"^\d+\.\s", "", line)
            item = _inline_md(item)
            html_parts.append(f"<li>{item}</li>")
        # Bullet list
        elif line.startswith("- ") or line.startswith("* "):
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            item = _inline_md(line[2:])
            html_parts.append(f"<li>{item}</li>")
        # Blank line
        elif line.strip() == "":
            close_lists()
            html_parts.append("")
        # Normal paragraph
        else:
            close_lists()
            html_parts.append(f"<p>{_inline_md(line)}</p>")

    close_lists()
    return "\n".join(html_parts)


def _inline_md(text: str) -> str:
    """Convert inline Markdown (bold, italic, code) to XHTML."""
    # Bold+italic ***text***
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    # Bold **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic *text*
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code `text`
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


@router.post("/generate-prompt", response_model=GeneratePromptResponse)
async def generate_architecture_prompt(
    request: GeneratePromptRequest,
    _current_user: dict = Depends(get_current_user),
):
    """
    Combine content from multiple Confluence pages and generate a
    fully self-contained architecture prompt (v3.0) suitable for
    Lucid Chart or any AI tool to produce a draw.io XML diagram.
    """
    if not request.page_contents:
        raise HTTPException(status_code=400, detail="No page contents provided")

    combined = "\n\n---\n\n".join(request.page_contents)

    user_message = f"""Analyze the following Confluence documentation and generate the complete, fully-populated architecture diagram prompt following your instructions exactly.

CONFLUENCE CONTENT:
{combined}

Output ONLY the completed prompt block starting with the ==== header line. Do not add any preamble or explanation before or after it."""

    logger.info(f"[DESIGN] Generating architecture prompt v3.0 from {len(request.page_contents)} page(s)")
    prompt = _invoke_claude(ARCHITECTURE_SYSTEM_PROMPT, user_message, model_id=PROMPT_MODEL_ID, max_tokens=PROMPT_MAX_TOKENS)
    logger.info(f"[DESIGN] Prompt generated ({len(prompt)} chars)")
    return GeneratePromptResponse(prompt=prompt)


@router.post("/generate-xml", response_model=GenerateXMLResponse)
async def generate_drawio_xml(
    request: GenerateXMLRequest,
    _current_user: dict = Depends(get_current_user),
):
    """
    Take a finalised architecture prompt and generate a valid draw.io
    (mxGraphModel) XML file that can be imported directly into draw.io / diagrams.net.
    """
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt must not be empty")

    system_prompt = (
        "You are an expert software architect and draw.io diagram creator. "
        "Your task is to produce valid, importable draw.io XML (mxGraphModel format) "
        "from architecture descriptions. "
        "Output ONLY raw XML — never wrap it in markdown code fences or add any explanation."
    )

    user_message = f"""Generate a valid draw.io XML file for the following system architecture. The XML must be directly importable into draw.io (diagrams.net).

ARCHITECTURE DESCRIPTION:
{request.prompt}

Requirements:
- Root element must be <mxGraphModel> with a <root> child
- Cell id="0" is the root cell (no parent), cell id="1" is the default layer (parent="0")
- All other cells start from id="2" with sequential integers
- Use vertex="1" for shapes and edge="1" for connections
- Use rounded=1;whiteSpace=wrap;html=1; for service/component boxes
- Use shape=mxgraph.flowchart.database for databases
- Use swimlane style for group containers
- Add descriptive edge labels for data flows
- Space elements at least 80px apart; avoid overlapping
- Do NOT wrap output in ```xml or any markdown

Output ONLY the XML, starting with <mxGraphModel and ending with </mxGraphModel>."""

    logger.info("[DESIGN] Generating draw.io XML")
    raw = _invoke_claude(system_prompt, user_message)

    # Strip any accidental markdown fences
    raw = raw.replace("```xml", "").replace("```", "").strip()

    # Extract the mxGraphModel block
    match = re.search(r"<mxGraphModel[\s\S]*?</mxGraphModel>", raw)
    xml = match.group(0) if match else raw

    logger.info(f"[DESIGN] XML generated ({len(xml)} chars)")
    return GenerateXMLResponse(xml=xml)


@router.post("/generate-document", response_model=GenerateDocumentResponse)
async def generate_architecture_document(
    request: GenerateDocumentRequest,
    _current_user: dict = Depends(get_current_user),
):
    """
    Generate a professional architecture document in Markdown from the
    draw.io XML diagram and the original architecture prompt.
    """
    if not request.xml.strip():
        raise HTTPException(status_code=400, detail="XML must not be empty")

    # Prompt is optional in edit mode — fall back to a generic context
    prompt_context = request.prompt.strip() or "Generate architecture documentation for this system."

    user_message = f"""Generate a complete architecture document from the following two inputs.

--- ARCHITECTURE PROMPT (contains project name and description) ---
{prompt_context}

--- DRAW.IO XML (contains all components and connections) ---
{request.xml}

Read both inputs carefully. Extract the project name from the prompt.
Extract all components and connections from the XML (use the `value` attribute of each mxCell as the component/connection label).
Then output the full architecture document in Markdown following your instructions exactly."""

    logger.info("[DESIGN] Generating architecture document")
    document = _invoke_claude(
        DOCUMENT_SYSTEM_PROMPT,
        user_message,
        model_id=DOCUMENT_MODEL_ID,
        max_tokens=DOCUMENT_MAX_TOKENS,
    )
    logger.info(f"[DESIGN] Document generated ({len(document)} chars)")
    return GenerateDocumentResponse(document=document)


def _invoke_claude_stream(system_prompt: str, user_message: str, model_id: str, max_tokens: int):
    """Generator that yields SSE chunks from a Bedrock streaming call."""
    bedrock = _get_bedrock()
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
        "temperature": 0.5,
    }
    response = bedrock.invoke_model_with_response_stream(
        modelId=model_id,
        body=json.dumps(request_body),
        contentType="application/json",
        accept="application/json",
    )
    for event in response["body"]:
        chunk = json.loads(event["chunk"]["bytes"])
        if chunk.get("type") == "content_block_delta":
            text = chunk.get("delta", {}).get("text", "")
            if text:
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.post("/generate-prompt-stream")
async def generate_architecture_prompt_stream(
    request: GeneratePromptRequest,
    _current_user: dict = Depends(get_current_user),
):
    """Streaming version of /generate-prompt. Sends SSE chunks as Claude generates."""
    if not request.page_contents:
        raise HTTPException(status_code=400, detail="No page contents provided")

    combined = "\n\n---\n\n".join(request.page_contents)
    user_message = f"""Analyze the following Confluence documentation and generate the complete, fully-populated architecture diagram prompt following your instructions exactly.

CONFLUENCE CONTENT:
{combined}

Output ONLY the completed prompt block starting with the ==== header line. Do not add any preamble or explanation before or after it."""

    def stream():
        try:
            yield from _invoke_claude_stream(ARCHITECTURE_SYSTEM_PROMPT, user_message, PROMPT_MODEL_ID, PROMPT_MAX_TOKENS)
        except Exception as e:
            logger.error(f"[DESIGN] Prompt stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/generate-document-stream")
async def generate_architecture_document_stream(
    request: GenerateDocumentRequest,
    _current_user: dict = Depends(get_current_user),
):
    """Streaming version of /generate-document. Sends SSE chunks as Claude generates."""
    if not request.xml.strip():
        raise HTTPException(status_code=400, detail="XML must not be empty")

    prompt_context = request.prompt.strip() or "Generate architecture documentation for this system."
    user_message = f"""Generate a complete architecture document from the following two inputs.

--- ARCHITECTURE PROMPT (contains project name and description) ---
{prompt_context}

--- DRAW.IO XML (contains all components and connections) ---
{request.xml}

Read both inputs carefully. Extract the project name from the prompt.
Extract all components and connections from the XML (use the `value` attribute of each mxCell as the component/connection label).
Then output the full architecture document in Markdown following your instructions exactly."""

    def stream():
        try:
            yield from _invoke_claude_stream(DOCUMENT_SYSTEM_PROMPT, user_message, DOCUMENT_MODEL_ID, DOCUMENT_MAX_TOKENS)
        except Exception as e:
            logger.error(f"[DESIGN] Document stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/push-to-confluence", response_model=PushToConfluenceResponse)
async def push_document_to_confluence(
    request: PushToConfluenceRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Push the generated architecture document to Confluence as a new page.
    Uses the Atlassian credentials linked to the current user's account.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account in Settings first.",
        )

    if not request.document.strip():
        raise HTTPException(status_code=400, detail="Document must not be empty")

    xhtml_content = _markdown_to_confluence_xhtml(request.document)

    try:
        confluence = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        # Upsert — update if page with same title exists, otherwise create
        existing = confluence.find_page_by_title(request.space_key, request.title)
        if existing:
            page = confluence.update_page(
                page_id=existing["id"],
                title=request.title,
                content=xhtml_content,
                current_version=existing["version"]["number"],
            )
            logger.info(f"[DESIGN] Confluence document page updated: {page['id']} — {page['title']}")
        else:
            page = confluence.create_page(
                space_key=request.space_key,
                title=request.title,
                content=xhtml_content,
            )
            logger.info(f"[DESIGN] Confluence document page created: {page['id']} — {page['title']}")
        return PushToConfluenceResponse(
            page_url=page["web_url"],
            page_id=page["id"],
        )
    except Exception as e:
        logger.error(f"[DESIGN] Confluence push error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to push to Confluence: {str(e)}")


@router.post("/list-diagrams", response_model=ListDiagramsResponse)
async def list_saved_diagrams(
    request: ListDiagramsRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Return all Confluence pages whose title starts with 'Architecture Diagram'
    in the given space — these are the diagrams saved by this tool.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account in Settings first.",
        )

    try:
        confluence = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )
        pages = confluence.search_pages_by_title_prefix(request.space_key, "Architecture Diagram")
        base = f"https://{credentials['atlassian_domain']}/wiki"
        diagrams = [
            DiagramPageInfo(
                page_id=p["id"],
                title=p["title"],
                page_url=f"{base}{p.get('_links', {}).get('webui', '')}",
                last_modified=p.get("version", {}).get("when", ""),
            )
            for p in pages
        ]
        return ListDiagramsResponse(diagrams=diagrams)
    except Exception as e:
        logger.error(f"[DESIGN] List diagrams error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list diagrams: {str(e)}")


# ─── Diagram save/load helpers ────────────────────────────────────────────────

def _wrap_xml_for_confluence(xml: str) -> str:
    """Wrap draw.io XML in a Confluence code macro for safe round-trip storage."""
    # Escape CDATA end sequence if it appears inside the XML
    safe_xml = xml.replace("]]>", "]]]]><![CDATA[>")
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        '<ac:parameter ac:name="language">xml</ac:parameter>'
        '<ac:parameter ac:name="title">draw.io Architecture Diagram — do not edit manually</ac:parameter>'
        f'<ac:plain-text-body><![CDATA[{safe_xml}]]></ac:plain-text-body>'
        '</ac:structured-macro>'
    )


def _extract_xml_from_confluence(storage_html: str) -> str:
    """Extract draw.io XML from a Confluence page's storage format."""
    # Primary: CDATA block inside the code macro
    match = re.search(r'<ac:plain-text-body><!\[CDATA\[([\s\S]*?)\]\]></ac:plain-text-body>', storage_html)
    if match:
        return match.group(1).strip()
    # Fallback: bare mxGraphModel in the page body
    match = re.search(r'(<mxGraphModel[\s\S]*?</mxGraphModel>)', storage_html)
    if match:
        return match.group(1).strip()
    return ""


@router.post("/save-diagram", response_model=SaveDiagramResponse)
async def save_diagram_to_confluence(
    request: SaveDiagramRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Save (or update) the draw.io XML as a Confluence page.
    If a page with the same title already exists it is updated; otherwise a new page is created.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account in Settings first.",
        )

    if not request.xml.strip():
        raise HTTPException(status_code=400, detail="XML must not be empty")

    page_title = request.page_title.strip() or f"Architecture Diagram — {request.space_key} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    content = _wrap_xml_for_confluence(request.xml)

    try:
        confluence = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )

        existing = confluence.find_page_by_title(request.space_key, page_title)
        if existing:
            page = confluence.update_page(
                page_id=existing["id"],
                title=page_title,
                content=content,
                current_version=existing["version"]["number"],
            )
            logger.info(f"[DESIGN] Diagram page updated: {page['id']}")
        else:
            page = confluence.create_page(
                space_key=request.space_key,
                title=page_title,
                content=content,
            )
            logger.info(f"[DESIGN] Diagram page created: {page['id']}")

        return SaveDiagramResponse(
            page_url=page["web_url"],
            page_id=page["id"],
        )
    except Exception as e:
        logger.error(f"[DESIGN] Save diagram error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save diagram: {str(e)}")


@router.post("/load-diagram", response_model=LoadDiagramResponse)
async def load_diagram_from_confluence(
    request: LoadDiagramRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Load a previously saved draw.io XML diagram from Confluence.
    Returns the XML string so the frontend can push it into the draw.io editor.
    Returns 404 if no saved diagram is found.
    """
    credentials = get_user_atlassian_credentials(current_user["id"])
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account in Settings first.",
        )

    page_title = request.page_title.strip() or f"Architecture Diagram — {request.space_key}"

    try:
        confluence = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )

        # Prefer direct page_id lookup (avoids title-search issues with special chars)
        if request.page_id.strip():
            page_content = confluence.get_content_page_by_id(
                request.page_id.strip(), expand="body.storage,version,_links"
            )
            xml = _extract_xml_from_confluence(page_content.get("body", {}).get("storage", {}).get("value", ""))
            if not xml:
                raise HTTPException(status_code=422, detail="Saved diagram page found but XML could not be extracted.")
            web_url = f"https://{credentials['atlassian_domain']}/wiki{page_content.get('_links', {}).get('webui', '')}"
            logger.info(f"[DESIGN] Diagram loaded by page_id {request.page_id}")
            return LoadDiagramResponse(xml=xml, page_url=web_url, page_id=request.page_id.strip())

        existing = confluence.find_page_by_title(request.space_key, page_title)
        if not existing:
            raise HTTPException(status_code=404, detail="No saved diagram found for this project.")

        page_content = confluence.get_page_content(existing["id"])
        xml = _extract_xml_from_confluence(page_content["content"])

        if not xml:
            raise HTTPException(status_code=422, detail="Saved diagram page found but XML could not be extracted.")

        web_url = f"https://{credentials['atlassian_domain']}/wiki{existing.get('_links', {}).get('webui', '')}"
        logger.info(f"[DESIGN] Diagram loaded from Confluence page {existing['id']}")
        return LoadDiagramResponse(xml=xml, page_url=web_url, page_id=existing["id"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DESIGN] Load diagram error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load diagram: {str(e)}")
