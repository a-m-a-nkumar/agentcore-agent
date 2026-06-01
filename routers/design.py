"""
Design Architecture Router
Provides endpoints for generating architecture prompts (for Lucid Chart)
and draw.io XML diagrams directly from Confluence page content.
Calls Bedrock Claude directly — no guardrails, no BRD session required.
"""

import asyncio
import base64
import json
import os
import logging
from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from auth import verify_azure_token, require_module
from db_helper import (
    create_or_update_user,
    get_user_atlassian_credentials,
    get_user_lucid_credentials,
    get_project,
    get_design_session,
    update_design_session,
    update_diagram_slot,
)
from services.confluence_service import ConfluenceService
from services.s3_service import s3_put_object, get_s3_client
from services.lucid_api_service import (
    LucidAPIService,
    InvalidLucidKeyError,
    LucidNotAccessibleError,
    LucidUpstreamError,
    LucidError,
)
from environment import chat_completion, chat_completion_stream, S3_BUCKET_NAME

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/design", tags=["design"], dependencies=[Depends(require_module("design"))])

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
    user_id: Optional[str] = None,
) -> str:
    """Call the environment LLM synchronously and return the full text response."""
    try:
        return chat_completion(
            messages=[{"role": "user", "content": user_message}],
            model=model_id,
            temperature=0.5,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            user_id=user_id,
        )
    except Exception as e:
        logger.error(f"[DESIGN] LLM invoke error: {e}")
        raise HTTPException(status_code=502, detail=f"AI model error: {str(e)}")


# ─── Request / Response models ────────────────────────────────────────────────

class GeneratePromptRequest(BaseModel):
    page_contents: List[str]   # Plain-text content of each selected Confluence page
    # Which architectural view the prompt should target. Drives which of
    # the three system prompts (infrastructure/logical/security) we
    # dispatch to. Defaults to infrastructure for legacy compatibility —
    # the original draw.io flow only had this one prompt.
    diagram_type: str = "infrastructure"


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
    # Project-only (legacy) flow keeps these required.
    project_id: str
    space_key: str = ""        # Confluence space; optional when saving to a session-only S3 location
    # Made Optional so the SAME endpoint can handle SVG-only updates — the
    # frontend uses this to recover when its initial draw.io SVG export
    # failed (the placeholder card's "Render diagram" button posts svg only).
    # The session-aware path requires AT LEAST one of xml / svg; both being
    # absent is a 400.
    xml: Optional[str] = None  # draw.io XML
    page_title: str = ""       # Defaults to "Architecture Diagram — <space_key>"
    # Multi-session flow (new). When provided:
    #  - XML is written to s3://.../sessions/{session_id}/diagram/logical.xml
    #  - If `svg` is also provided, it's written to .../diagram/logical.svg
    #  - The design_sessions row is updated with the keys and stage transitions
    #    NEW/DIAGRAM_GATHERING → DIAGRAM_READY.
    #  - If `space_key` is also provided AND user has Atlassian linked, the XML
    #    is *also* pushed to Confluence (sharing). If not, Confluence push is skipped.
    session_id: Optional[str] = None
    svg: Optional[str] = None  # rendered SVG from the draw.io iframe (xmlsvg export)
    # Rendered PNG from the draw.io iframe (canvas-rasterise export). Sent
    # as a `data:image/png;base64,...` URL string. Used by the DOCX export
    # path for native python-docx embedding (no cairosvg conversion needed).
    # PNG export is more reliable than SVG export when icon CDNs are slow
    # or blocked in the user's environment.
    png: Optional[str] = None
    # SAD-redesign: which slot this diagram fills. One of
    # "logical" | "infrastructure" | "security". Defaults to "logical" so
    # any legacy caller continues hitting the existing single-slot path.
    diagram_type: Optional[str] = None  # validated server-side


class SaveDiagramResponse(BaseModel):
    page_url: Optional[str] = None    # Confluence URL — only when pushed to Confluence
    page_id: Optional[str] = None     # Confluence page id — only when pushed to Confluence
    diagram_s3_key: Optional[str] = None
    diagram_svg_s3_key: Optional[str] = None
    session_stage: Optional[str] = None  # echoes the new stage on the design_sessions row
    # Per-type slot snapshot after the save — frontend reads this back so the
    # hub UI updates in lockstep with the server state.
    diagram_slot: Optional[Dict[str, Any]] = None


class LoadDiagramRequest(BaseModel):
    project_id: str
    space_key: str = ""
    page_title: str = ""       # Must match the title used when saving
    page_id: str = ""          # If provided, load directly by Confluence page id
    # Multi-session flow (new). When provided, S3 is the primary source.
    session_id: Optional[str] = None
    # Per-type slot identifier. When provided, the server reads
    # sessions/{id}/diagram/{type}.xml. Defaults to "logical" for legacy
    # callers (the SAD generator's section 4 path).
    diagram_type: Optional[str] = None


class LoadDiagramResponse(BaseModel):
    xml: str
    page_url: str = ""           # populated only when loaded from Confluence
    page_id: str = ""            # populated only when loaded from Confluence
    diagram_s3_key: Optional[str] = None
    diagram_svg_s3_key: Optional[str] = None
    source: str = "confluence"   # "s3" | "confluence" — tells the frontend where the XML came from



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


# ─── Per-view system prompts for the draw.io flow ─────────────────────────────
#
# The default `ARCHITECTURE_SYSTEM_PROMPT` above is INFRASTRUCTURE-flavoured
# (AWS icons, networking layers, deployment topology). For parity with the
# Lucid flow we offer two siblings — LOGICAL and SECURITY — that produce
# the same overall scaffolding (NAME / COMPONENTS / CONNECTIONS / DIAGRAM
# directives) so the XML generator behaves the same downstream, but tuned
# to a different concern.

LOGICAL_SYSTEM_PROMPT = """
You are a senior solutions architect. You will be given content from one or more
Confluence pages describing a software project. Read everything carefully, then output
a single fully-populated prompt that any AI tool can use to generate a professional,
visually-rich, draw.io-compatible XML LOGICAL architecture diagram.

A LOGICAL diagram shows WHAT the system does and HOW data flows between capabilities.
It is vendor-agnostic — no cloud provider names, no server types, no infrastructure
deployment detail. But it is NOT plain. Every capability gets a recognisable shape
(person, gear, database, queue, document, cloud) so the diagram reads at a glance.

═══════════════════════════════════════════════════════════════
STEP 1 — UNDERSTAND WHAT TO EXTRACT
═══════════════════════════════════════════════════════════════

Read the document and identify:
  - Project name and what the system does (2–3 sentences)
  - Actors / users / external systems on the periphery
  - Core capabilities — focus on the most important ~8 to 14 (more is noise)
  - The verb-phrase data flows between them
  - Which logical layer each capability belongs to

Target distribution per layer (don't force every layer to fill if irrelevant):
  Actors / Users           → 1 to 3
  Presentation             → 1 to 3
  API / Mediation          → 1 to 2
  Business Logic           → 3 to 6
  Data / Persistence       → 1 to 3
  Messaging / Events       → 0 to 2
  External Systems         → 0 to 3

═══════════════════════════════════════════════════════════════
STEP 2 — VENDOR-AGNOSTIC SHAPE VOCABULARY (mandatory)
═══════════════════════════════════════════════════════════════

Each capability MUST use a shape that conveys its role at a glance.
NEVER use AWS / Azure / GCP / cloud-provider icons — those belong in the
Infrastructure view.

ONLY use shapes from this whitelist. They are draw.io's built-in shapes
plus the flowchart and EIP libraries that are guaranteed to render in the
embed (https://embed.diagrams.net). Other libraries (bootstrap, gmdl,
cisco_safe, networking, mxgraph.aws4, mscae) are NOT allowed — they fail
to serialise on save in the embedded editor and corrupt the diagram.

ROLE-TO-SHAPE TABLE (copy the `style=` string verbatim):

  Actor / End User      →  shape=umlActor;
  External System       →  shape=cloud;
  Web / Mobile UI       →  shape=mxgraph.flowchart.display;
  API / Gateway         →  shape=hexagon;perimeter=hexagonPerimeter2;
  Service / Capability  →  rounded=1;whiteSpace=wrap;html=1;
  Business Engine       →  shape=mxgraph.flowchart.predefined_process;
  Authentication        →  shape=mxgraph.flowchart.protected_storage;
  Database / Store      →  shape=cylinder3;boundedLbl=1;backgroundOutline=1;
  Cache                 →  shape=mxgraph.flowchart.stored_data;
  Queue / Event Bus     →  shape=mxgraph.eip.message_channel;
  Topic / Pub-Sub       →  shape=mxgraph.eip.publish_subscribe_channel;
  Document / Report     →  shape=mxgraph.flowchart.document;
  Workflow / Process    →  shape=mxgraph.flowchart.predefined_process;
  Notification          →  shape=mxgraph.flowchart.display;
  Search / Query        →  shape=mxgraph.flowchart.manual_input;
  File / Storage        →  shape=mxgraph.flowchart.stored_data;
  Decision / Router     →  shape=rhombus;

If a capability doesn't cleanly match a row above, use the rounded
rectangle (Service / Capability default — `rounded=1;whiteSpace=wrap;html=1;`).
DO NOT invent shapes outside this table. DO NOT use any `mxgraph.bootstrap`,
`mxgraph.gmdl`, `mxgraph.cisco_safe`, `mxgraph.aws4`, `mxgraph.azure`,
`mxgraph.gcp2`, `mscae`, or `mxgraph.networking` shapes — they break the
embedded editor's save flow with a serialisation error.

Each component cell follows this exact XML form:

  <mxCell id="auth_svc" value="Auth Gateway"
    style="<style from table>;
           fillColor=<layer fill>;strokeColor=#1F2937;
           fontColor=#FFFFFF;fontSize=12;fontStyle=1;
           labelPosition=center;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
    vertex="1" parent="1">
    <mxGeometry x="..." y="..." width="64" height="64" as="geometry"/>
  </mxCell>

═══════════════════════════════════════════════════════════════
STEP 3 — LOGICAL LAYER PALETTE (vendor-agnostic, mandatory)
═══════════════════════════════════════════════════════════════

Each capability is coloured by which LAYER it belongs to. White text on the
dark fills, navy text on the light layers.

  Actors / Users         →  fillColor=#374151  fontColor=#FFFFFF   (graphite — outside the system boundary)
  Presentation           →  fillColor=#6B7BFF  fontColor=#FFFFFF   (cool blue — what users see)
  API / Mediation        →  fillColor=#FFB347  fontColor=#1F2937   (warm orange — request gateway)
  Business Logic         →  fillColor=#FF6B6B  fontColor=#FFFFFF   (coral — the system's verbs)
  Data / Persistence     →  fillColor=#4CAF50  fontColor=#FFFFFF   (forest — the system's nouns)
  Messaging / Events     →  fillColor=#9C27B0  fontColor=#FFFFFF   (violet — async)
  External Systems       →  fillColor=#757575  fontColor=#FFFFFF   (slate — outside the boundary)

LAYER CONTAINER COLOURS (swimlane backgrounds, hairline strokes — not loud):
  Presentation container  →  fill=#EEF1FF  stroke=#6B7BFF
  API container           →  fill=#FFF7EE  stroke=#FFB347
  Business container      →  fill=#FFEFEF  stroke=#FF6B6B
  Data container          →  fill=#EBF7EE  stroke=#4CAF50
  Messaging container     →  fill=#F5EEF8  stroke=#9C27B0
  External container      →  fill=#F5F5F5  stroke=#9CA3AF

CONTAINER STYLE STRING:
  shape=swimlane;swimlaneFillColor=<container fill>;fillColor=<container fill>;
  strokeColor=<container stroke>;rounded=1;startSize=24;
  fontStyle=1;fontSize=11;fontColor=#1F2937;
  whiteSpace=wrap;html=1;horizontal=0;

═══════════════════════════════════════════════════════════════
STEP 4 — CANVAS, SPACING, AND EDGE RULES
═══════════════════════════════════════════════════════════════

  Canvas              →  1600 × 1000
  Component size      →  64 × 64 (icons) — labels sit BELOW the icon, never inside
  Container padding   →  20px around children; 24px header for the layer name
  Horizontal spacing  →  ≥ 140px between component centres
  Vertical spacing    →  ≥ 110px between rows within a container
  Layer order (left → right)  →  Actors → Presentation → API → Business → Data → External
  Layer order (top → bottom)  →  Messaging container spans the bottom under Business + Data

Edges (data flows):
  - Always orthogonal: edgeStyle=orthogonalEdgeStyle;rounded=0;curved=0;
  - Stroke #1F2937, strokeWidth=1.5
  - LABEL is the exact verb phrase from CONNECTIONS — never generic
    ("uses", "calls", "talks to" are FORBIDDEN). Use specifics:
    "authenticates", "queries", "publishes", "streams", "subscribes to",
    "validates", "issues", "fetches", "writes", "consumes".
  - Label rendering: edgeLabel;html=1;background=#FFFFFFCC;align=center;
    fontSize=10;fontColor=#1F2937;
  - Mark async/event flows with dashed stroke (dashed=1) and a different
    arrow style (endArrow=open) so they're visually distinct from sync calls.

═══════════════════════════════════════════════════════════════
STEP 5 — OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Output a single block in exactly this shape (no markdown fences, no preamble):

==== LOGICAL ARCHITECTURE PROMPT ====
Name: <Project Name> – Logical Architecture
Description: <2–3 sentence plain-English description of what the system does and who uses it>

LAYERS
  Actors        – outside the system boundary; people or peer systems
  Presentation  – user-facing surfaces (web, mobile, embedded SDK)
  API           – request mediation and protocol translation
  Business      – domain logic, the system's verbs
  Data          – stores, caches, search indexes
  Messaging     – async pipes, event buses, topics
  External      – third-party services the system depends on

COMPONENTS
  | id | layer | label | role | shape |
  |----|-------|-------|------|-------|
  <one row per capability — aim for 8 to 14. Keep labels 2–4 words like
   "Auth Gateway", "Order Engine", "Notification Service". Pick `role`
   and `shape` from the STEP 2 table.>

CONTAINERS
  | layer        | components            |
  |--------------|-----------------------|
  | Actors       | <ids of actor rows>   |
  | Presentation | <ids of UI rows>      |
  | API          | <ids of API rows>     |
  | Business     | <ids of business rows>|
  | Data         | <ids of data rows>    |
  | Messaging    | <ids of msg rows>     |
  | External     | <ids of external rows>|

CONNECTIONS
  <source_id> → <target_id> : <verb phrase> [sync|async]
  <one per meaningful interaction; aim for 10 to 18>

DIAGRAM DIRECTIVES
  - Use the role-to-shape mapping from STEP 2 verbatim. Every component
    gets an icon, never a plain rectangle (unless the role is generic
    Service / Capability).
  - Fill colour comes from the LAYER PALETTE in STEP 3. Container
    backgrounds come from the same palette's "container" colours.
  - Group components by layer using swimlane containers — Actors and
    External float free outside the system boundary; the other five
    sit inside an enclosing rounded box titled with the project name.
  - Edge labels MUST be the verb phrase from CONNECTIONS — never
    generic verbs ("uses", "calls", "talks to" are forbidden).
  - Async / event flows render with dashed strokes (dashed=1) and an
    open arrowhead. Sync calls are solid with a filled arrowhead.
  - Canvas 1600×1000, component size 64×64, h-spacing ≥ 140, v-spacing ≥ 110.
  - DO NOT use any AWS / Azure / GCP icon shapes. This is the logical
    view; vendor branding belongs in the Infrastructure diagram.

═══════════════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════════════
- Output ONLY the populated prompt block — no preamble, no markdown fences,
  no explanation.
- Keep capability names short (2–4 words). No vendor names ("AWS Lambda",
  "Stripe API" → wrong; "Order Service", "Payments Gateway" → right).
- Verb labels on connections must be specific: "authenticates", "queries",
  "publishes", "streams", "validates", "issues" — not "uses" or "calls".
- If a detail is not in the documents, infer what a senior architect would
  include and append (inferred).
- Aim for 8 to 14 capabilities and 10 to 18 data flows. Density beyond that
  hurts readability.
- The diagram must read at a glance: layer colour + recognisable icon should
  tell you what the component IS without reading the label.
"""


SECURITY_SYSTEM_PROMPT = """
You are a senior security architect. You will be given content from one or more
Confluence pages describing a software project. Read everything carefully, then output
a single fully-populated prompt that any AI tool can use to generate a professional,
visually-rich, draw.io-compatible XML SECURITY architecture diagram.

A SECURITY diagram shows trust boundaries, WHO accesses WHAT, WHERE controls sit,
and HOW data is protected. It is organised by trust zones, not deployment layers.
Every actor and control gets a recognisable shape (person, gateway, lock, key,
audit log, gear) so the diagram reads at a glance.

═══════════════════════════════════════════════════════════════
STEP 1 — UNDERSTAND WHAT TO EXTRACT
═══════════════════════════════════════════════════════════════

Read the document and identify:
  - Project name, data sensitivity, and compliance context (2–3 sentences)
  - The actors / clients who access the system from each trust zone
  - The components that handle data — services, stores, secret managers
  - The auth mechanisms protecting each cross-zone interaction
  - The security controls (WAF, MFA, TLS, RBAC, encryption, audit) and what they enforce

Target distribution per zone (don't force a zone if it's irrelevant):
  Internet / Untrusted     → 1 to 3 actors
  DMZ / Low Trust          → 1 to 3 components (gateway, WAF, edge controls)
  Application / Medium     → 3 to 6 services
  Data / High Trust        → 1 to 3 stores + secret manager
  Admin / Privileged       → 1 to 2 (admin console, ops jump host)
  CI/CD / Privileged       → 0 to 2 (pipelines, artifact store)

═══════════════════════════════════════════════════════════════
STEP 2 — VENDOR-AGNOSTIC SHAPE VOCABULARY (mandatory)
═══════════════════════════════════════════════════════════════

Each component MUST use a shape that conveys its security role at a glance.
ONLY use shapes from this whitelist. They are draw.io's built-in shapes plus
the flowchart and EIP libraries that render reliably in the embed
(https://embed.diagrams.net). Other libraries (bootstrap, gmdl, cisco_safe,
networking, mxgraph.aws4, mscae) are NOT allowed — they fail to serialise on
save in the embedded editor and corrupt the diagram with "u.substring is
not a function" errors.

ROLE-TO-SHAPE TABLE (copy the `style=` string verbatim):

  External User / Client    →  shape=umlActor;
  External System           →  shape=cloud;
  WAF / Firewall            →  shape=mxgraph.flowchart.protected_storage;
  API Gateway / Edge        →  shape=hexagon;perimeter=hexagonPerimeter2;
  Auth Server / IdP         →  shape=mxgraph.flowchart.protected_storage;
  Service / Microservice    →  rounded=1;whiteSpace=wrap;html=1;
  Database / Store          →  shape=cylinder3;boundedLbl=1;backgroundOutline=1;
  Cache                     →  shape=mxgraph.flowchart.stored_data;
  Secret Store / Vault      →  shape=mxgraph.flowchart.protected_storage;
  Audit Log / SIEM          →  shape=mxgraph.flowchart.document;
  Message Queue / Bus       →  shape=mxgraph.eip.message_channel;
  Admin Console             →  shape=mxgraph.flowchart.display;
  CI/CD Pipeline            →  shape=mxgraph.flowchart.predefined_process;
  Decision / Policy Engine  →  shape=rhombus;
  Manual Input / Form       →  shape=mxgraph.flowchart.manual_input;

If a component doesn't cleanly match a row above, use the rounded
rectangle (Service / Microservice default — `rounded=1;whiteSpace=wrap;html=1;`).
DO NOT invent shapes outside this table. DO NOT use any `mxgraph.bootstrap`,
`mxgraph.gmdl`, `mxgraph.cisco_safe`, `mxgraph.aws4`, `mxgraph.azure`,
`mxgraph.gcp2`, `mscae`, or `mxgraph.networking` shapes.

Each component cell follows this exact XML form:

  <mxCell id="auth_idp" value="Auth IdP"
    style="<style from table>;
           fillColor=<zone fill stroke>;strokeColor=<zone stroke darker>;
           fontColor=#FFFFFF;fontSize=12;fontStyle=1;
           labelPosition=center;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
    vertex="1" parent="<zone container id>">
    <mxGeometry x="..." y="..." width="64" height="64" as="geometry"/>
  </mxCell>

═══════════════════════════════════════════════════════════════
STEP 3 — TRUST ZONE PALETTE (mandatory)
═══════════════════════════════════════════════════════════════

Each component is coloured by which TRUST ZONE it lives in. White text on
component fills, dark navy on container backgrounds. Containers use the
lighter pale tone for their background and the saturated stroke for their
border, making the trust boundary visually obvious.

  Zone               Trust       Component fill   Container fill   Container stroke
  ----               -----       --------------   --------------   ----------------
  Internet           Untrusted   #C62828          #FFEBEE          #C62828
  DMZ                Low         #E65100          #FFF3E0          #E65100
  Application        Medium      #2E7D32          #E8F5E9          #2E7D32
  Data               High        #1565C0          #E3F2FD          #1565C0
  Admin              High        #6A1B9A          #F3E5F5          #6A1B9A
  CI/CD              Privileged  #455A64          #ECEFF1          #455A64

Zone container style string:

  shape=swimlane;swimlaneFillColor=<container fill>;fillColor=<container fill>;
  strokeColor=<container stroke>;strokeWidth=2;rounded=1;startSize=24;
  fontStyle=1;fontSize=11;fontColor=#1F2937;
  whiteSpace=wrap;html=1;horizontal=0;dashed=0;

═══════════════════════════════════════════════════════════════
STEP 4 — ACCESS FLOWS, CONTROLS, AND CANVAS
═══════════════════════════════════════════════════════════════

Canvas              →  1600 × 1000
Component size      →  64 × 64 (icons) — labels sit BELOW the icon, never inside
Container padding   →  20px around children; 28px header for the zone title
Horizontal spacing  →  ≥ 140px between component centres
Zone order (left → right)  →  Internet → DMZ → Application → Data
                              (Admin and CI/CD float ABOVE Application as separate boxes)

Edges (access flows):
  - Always orthogonal: edgeStyle=orthogonalEdgeStyle;rounded=0;curved=0;
  - Stroke #1F2937, strokeWidth=1.5
  - LABEL is the auth mechanism + scope from ACCESS FLOWS — never generic
    ("uses", "accesses", "talks to" are FORBIDDEN). Use specifics:
    "OAuth 2.0 + MFA", "mTLS", "API key", "JWT", "service account",
    "SSH + bastion", "private link", "SAML SSO".
  - Label rendering: edgeLabel;html=1;background=#FFFFFFCC;align=center;
    fontSize=10;fontColor=#1F2937;rounded=1;
  - Cross-zone flows are drawn DASHED with strokeColor=#C62828 (the
    Internet stroke) so trust-boundary crossings are visually loud.
  - Same-zone flows are SOLID with strokeColor=#9CA3AF (muted graphite).

Security control callouts:
  Each enforced control renders as a SMALL red rhombus (32×32) attached
  near the boundary it protects, labelled with the control type
  (e.g. "WAF", "MFA", "RBAC"). Style:
    shape=rhombus;fillColor=#C62828;strokeColor=#7F0000;fontColor=#FFFFFF;
    fontSize=9;fontStyle=1;html=1;
  Wire each callout to its target component with a thin dashed line:
    edgeStyle=none;dashed=1;strokeColor=#C62828;strokeWidth=1;endArrow=none;

═══════════════════════════════════════════════════════════════
STEP 5 — OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Output a single block in exactly this shape (no markdown fences, no preamble):

==== SECURITY ARCHITECTURE PROMPT ====
Name: <Project Name> – Security Architecture
Description: <2–3 sentences on the system, data sensitivity, and compliance context>
Compliance scope: <GDPR | HIPAA | SOC 2 | ISO 27001 | PCI-DSS — or "None identified">

TRUST ZONES
  | zone_id | name        | trust       |
  |---------|-------------|-------------|
  <pick 4–6 from STEP 3 table; reference each by zone_id below>

COMPONENTS
  | id | zone_id | type | label | role | shape |
  |----|---------|------|-------|------|------|-------|
  <one row per actor / control / data store; aim for 10–18.
   `type` is the human-readable role (User, WAF, Auth Server, Database, etc.).
   `shape` is picked from STEP 2 table.>

ACCESS FLOWS
  <source_id> → <target_id> : <auth mechanism + scope> [cross-zone|same-zone]
  <one per meaningful interaction; aim for 8–15>

SECURITY CONTROLS
  | control_id | type | enforces                                | attached_to |
  |------------|------|-----------------------------------------|-------------|
  <one row per control. Type ∈ {WAF, MFA, TLS, RBAC, Encryption-at-Rest,
   Rate-Limiting, SIEM, Audit-Logging, Secret-Rotation, Network-Policy}.
   `attached_to` is a component_id or boundary descriptor.>

DIAGRAM DIRECTIVES
  - Use the role-to-shape mapping from STEP 2 verbatim. Every component
    gets an icon, never a plain rectangle (unless the role is generic
    Service / Microservice).
  - Wrap each trust zone in a swimlane container using the fill / stroke
    from STEP 3. Containers visually separate the zones — the boundary
    IS the diagram's main signal.
  - Component fill colour comes from the TRUST ZONE the component lives in
    (the saturated stroke colour, not the pale container fill).
  - Cross-zone access flows render dashed with crimson stroke (#C62828) and
    a labelled auth mechanism. Same-zone flows render solid muted grey.
  - Security controls render as small red rhombus callouts attached to
    the component or boundary they protect, with the control type as label.
  - Canvas 1600×1000, component size 64×64, h-spacing ≥ 140, v-spacing ≥ 110.
  - DO NOT use any AWS / Azure / GCP / cisco_safe / bootstrap icon shapes —
    only the whitelist in STEP 2.

═══════════════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════════════
- Output ONLY the populated prompt block — no preamble, no markdown fences,
  no explanation.
- Every ACCESS FLOW must specify the auth mechanism + scope. Internal calls
  within the same zone are still access flows but render with the muted
  stroke; cross-zone flows render crimson dashed.
- Control names must be specific: "Azure AD MFA" not just "MFA";
  "AES-256 at rest" not just "encryption".
- If a detail is not in the documents, infer what a senior security
  architect would include and append (inferred).
- Aim for 10–18 components, 4–6 zones, 8–15 access flows, 5–10 controls.
- The diagram must read at a glance: the trust-zone container colour +
  the recognisable component icon should tell you what the component IS
  and where it sits without reading the label.
"""


def _get_drawio_system_prompt(diagram_type: str) -> str:
    """Pick the system prompt for the draw.io flow based on diagram type."""
    if diagram_type == "logical":
        return LOGICAL_SYSTEM_PROMPT
    if diagram_type == "security":
        return SECURITY_SYSTEM_PROMPT
    return ARCHITECTURE_SYSTEM_PROMPT


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
    current_user: dict = Depends(get_current_user),
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

    system_prompt = _get_drawio_system_prompt(request.diagram_type)
    logger.info(
        f"[DESIGN] Generating draw.io {request.diagram_type} prompt from "
        f"{len(request.page_contents)} page(s)"
    )
    prompt = _invoke_claude(
        system_prompt, user_message,
        model_id=PROMPT_MODEL_ID, max_tokens=PROMPT_MAX_TOKENS,
        user_id=current_user.get("id"),
    )
    logger.info(f"[DESIGN] Prompt generated ({len(prompt)} chars)")
    return GeneratePromptResponse(prompt=prompt)


@router.post("/generate-xml", response_model=GenerateXMLResponse)
async def generate_drawio_xml(
    request: GenerateXMLRequest,
    current_user: dict = Depends(get_current_user),
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
    raw = _invoke_claude(system_prompt, user_message, user_id=current_user.get("id"))

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
    current_user: dict = Depends(get_current_user),
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
        user_id=current_user.get("id"),
    )
    logger.info(f"[DESIGN] Document generated ({len(document)} chars)")
    return GenerateDocumentResponse(document=document)


def _invoke_claude_stream(
    system_prompt: str,
    user_message: str,
    model_id: str,
    max_tokens: int,
    user_id: Optional[str] = None,
    token_source: Optional[str] = None,
):
    """Generator that yields SSE chunks from the environment LLM streaming call."""
    yield from chat_completion_stream(
        messages=[{"role": "user", "content": user_message}],
        model=model_id,
        temperature=0.5,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        user_id=user_id,
        token_source=token_source,
    )


@router.post("/generate-prompt-stream")
async def generate_architecture_prompt_stream(
    request: GeneratePromptRequest,
    current_user: dict = Depends(get_current_user),
):
    """Streaming version of /generate-prompt. Sends SSE chunks as Claude generates."""
    if not request.page_contents:
        raise HTTPException(status_code=400, detail="No page contents provided")

    combined = "\n\n---\n\n".join(request.page_contents)
    user_message = f"""Analyze the following Confluence documentation and generate the complete, fully-populated architecture diagram prompt following your instructions exactly.

CONFLUENCE CONTENT:
{combined}

Output ONLY the completed prompt block starting with the ==== header line. Do not add any preamble or explanation before or after it."""

    system_prompt = _get_drawio_system_prompt(request.diagram_type)
    logger.info(
        f"[DESIGN] Streaming draw.io {request.diagram_type} prompt from "
        f"{len(request.page_contents)} page(s)"
    )

    def stream():
        try:
            yield from _invoke_claude_stream(
                system_prompt, user_message, PROMPT_MODEL_ID, PROMPT_MAX_TOKENS,
                user_id=current_user.get("id"),
                token_source="routers/design:generate_architecture_prompt_stream",
            )
        except Exception as e:
            logger.error(f"[DESIGN] Prompt stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/generate-document-stream")
async def generate_architecture_document_stream(
    request: GenerateDocumentRequest,
    current_user: dict = Depends(get_current_user),
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
            yield from _invoke_claude_stream(
                DOCUMENT_SYSTEM_PROMPT, user_message, DOCUMENT_MODEL_ID, DOCUMENT_MAX_TOKENS,
                user_id=current_user.get("id"),
                token_source="routers/design:generate_architecture_document_stream",
            )
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
async def save_diagram(
    request: SaveDiagramRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Save the draw.io XML (and optionally a rendered SVG) for a project.

    Two routes:
      • session-aware (new): if `session_id` is provided, the XML and SVG are
        written to S3 under the session's prefix, the design_sessions row is
        updated, and a Confluence push is attempted only as an optional copy
        when `space_key` + Atlassian credentials are present.
      • legacy: if `session_id` is omitted, the previous Confluence-only flow
        is preserved (backward compatibility for any caller still on the
        single-diagram-per-project model).
    """
    if not request.xml.strip():
        raise HTTPException(status_code=400, detail="XML must not be empty")

    user_id = current_user["id"]

    # ── Session-aware path ───────────────────────────────────────────────
    if request.session_id:
        session = get_design_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't have access to this session")
        if session["project_id"] != request.project_id:
            raise HTTPException(status_code=400, detail="session_id does not belong to project_id")

        # Resolve the diagram type. Defaults to "logical" for legacy callers
        # (the previous single-slot model). The redesign hub passes one of
        # the three explicit values per save.
        diagram_type = (request.diagram_type or "logical").lower()
        if diagram_type not in ("logical", "infrastructure", "security"):
            raise HTTPException(
                status_code=400,
                detail="diagram_type must be one of: logical, infrastructure, security",
            )

        xml_provided = bool(request.xml and request.xml.strip())
        svg_provided = bool(request.svg and request.svg.strip())
        png_provided = bool(request.png and request.png.strip())
        if not xml_provided and not svg_provided and not png_provided:
            raise HTTPException(
                status_code=400,
                detail="At least one of `xml`, `svg`, or `png` is required for a session save",
            )

        # Per-type S3 keys. When `diagram_type == "logical"` the keys match
        # the legacy locations exactly so existing readers (DOCX export,
        # the SAD orchestrator's pre-redesign codepath) keep working.
        xml_key: Optional[str] = (
            f"sessions/{request.session_id}/diagram/{diagram_type}.xml" if xml_provided else None
        )
        svg_key: Optional[str] = None
        png_key: Optional[str] = None
        try:
            if xml_provided:
                s3_put_object(key=xml_key, body=request.xml.encode("utf-8"), content_type="application/xml")
            if svg_provided:
                svg_key = f"sessions/{request.session_id}/diagram/{diagram_type}.svg"
                s3_put_object(key=svg_key, body=request.svg.encode("utf-8"), content_type="image/svg+xml")
            if png_provided:
                # Strip the data-URL prefix and decode the base64 to raw PNG bytes.
                png_url = request.png.strip()
                if png_url.startswith("data:image/png;base64,"):
                    png_b64 = png_url.split(",", 1)[1]
                else:
                    png_b64 = png_url  # Tolerate raw base64 without the prefix.
                png_bytes = base64.b64decode(png_b64)
                png_key = f"sessions/{request.session_id}/diagram/{diagram_type}.png"
                s3_put_object(key=png_key, body=png_bytes, content_type="image/png")
        except Exception as e:
            logger.error(f"[DESIGN] S3 put for diagram failed (session {request.session_id}, type {diagram_type}): {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save diagram to S3: {e}")

        page_url: Optional[str] = None
        page_id: Optional[str] = None
        # Optional: also push to Confluence (sharing). Skipped silently if user
        # hasn't linked Atlassian, no space_key was supplied, or no XML was
        # in this request (svg-only update from the SAD-page recovery flow).
        if request.space_key and xml_provided:
            credentials = get_user_atlassian_credentials(user_id)
            if credentials and credentials.get("atlassian_api_token"):
                try:
                    page_url, page_id = _push_xml_to_confluence(
                        credentials, request.space_key, request.page_title, request.xml,
                    )
                except Exception as e:
                    # Don't fail the save just because Confluence is unhappy.
                    logger.warning(f"[DESIGN] Confluence push failed (non-fatal, session save succeeded): {e}")

        # Update the per-type slot in JSONB.
        from db_helper import update_diagram_slot
        slot_patch: Dict[str, Any] = {
            "status": "done",
            "saved_at": int(datetime.now().timestamp()),
        }
        # Prefer the SVG key as the artifact pointer (what the SAD generator
        # embeds); fall back to XML if SVG wasn't in this request.
        if svg_key:
            slot_patch["artifact_key"] = svg_key
        elif xml_key:
            slot_patch["artifact_key"] = xml_key
        try:
            slot = update_diagram_slot(request.session_id, diagram_type, slot_patch)
        except Exception as e:
            logger.warning(f"[DESIGN] Failed to update diagram_slots for {request.session_id}.{diagram_type}: {e}")
            slot = None

        # For backward compat: when saving the LOGICAL slot, also bump the
        # legacy single-slot columns so anything that hasn't been migrated
        # yet (e.g. DOCX export reading session.diagram_svg_s3_key) keeps
        # working. Other types only update the JSONB slot.
        legacy_xml_key: Optional[str] = session.get("diagram_s3_key")
        legacy_svg_key: Optional[str] = session.get("diagram_svg_s3_key")
        if diagram_type == "logical":
            legacy_xml_key = xml_key or legacy_xml_key
            legacy_svg_key = svg_key or legacy_svg_key

        # Bump session: record legacy S3 keys (logical only) + stage.
        new_stage = session["stage"] if session["stage"] in ("SAD_GATHERING", "SAD_GENERATING", "SAD_REFINING") else "DIAGRAM_READY"
        updated = update_design_session(
            session_id=request.session_id,
            diagram_s3_key=legacy_xml_key,
            diagram_svg_s3_key=legacy_svg_key,
            stage=new_stage,
            confluence_page_id=page_id,
        )
        return SaveDiagramResponse(
            page_url=page_url,
            page_id=page_id,
            diagram_s3_key=xml_key or legacy_xml_key,
            diagram_svg_s3_key=svg_key or legacy_svg_key,
            session_stage=updated["stage"],
            diagram_slot=slot,
        )

    # ── Legacy Confluence-only path (preserved for old callers) ────────────
    if not request.xml or not request.xml.strip():
        raise HTTPException(status_code=400, detail="xml is required for the legacy Confluence-only save path")
    credentials = get_user_atlassian_credentials(user_id)
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account in Settings first.",
        )
    if not request.space_key:
        raise HTTPException(status_code=400, detail="space_key is required when session_id is not provided")

    try:
        page_url, page_id = _push_xml_to_confluence(
            credentials, request.space_key, request.page_title, request.xml,
        )
        return SaveDiagramResponse(page_url=page_url, page_id=page_id)
    except Exception as e:
        logger.error(f"[DESIGN] Save diagram error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save diagram: {str(e)}")


def _push_xml_to_confluence(
    credentials: dict,
    space_key: str,
    page_title: str,
    xml: str,
) -> tuple[str, str]:
    """Create or update a Confluence page holding the draw.io XML. Returns (web_url, page_id)."""
    title = (page_title or "").strip() or f"Architecture Diagram — {space_key} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    content = _wrap_xml_for_confluence(xml)
    confluence = ConfluenceService(
        credentials["atlassian_domain"],
        credentials["atlassian_email"],
        credentials["atlassian_api_token"],
    )
    existing = confluence.find_page_by_title(space_key, title)
    if existing:
        page = confluence.update_page(
            page_id=existing["id"],
            title=title,
            content=content,
            current_version=existing["version"]["number"],
        )
        logger.info(f"[DESIGN] Diagram page updated: {page['id']}")
    else:
        page = confluence.create_page(space_key=space_key, title=title, content=content)
        logger.info(f"[DESIGN] Diagram page created: {page['id']}")
    return page["web_url"], page["id"]


@router.post("/load-diagram", response_model=LoadDiagramResponse)
async def load_diagram(
    request: LoadDiagramRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Load a saved draw.io XML diagram.

    Resolution order:
      1. If `session_id` is provided and the session has `diagram_s3_key`,
         load the XML from S3 (primary source for the new flow).
      2. Otherwise (or if S3 read fails), fall back to Confluence using
         `page_id` if given, else by title within `space_key`.
    """
    user_id = current_user["id"]

    # ── 1. Session-aware path ───────────────────────────────────────────
    if request.session_id:
        session = get_design_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't have access to this session")

        # Per-type lookup. Default to "logical" so legacy callers behave as before.
        diagram_type = (request.diagram_type or "logical").lower()
        if diagram_type not in ("logical", "infrastructure", "security"):
            raise HTTPException(
                status_code=400,
                detail="diagram_type must be one of: logical, infrastructure, security",
            )

        # Try the per-type S3 key first (where the redesign saves), then
        # fall back to the legacy single-slot keys for the logical type
        # (where pre-redesign saves went).
        per_type_xml_key = f"sessions/{request.session_id}/diagram/{diagram_type}.xml"
        per_type_svg_key = f"sessions/{request.session_id}/diagram/{diagram_type}.svg"
        s3 = get_s3_client()

        try:
            obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=per_type_xml_key)
            xml = obj["Body"].read().decode("utf-8")
            logger.info(f"[DESIGN] {diagram_type} diagram loaded from S3 for session {request.session_id}")
            return LoadDiagramResponse(
                xml=xml,
                diagram_s3_key=per_type_xml_key,
                diagram_svg_s3_key=per_type_svg_key,
                source="s3",
            )
        except Exception as e:
            logger.info(
                f"[DESIGN] No per-type {diagram_type} diagram for session {request.session_id} "
                f"({e!r}) — checking legacy single-slot keys"
            )

        # Legacy single-slot fallback: only meaningful for the logical
        # type (which is what the pre-redesign codepath was implicitly
        # writing to anyway).
        if diagram_type == "logical":
            xml_key = session.get("diagram_s3_key")
            svg_key = session.get("diagram_svg_s3_key")
            if xml_key:
                try:
                    obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=xml_key)
                    xml = obj["Body"].read().decode("utf-8")
                    logger.info(f"[DESIGN] Legacy diagram loaded from S3 for session {request.session_id}")
                    return LoadDiagramResponse(
                        xml=xml,
                        diagram_s3_key=xml_key,
                        diagram_svg_s3_key=svg_key,
                        source="s3",
                    )
                except Exception as e:
                    logger.warning(
                        f"[DESIGN] Legacy S3 diagram read failed for session {request.session_id}: "
                        f"{e} — returning 404 (caller should treat as unsaved)"
                    )

        # Session-aware path: nothing in S3 for this type. Return 404 directly
        # rather than falling through to the Confluence fallback — that fallback
        # requires `space_key`/`page_id`, which the redesigned hub doesn't pass,
        # and would otherwise produce a misleading 400. The new flow stores
        # diagrams in S3 only; Confluence push is opt-in and informational.
        raise HTTPException(
            status_code=404,
            detail=f"No {diagram_type} diagram saved for this session yet",
        )

    # ── 2. Confluence fallback (legacy non-session path only) ──────────
    credentials = get_user_atlassian_credentials(user_id)
    if not credentials or not credentials.get("atlassian_api_token"):
        raise HTTPException(
            status_code=404,
            detail="No diagram in S3 for this session and Atlassian is not linked for the Confluence fallback.",
        )

    page_title = request.page_title.strip() or f"Architecture Diagram — {request.space_key}"
    try:
        confluence = ConfluenceService(
            credentials["atlassian_domain"],
            credentials["atlassian_email"],
            credentials["atlassian_api_token"],
        )

        if request.page_id.strip():
            page_content = confluence.get_content_page_by_id(
                request.page_id.strip(), expand="body.storage,version,_links"
            )
            xml = _extract_xml_from_confluence(page_content.get("body", {}).get("storage", {}).get("value", ""))
            if not xml:
                raise HTTPException(status_code=422, detail="Saved diagram page found but XML could not be extracted.")
            web_url = f"https://{credentials['atlassian_domain']}/wiki{page_content.get('_links', {}).get('webui', '')}"
            logger.info(f"[DESIGN] Diagram loaded by page_id {request.page_id}")
            return LoadDiagramResponse(xml=xml, page_url=web_url, page_id=request.page_id.strip(), source="confluence")

        if not request.space_key:
            raise HTTPException(status_code=400, detail="Either session_id, page_id, or space_key is required to load a diagram")

        existing = confluence.find_page_by_title(request.space_key, page_title)
        if not existing:
            raise HTTPException(status_code=404, detail="No saved diagram found.")

        page_content = confluence.get_page_content(existing["id"])
        xml = _extract_xml_from_confluence(page_content["content"])
        if not xml:
            raise HTTPException(status_code=422, detail="Saved diagram page found but XML could not be extracted.")

        web_url = f"https://{credentials['atlassian_domain']}/wiki{existing.get('_links', {}).get('webui', '')}"
        logger.info(f"[DESIGN] Diagram loaded from Confluence page {existing['id']}")
        return LoadDiagramResponse(xml=xml, page_url=web_url, page_id=existing["id"], source="confluence")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DESIGN] Load diagram error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load diagram: {str(e)}")


# ============================================================================
# Lucidchart integration
# ============================================================================
#
# Lucid is offered alongside draw.io as a second authoring path for the
# diagram phase. The flow:
#   1. User picks Confluence pages and a diagram type (logical / infra / security).
#   2. /generate-lucid-prompt(-stream) calls Claude with one of the three
#      LUCID_*_PROMPT_SYSTEM templates and returns a structured brief.
#   3. User connects their Lucid account via OAuth (/lucid-auth-url →
#      lucid.app → /lucid-callback). Token is held in-memory keyed by
#      user_id so subsequent /create-lucid-mcp calls authenticate as them.
#   4. /create-lucid-mcp invokes Lucid's MCP `lucid_create_diagram_from_description`
#      tool through services.lucid_mcp_client and returns the edit URL.

import httpx
from urllib.parse import urlencode
from fastapi.responses import RedirectResponse
from services.lucid_mcp_client import create_diagram_from_description as _lucid_mcp_create

LUCID_PROMPT_MODEL_ID = os.getenv(
    "DESIGN_LUCID_PROMPT_MODEL_ID",
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
)
LUCID_PROMPT_MAX_TOKENS = int(os.getenv("DESIGN_LUCID_PROMPT_MAX_TOKENS", "8192"))

LUCID_CLIENT_ID = os.getenv("LUCID_CLIENT_ID", "")
LUCID_CLIENT_SECRET = os.getenv("LUCID_CLIENT_SECRET", "")
LUCID_REDIRECT_URI = os.getenv(
    "LUCID_REDIRECT_URI",
    "http://localhost:8000/api/design/lucid-callback",
)
LUCID_FRONTEND_URL = os.getenv("LUCID_FRONTEND_URL", "http://localhost:5173")

# user_id → access_token. In-memory only — fine for single-Pod dev/VDI;
# move to a DB column or KMS-encrypted store before scaling horizontally.
_lucid_tokens: dict = {}


# ─── Lucid prompt templates (one per diagram type) ────────────────────────────

LUCID_LOGICAL_PROMPT_SYSTEM = """
You are a senior solutions architect. You will be given content from one or more Confluence pages
or design documents describing a software project. Read everything carefully, then produce a
concise, structured diagram brief that Lucid AI can directly consume to generate a
professional logical architecture diagram.

A LOGICAL diagram shows WHAT the system does and HOW data flows between capabilities.
It is vendor-agnostic — no cloud provider names, no server types, no infrastructure details.

══════════════════════════════════════════════════
OUTPUT — write exactly this block, plain text only
══════════════════════════════════════════════════

Create a logical architecture diagram titled "[Project Name] – Logical Architecture".

OVERVIEW
[Project Name] is [2–3 sentence plain-English description of what the system does and who uses it].

ACTORS
[Actor Name] ([type: End User | Admin | External System | Partner]): [one sentence — what they do in the system]
[repeat for each actor, one per line]

CAPABILITIES
[Capability Name] ([layer: Presentation | API | Business Logic | Data | Messaging | External]): [one sentence — what this capability is responsible for]
[aim for 8–15 capabilities, one per line]

DATA FLOWS
[Source] → [Target]: [verb phrase describing what moves, e.g. "submits login request", "returns user profile", "publishes order event", "queries transaction history"]
[one flow per line — list every meaningful interaction]

GROUPS
[Group/Layer Name]: [Capability1], [Capability2], [Capability3]
[group capabilities into 3–5 logical layers, one group per line]

NOTES
[Any key business rules, constraints, or open questions — one bullet per line starting with -]

══════════════════════════════════════════════════
RULES
══════════════════════════════════════════════════
- Output ONLY the diagram brief above — no preamble, no explanation, no markdown fences
- No cloud provider names (AWS, Azure, GCP), no server types, no infrastructure
- Keep capability names short (2–4 words): "Order Service", "Auth Gateway", "Notification Engine"
- Data flow verbs must be specific: "authenticates", "queries", "publishes", "streams", "validates" — not generic "uses" or "calls"
- If a detail is not in the documents, infer what a senior architect would include and append (inferred)
- Aim for 8–15 capabilities and 10–20 data flows — enough to make a meaningful diagram, not overwhelming
"""

LUCID_ARCHITECTURE_PROMPT_SYSTEM = """
You are a senior infrastructure architect. You will be given content from one or more Confluence pages
describing a software project. Read everything carefully, then produce a concise, structured
diagram brief that Lucid AI can directly consume to generate a professional infrastructure
architecture diagram.

An INFRASTRUCTURE diagram shows WHERE the system runs and HOW components connect —
cloud services, compute, databases, networking, security, and external integrations.

══════════════════════════════════════════════════
OUTPUT — write exactly this block, plain text only
══════════════════════════════════════════════════

Create an infrastructure architecture diagram titled "[Project Name] – Infrastructure Architecture".

OVERVIEW
[Project Name] is [2–3 sentence description of the system, its cloud platform, and scale context].

COMPONENTS
[Component Name] ([type: Web App | REST API | GraphQL API | Microservice | Serverless Function | Container | Load Balancer | CDN | API Gateway | Database | Cache | Object Storage | Message Queue | Event Bus | Auth Service | WAF | Secret Store | Monitoring | External API | SaaS Integration]): [one sentence — what it does]
[aim for 12–20 components, one per line]

CONNECTIONS
[Source Component] → [Target Component]: [verb phrase, e.g. "routes HTTPS traffic to", "reads and writes records in", "publishes events to", "authenticates requests via", "caches responses in", "triggers on message from"]
[one connection per line — every meaningful link between components]

GROUPS
[Layer/Zone Name]: [Component1], [Component2], [Component3]
[group components into 4–6 infrastructure layers, e.g. "Public Layer", "Compute Layer", "Data Layer", "Security Layer", "Monitoring", "External Services"]

NOTES
[Key architectural decisions, inferred components, or important constraints — one bullet per line starting with -]

══════════════════════════════════════════════════
RULES
══════════════════════════════════════════════════
- Output ONLY the diagram brief above — no preamble, no explanation, no markdown fences
- Component names must match real service names where known (e.g. "AWS Lambda", "Redis Cache", "PostgreSQL", "Azure AD")
- Connection verbs must be precise: "routes", "queries", "caches", "validates", "streams", "writes to" — not "uses" or "talks to"
- If a detail is not in the documents, infer what a senior architect would include and append (inferred)
- Aim for 12–20 components and 15–25 connections — enough for a complete, meaningful diagram
"""

LUCID_SECURITY_PROMPT_SYSTEM = """
You are a senior security architect. You will be given content from one or more Confluence pages
or design documents describing a software project. Read everything carefully, then produce a
concise, structured diagram brief that Lucid AI can directly consume to generate a professional
security architecture diagram.

A SECURITY diagram shows trust boundaries, WHO accesses WHAT, WHERE controls sit,
and HOW data is protected. It is organized by trust zones, not deployment layers.

══════════════════════════════════════════════════
OUTPUT — write exactly this block, plain text only
══════════════════════════════════════════════════

Create a security architecture diagram titled "[Project Name] – Security Architecture".

OVERVIEW
[Project Name] is [2–3 sentence description of the system, its data sensitivity, and compliance context].
Compliance scope: [list frameworks: GDPR, HIPAA, SOC 2, ISO 27001, PCI-DSS — or "None identified"]

TRUST ZONES
[Zone Name] (trust: [Untrusted | Low | Medium | High | Privileged]): [one sentence — what lives here and why this trust level]
[list 4–6 zones, one per line: e.g. "Internet Zone", "DMZ", "Application Zone", "Data Zone", "Admin Zone", "CI/CD Zone"]

COMPONENTS
[Component Name] ([type: User | External Client | API Gateway | WAF | Firewall | Auth Server | Service | Database | Secret Store | Audit Log | Admin Console | CI/CD Pipeline]): [zone it belongs to] — [one sentence security role]
[aim for 10–18 components, one per line]

ACCESS FLOWS
[Actor/Component] → [Target Component]: [auth mechanism + what they can do, e.g. "authenticates via OAuth 2.0 + MFA, can read user profile", "uses mTLS, can publish events to queue", "uses API key, read-only access to metrics"]
[one flow per line — every trust boundary crossing]

SECURITY CONTROLS
[Control Name] ([type: WAF | MFA | TLS | RBAC | Encryption at Rest | Rate Limiting | SIEM | Audit Logging | Secret Rotation | Network Policy]): enforces [what rule] at [which boundary or component]
[one control per line]

GROUPS
[Zone Name]: [Component1], [Component2], [Component3]
[map every component to its zone — one group per zone]

NOTES
[Security gaps, compliance mappings, or inferred controls — one bullet per line starting with -]

══════════════════════════════════════════════════
RULES
══════════════════════════════════════════════════
- Output ONLY the diagram brief above — no preamble, no explanation, no markdown fences
- Every access flow must cross a trust boundary — internal calls within the same zone are not access flows
- Control names must be specific: "Azure AD MFA" not just "MFA", "AES-256 at rest" not just "encryption"
- If a detail is not in the documents, infer what a senior security architect would include and append (inferred)
- Aim for 10–18 components, 4–6 trust zones, and 8–15 access flows
"""


# ─── Lucid request / response models ──────────────────────────────────────────

class GenerateLucidPromptRequest(BaseModel):
    project_id: str
    page_contents: List[str]
    diagram_type: str = "infrastructure"  # logical | infrastructure | security


class GenerateLucidPromptResponse(BaseModel):
    prompt: str


class CreateLucidMcpRequest(BaseModel):
    prompt: str
    title: str


class CreateLucidMcpResponse(BaseModel):
    edit_url: str
    document_id: str
    raw: str = ""


def _get_lucid_system_prompt(diagram_type: str) -> str:
    if diagram_type == "logical":
        return LUCID_LOGICAL_PROMPT_SYSTEM
    if diagram_type == "security":
        return LUCID_SECURITY_PROMPT_SYSTEM
    return LUCID_ARCHITECTURE_PROMPT_SYSTEM


# ─── Lucid prompt-generation endpoints ────────────────────────────────────────

@router.post("/generate-lucid-prompt", response_model=GenerateLucidPromptResponse)
async def generate_lucid_prompt(
    request: GenerateLucidPromptRequest,
    current_user: dict = Depends(get_current_user),
):
    """Build a structured architecture brief from selected Confluence pages.
    Returns the full prompt as a single string (non-streamed)."""
    if not request.page_contents:
        raise HTTPException(status_code=400, detail="No page contents provided")

    combined = "\n\n---\n\n".join(request.page_contents)
    user_message = (
        "Read the following Confluence documentation carefully and produce the diagram brief "
        "following your instructions exactly.\n\n"
        f"CONFLUENCE CONTENT:\n{combined}\n\n"
        "Output ONLY the diagram brief — starting with the \"Create a ... diagram titled\" line. "
        "No preamble, no explanation."
    )
    system_prompt = _get_lucid_system_prompt(request.diagram_type)
    prompt = _invoke_claude(
        system_prompt,
        user_message,
        LUCID_PROMPT_MODEL_ID,
        LUCID_PROMPT_MAX_TOKENS,
        user_id=current_user.get("id") or current_user.get("id"),
    )
    logger.info(f"[DESIGN] Lucid {request.diagram_type} prompt generated ({len(prompt)} chars)")
    return GenerateLucidPromptResponse(prompt=prompt)


@router.post("/generate-lucid-prompt-stream")
async def generate_lucid_prompt_stream(
    request: GenerateLucidPromptRequest,
    _current_user: dict = Depends(get_current_user),
):
    """SSE-streaming variant of /generate-lucid-prompt for progressive UI."""
    if not request.page_contents:
        raise HTTPException(status_code=400, detail="No page contents provided")

    combined = "\n\n---\n\n".join(request.page_contents)
    user_message = (
        "Read the following Confluence documentation carefully and produce the diagram brief "
        "following your instructions exactly.\n\n"
        f"CONFLUENCE CONTENT:\n{combined}\n\n"
        "Output ONLY the diagram brief — starting with the \"Create a ... diagram titled\" line. "
        "No preamble, no explanation."
    )
    system_prompt = _get_lucid_system_prompt(request.diagram_type)

    def stream():
        try:
            yield from _invoke_claude_stream(
                system_prompt, user_message, LUCID_PROMPT_MODEL_ID, LUCID_PROMPT_MAX_TOKENS,
                user_id=_current_user.get("id"),
                token_source="routers/design:generate_lucid_prompt_stream",
            )
        except Exception as e:
            logger.error(f"[DESIGN] Lucid prompt stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Lucid OAuth ──────────────────────────────────────────────────────────────

@router.get("/lucid-auth-url")
async def get_lucid_auth_url(current_user: dict = Depends(get_current_user)):
    if not LUCID_CLIENT_ID:
        raise HTTPException(status_code=500, detail="LUCID_CLIENT_ID not configured")
    user_id = str(current_user.get("id") or current_user.get("id") or "default")
    state = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    params = urlencode({
        "client_id": LUCID_CLIENT_ID,
        "redirect_uri": LUCID_REDIRECT_URI,
        "response_type": "code",
        "scope": "lucidchart.document.content offline_access",
        "state": state,
    })
    return {"url": f"https://lucid.app/oauth2/authorize?{params}"}


@router.get("/lucid-status")
async def get_lucid_status(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user.get("id") or current_user.get("id") or "default")
    connected = bool(_lucid_tokens.get(user_id) or os.getenv("LUCID_OAUTH_TOKEN", ""))
    return {"connected": connected}


@router.get("/lucid-callback")
async def lucid_oauth_callback(code: str = None, state: str = "", error: str = None):
    """OAuth redirect target — exchanges the auth code for an access token,
    stashes it under the user, and bounces the browser back to the SPA."""
    if error or not code:
        return RedirectResponse(url=f"{LUCID_FRONTEND_URL}/design-assistant?lucid=error")

    try:
        padded = state + "=" * (4 - len(state) % 4)
        user_id = base64.urlsafe_b64decode(padded).decode()
    except Exception:
        user_id = "default"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.lucid.co/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": LUCID_CLIENT_ID,
                "client_secret": LUCID_CLIENT_SECRET,
                "redirect_uri": LUCID_REDIRECT_URI,
            },
        )
        data = r.json()

    access_token = data.get("access_token", "")
    if not access_token:
        logger.error(f"[LUCID_AUTH] Token exchange failed: {data}")
        return RedirectResponse(url=f"{LUCID_FRONTEND_URL}/design-assistant?lucid=error")

    _lucid_tokens[user_id] = access_token
    logger.info(f"[LUCID_AUTH] Token stored for user: {user_id[:30]}")
    return RedirectResponse(url=f"{LUCID_FRONTEND_URL}/design-assistant?lucid=connected")


# ─── Lucid MCP — diagram creation ─────────────────────────────────────────────

@router.post("/create-lucid-mcp", response_model=CreateLucidMcpResponse)
async def create_lucid_diagram_via_mcp(
    request: CreateLucidMcpRequest,
    current_user: dict = Depends(get_current_user),
):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required")

    user_id = str(current_user.get("id") or current_user.get("id") or "default")
    token = _lucid_tokens.get(user_id) or os.getenv("LUCID_OAUTH_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not connected to Lucid. Click 'Connect to Lucid' first.")

    title = request.title.strip() or "Architecture Diagram"

    try:
        result = await _lucid_mcp_create(description=request.prompt, title=title, token=token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"[DESIGN] Lucid MCP error: {e}")
        raise HTTPException(status_code=502, detail=f"Lucid MCP error: {str(e)}")

    if not result.get("edit_url"):
        logger.warning(f"[DESIGN] MCP call returned no URL. Raw: {result.get('raw', '')[:200]}")
        raise HTTPException(status_code=502, detail="Diagram created but no URL returned")

    return CreateLucidMcpResponse(
        edit_url=result["edit_url"],
        document_id=result["document_id"],
        raw=result.get("raw", ""),
    )


# =============================================================================
# Lucid REST API import flow (personal API key, NOT OAuth)
# =============================================================================
# The user pastes their personal Lucid REST API key on the Profile page; we
# store it KMS-encrypted via db_helper.update_user_lucid_credentials. The
# Architecture / Lucid section then lets them:
#   1) generate the architecture prompt (existing /generate-lucid-prompt-stream)
#   2) paste it into Lucid AI to create the diagram (manual step in lucid.app)
#   3) GET /lucid/documents — list the user's recent Lucid docs, pre-filtered
#      by the suggested title so the new doc is at the top
#   4) POST /lucid/import — fetch the chosen doc as SVG, write to S3 at the
#      existing diagram-slot path, update diagram_slots so SAD generation
#      picks it up identically to a drawio diagram (no SAD-side changes).
# The legacy OAuth /create-lucid-mcp flow above is kept as a one-click
# "generate the diagram for me" shortcut and runs in parallel.


def _get_lucid_service_for_user(user_id: str) -> LucidAPIService:
    """Load the user's KMS-decrypted Lucid API key and instantiate a service.

    Raises HTTPException(400) if the user hasn't linked an API key yet — the
    UI is expected to surface this as "Link your Lucid API key in Profile."
    """
    creds = get_user_lucid_credentials(user_id)
    if not creds or not creds.get("lucid_api_key"):
        raise HTTPException(
            status_code=400,
            detail="No Lucid API key on file. Link your Lucid account in Profile first.",
        )
    return LucidAPIService(creds["lucid_api_key"])


def _lucid_error_to_http(e: Exception, context: str) -> HTTPException:
    """Translate typed Lucid errors into FastAPI HTTPExceptions with sensible
    status codes. Anything unknown bubbles as 502."""
    if isinstance(e, InvalidLucidKeyError):
        return HTTPException(
            status_code=401,
            detail=("Lucid rejected the saved API key. "
                    "Re-link your Lucid account in Profile."),
        )
    if isinstance(e, LucidNotAccessibleError):
        return HTTPException(
            status_code=404,
            detail=f"Lucid document not found or not accessible ({context}).",
        )
    if isinstance(e, LucidUpstreamError):
        return HTTPException(
            status_code=502,
            detail=f"Lucid is temporarily unavailable ({context}).",
        )
    if isinstance(e, LucidError):
        return HTTPException(status_code=502, detail=str(e))
    return HTTPException(status_code=502, detail=f"Unexpected Lucid error: {e}")


class LucidDocumentItem(BaseModel):
    document_id: str
    title: str
    last_modified: Optional[str] = None


class ListLucidDocumentsResponse(BaseModel):
    documents: List[LucidDocumentItem]
    suggested_search: Optional[str] = None


@router.get("/lucid/documents", response_model=ListLucidDocumentsResponse)
async def list_lucid_documents(
    search: Optional[str] = None,
    suggest: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """List the authenticated user's recent Lucid documents.

    Query params:
      - `search`: explicit user-typed search string. Filters server-side
        via Lucid's `keywords` field. When empty, returns all of the
        user's docs (paginated, capped at 500).
      - `suggest`: ignored as a filter — kept for backward-compat with the
        frontend which uses this value purely as the search-box placeholder
        text. Previously this was OR'd into `search` which silently hid any
        document whose title didn't match the suggestion (e.g. a "Logical
        Architecture" suggestion would hide a freshly-created "infra idp"
        diagram even on an empty user search).

    Returns up to 500 docs across the user's Lucidchart + Lucidspark
    documents, sorted by lastModified DESC. Pagination is handled
    transparently in `LucidAPIService.list_documents`.
    """
    # IMPORTANT: do NOT fold `suggest` into the search filter — see docstring.
    effective_search = (search or "").strip() or None
    _ = suggest  # accepted but unused; param kept for API stability
    service = _get_lucid_service_for_user(current_user["id"])
    try:
        docs = service.list_documents(search=effective_search)
    except Exception as e:
        raise _lucid_error_to_http(e, "list_documents")

    items = [
        LucidDocumentItem(
            document_id=d.get("documentId") or d.get("id") or "",
            title=d.get("title") or "(untitled)",
            last_modified=d.get("lastModified") or d.get("modified"),
        )
        for d in docs
        if (d.get("documentId") or d.get("id"))
    ]
    return ListLucidDocumentsResponse(
        documents=items,
        suggested_search=effective_search,
    )


class ImportLucidRequest(BaseModel):
    session_id: str
    document_id: str
    diagram_type: str  # "logical" | "infrastructure" | "security"
    document_title: Optional[str] = None  # echoed back for client convenience


class ImportLucidResponse(BaseModel):
    artifact_key: str
    diagram_type: str
    preview_url: str  # frontend-accessible GET to stream the SVG back
    saved_at: int
    document_id: str
    document_title: Optional[str] = None


_LUCID_DIAGRAM_TYPES = {"logical", "infrastructure", "security"}


@router.post("/lucid/import", response_model=ImportLucidResponse)
async def import_lucid_document(
    request: ImportLucidRequest,
    current_user: dict = Depends(get_current_user),
):
    """Fetch a Lucid document as SVG, persist to S3 against the session's
    diagram slot, and patch design_sessions.diagram_slots so the SAD
    generator's existing per-section diagram block emits this artifact.

    This endpoint does NOT modify lambda_sad_orchestrator or the SAD
    schema — it produces an artifact at the exact same S3 path the drawio
    save path uses, so SAD picks it up identically.
    """
    if request.diagram_type not in _LUCID_DIAGRAM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"diagram_type must be one of {sorted(_LUCID_DIAGRAM_TYPES)}",
        )

    # Sanity-check the session exists and belongs to a project the user has
    # access to. The existing get_design_session helper does the lookup;
    # downstream require_module("design") covers RBAC at the route level.
    session = get_design_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="design_session not found")

    service = _get_lucid_service_for_user(current_user["id"])

    # Two independent Lucid round-trips for one import:
    #   A. GET /documents/{id} with Accept: image/png    -> PNG bytes -> S3
    #   B. GET /documents/{id}/contents                  -> JSON       -> S3
    # Run them in parallel since neither depends on the other. Halves
    # wall-clock latency on the user's "Fetch & Save" click (typically
    # 2-3s -> 1-1.5s for a medium-size diagram).
    #
    # PNG fetch is REQUIRED (failure aborts the import); JSON fetch is
    # best-effort (only used for SAD-generation LLM context — if it
    # fails we log and continue, and the section worker just gets less
    # context for this slot until the user re-imports).
    artifact_key = f"sessions/{request.session_id}/diagram/{request.diagram_type}.png"
    contents_key = f"sessions/{request.session_id}/diagram/{request.diagram_type}.lucid.json"

    def _fetch_and_save_png() -> str:
        png_bytes = service.export_document(request.document_id, fmt="png")
        s3_put_object(
            key=artifact_key, body=png_bytes, content_type="image/png",
        )
        return artifact_key

    def _fetch_and_save_json() -> Optional[Dict[str, Any]]:
        try:
            doc = service.get_document_contents(request.document_id)
            s3_put_object(
                key=contents_key,
                body=json.dumps(doc).encode("utf-8"),
                content_type="application/json",
            )
            logger.info(
                f"[LUCID IMPORT] saved structured JSON to {contents_key} "
                f"({len(doc.get('pages', []))} pages)"
            )
            return doc
        except Exception as e:
            # Don't fail the import — the diagram is saved and renderable.
            # Just log so we know LLM context will be missing for this slot.
            logger.warning(
                f"[LUCID IMPORT] couldn't save /contents JSON for "
                f"{request.document_id}: {e}. PNG saved; SAD generator won't "
                "have Lucid-shape context for this section."
            )
            return None

    # asyncio.to_thread (3.9+) runs the sync function in the default
    # threadpool. gather waits for BOTH to settle. PNG errors propagate
    # via return_exceptions=False; JSON errors are swallowed inside
    # _fetch_and_save_json so it never raises.
    try:
        _png_key, _lucid_doc = await asyncio.gather(
            asyncio.to_thread(_fetch_and_save_png),
            asyncio.to_thread(_fetch_and_save_json),
        )
    except Exception as e:
        # PNG fetch or S3 put failed. Translate Lucid-side errors to
        # HTTP; everything else gets a 500 with the underlying message.
        if isinstance(e, (InvalidLucidKeyError, LucidNotAccessibleError,
                          LucidUpstreamError, LucidError)):
            raise _lucid_error_to_http(e, f"export_document({request.document_id})")
        logger.error(f"[LUCID IMPORT] PNG fetch/save failed for {artifact_key}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save diagram to S3: {e}")

    saved_at = int(datetime.utcnow().timestamp())
    try:
        update_diagram_slot(
            session_id=request.session_id,
            diagram_type=request.diagram_type,
            patch={
                "status": "done",
                "tool": "lucid",
                "artifact_key": artifact_key,
                "saved_at": saved_at,
            },
        )
    except Exception as e:
        logger.error(f"[LUCID IMPORT] slot update failed for {request.session_id}: {e}")
        # The artifact is in S3 but the slot didn't update — caller can retry.
        raise HTTPException(
            status_code=500,
            detail=("Diagram saved to S3 but failed to mark slot done. "
                    "Retry the import."),
        )

    logger.info(
        f"[LUCID IMPORT] user={current_user['id']} session={request.session_id} "
        f"type={request.diagram_type} doc={request.document_id} → {artifact_key}"
    )
    return ImportLucidResponse(
        artifact_key=artifact_key,
        diagram_type=request.diagram_type,
        preview_url=(
            f"/api/design/lucid/preview/{request.session_id}/{request.diagram_type}"
        ),
        saved_at=saved_at,
        document_id=request.document_id,
        document_title=request.document_title,
    )


@router.get("/lucid/preview/{session_id}/{diagram_type}")
async def preview_lucid_import(
    session_id: str,
    diagram_type: str,
    current_user: dict = Depends(get_current_user),
):
    """Stream the saved diagram bytes back to the browser for the inline
    preview pane.

    Tries .png first (current Lucid import format — see export_document),
    then falls back to .svg for legacy artifacts saved by older versions of
    this endpoint before we switched off the broken `/contents?format=svg`
    path. Content-Type is set from whichever file actually exists so the
    browser renders correctly without sniffing.
    """
    if diagram_type not in _LUCID_DIAGRAM_TYPES:
        raise HTTPException(status_code=400, detail="Invalid diagram_type")

    session = get_design_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="design_session not found")

    s3 = get_s3_client()
    candidates = [
        (f"sessions/{session_id}/diagram/{diagram_type}.png", "image/png"),
        (f"sessions/{session_id}/diagram/{diagram_type}.svg", "image/svg+xml"),
    ]
    for artifact_key, content_type in candidates:
        try:
            obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=artifact_key)
            body_bytes = obj["Body"].read()
            return StreamingResponse(
                iter([body_bytes]),
                media_type=content_type,
                headers={"Cache-Control": "no-cache"},
            )
        except Exception as e:
            logger.debug(f"[LUCID PREVIEW] miss for {artifact_key}: {e}")
            continue

    raise HTTPException(
        status_code=404,
        detail="No saved diagram at this slot. Import from Lucid first.",
    )
