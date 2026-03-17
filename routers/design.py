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
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from auth import verify_azure_token
from db_helper import create_or_update_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/design", tags=["design"])

# ─── Bedrock client ───────────────────────────────────────────────────────────

_bedrock_client = None

def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        region = os.getenv("AWS_REGION", os.getenv("BEDROCK_REGION", "us-east-1"))
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client

PROMPT_MODEL_ID   = os.getenv("DESIGN_PROMPT_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
XML_MODEL_ID      = os.getenv("DESIGN_XML_MODEL_ID",    "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
PROMPT_MAX_TOKENS = int(os.getenv("DESIGN_PROMPT_MAX_TOKENS", "32768"))
XML_MAX_TOKENS    = int(os.getenv("DESIGN_XML_MAX_TOKENS",    "16384"))


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


# ─── Endpoints ────────────────────────────────────────────────────────────────

ARCHITECTURE_SYSTEM_PROMPT = """# AWS Architecture Diagram Agent System Prompt (v4.0)

## Role
You are an AWS cloud architecture expert. You read a BRD and output a single, self-contained prompt that any AI tool can use to generate a professional, draw.io-compatible XML diagram.

---

## Your Output
After reading the BRD, output **only** a ready-to-use prompt (not the XML itself). The prompt must be fully populated — no placeholders — so it works when pasted directly into ChatGPT, Gemini, or any LLM.

---

## What to Extract from the BRD
- Project name and purpose
- Core functional modules (keep to the most important ~20 components)
- External integrations explicitly mentioned
- Scale and security context (infer if not stated)

**Infer sensibly.** If the BRD doesn't specify networking, security, or monitoring details, add the standard AWS components a senior architect would include.

---

## Component Limit: ~20 Shapes

Focus on the **most architecturally significant** components only. A good breakdown:

| Layer | Count |
|---|---|
| User / Entry Point | 1–2 |
| Frontend | 2–3 |
| Backend / Compute | 4–6 |
| Data | 3–4 |
| Security | 2–3 |
| Monitoring | 1–2 |
| External integrations | 1–3 |

---

## AWS Icons (MANDATORY)

Always use official AWS icon shapes. Never use plain rectangles for AWS services.

### Icon Reference

| Service | Shape Style |
|---|---|
| Users / Clients | `shape=mxgraph.aws4.user;` |
| CloudFront | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudfront` |
| Route 53 | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.route_53` |
| API Gateway | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.api_gateway` |
| ALB | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.application_load_balancer` |
| Lambda | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda` |
| ECS / Fargate | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.fargate` |
| EKS | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.eks` |
| EC2 | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2` |
| S3 | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3` |
| RDS | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.rds` |
| DynamoDB | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.dynamodb` |
| ElastiCache | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.elasticache` |
| SQS | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sqs` |
| SNS | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sns` |
| Cognito | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cognito` |
| IAM | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.role` |
| WAF | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.waf` |
| Secrets Manager | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.secrets_manager` |
| CloudWatch | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudwatch` |
| Bedrock | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.bedrock` |
| SageMaker | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.sagemaker` |
| Kinesis | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.kinesis` |
| Step Functions | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.step_functions` |
| VPC | `shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc` |
| Public Subnet | `shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_public_subnet` |
| Private Subnet | `shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_private_subnet` |

**Icon cell format:**
```xml
<mxCell id="lambda_fn" value="Order Processor"
  style="outlineConnect=0;fontColor=#232F3E;gradientColor=none;
         strokeColor=none;fillColor=#E7157B;
         shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;
         labelPosition=center;verticalLabelPosition=bottom;
         verticalAlign=top;align=center;html=1;fontSize=11;"
  vertex="1" parent="1">
  <mxGeometry x="500" y="400" width="60" height="60" as="geometry"/>
</mxCell>
```

---

## Color Scheme (AWS Official Palette — MANDATORY)

Use AWS's official service category colors. This is what makes the diagram look professional and vibrant.

| Category | Fill Color | Used For |
|---|---|---|
| Compute (orange) | `#ED7100` | Lambda, EC2, ECS, EKS, Fargate |
| Storage (green) | `#3F8624` | S3, EFS, Glacier |
| Database (blue) | `#1A9C3E` | RDS, DynamoDB, Aurora |
| Networking (purple) | `#8C4FFF` | CloudFront, Route 53, ALB, API Gateway, VPC |
| Security (red) | `#DD344C` | Cognito, WAF, IAM, Secrets Manager |
| Integration (pink) | `#E7157B` | SQS, SNS, EventBridge, Step Functions |
| AI/ML (teal) | `#01A88D` | Bedrock, SageMaker |
| Management (orange-red) | `#E7157B` | CloudWatch, CloudTrail |
| General/User | `#232F3E` | User icons, client apps |

**Container (group) colors:**

| Container | Fill | Stroke |
|---|---|---|
| VPC | `#F0F8FF` | `#8C4FFF` |
| Public Subnet | `#E8F5E9` | `#3F8624` |
| Private Subnet | `#FFF3E0` | `#ED7100` |
| External Zone | `#F5F5F5` | `#999999` |

---

## Layout Rules

- **Canvas:** 1600px x 1000px
- **Icon size:** 60x60px for all AWS service icons
- **Horizontal spacing:** minimum 120px between icons
- **Vertical spacing:** minimum 100px between rows
- **Labels:** below each icon, font size 11px, color `#232F3E`
- **Flow direction:** left to right, top to bottom
- **Containers:** VPC wraps backend and data layers; use subnet groupings

**Layout zones:**

```
Left (x=50-200):    Users / External clients
Center-left (x=250-500):  Entry layer (CDN, DNS, WAF, API GW)
Center (x=550-950): Compute / backend services
Center-right (x=1000-1250): Data layer
Right (x=1300-1550): External services / monitoring
```

---

## Arrow Standards

```xml
<!-- Standard flow arrow -->
style="edgeStyle=orthogonalEdgeStyle;html=1;
       strokeColor=#555555;strokeWidth=1.5;
       fontSize=10;fontColor=#333333;
       rounded=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;"

<!-- Async / event arrow (dashed) -->
style="edgeStyle=orthogonalEdgeStyle;html=1;
       strokeColor=#999999;strokeWidth=1.5;dashed=1;
       fontSize=10;fontColor=#666666;rounded=1;"
```

- Label every arrow with a short action: `"HTTPS"`, `"Publishes event"`, `"Reads/Writes"`, `"Authenticates"`
- Use dashed lines for async/event-driven flows
- Route arrows to avoid crossing icons — use waypoints when needed

---

## Generated Prompt Template

Populate every field below and output this as your response:

```
=============================================================
PROMPT: GENERATE AWS DRAW.IO ARCHITECTURE DIAGRAM
=============================================================

TASK
Generate a valid draw.io XML file for the AWS architecture below.
Output raw XML only. No markdown, no explanations, no code fences.

---

PROJECT
Name: [Project name]
Description: [1-2 sentence summary]

---

COMPONENTS (~20 total)
List each component with:
  ID | Display Label | AWS Service | Icon Shape Style | Fill Color | x | y

Example row:
  cloudfront_cdn | CloudFront CDN | CloudFront
  shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudfront
  fill=#8C4FFF | x=300 | y=100

[Populate all ~20 components here]

---

CONTAINERS
List each grouping boundary:
  ID | Label | x | y | width | height | fill | stroke

Example:
  vpc | AWS VPC | x=400 y=200 w=900 h=600 fill=#F0F8FF stroke=#8C4FFF
  private_subnet | Private Subnet | x=550 y=300 w=600 h=400
                   fill=#FFF3E0 stroke=#ED7100

[Populate all containers here]

---

CONNECTIONS
List each arrow:
  Source ID -> Target ID | Label | solid or dashed

Example:
  cloudfront_cdn -> api_gateway | "HTTPS requests" | solid
  lambda_fn -> sqs_queue | "Publishes event" | dashed

[Populate all connections here]

---

DIAGRAM TITLE
"[Project Name] - AWS Architecture"
x=600, y=20, font size 20px bold, color #232F3E

---

XML RULES (follow exactly)
1. Canvas: pageWidth="1600" pageHeight="1000"
2. All AWS icons: 60x60px, label below (verticalLabelPosition=bottom)
3. Icon style must include correct fillColor from AWS palette
4. Icon style must include: outlineConnect=0; strokeColor=none; labelPosition=center;
   verticalLabelPosition=bottom; verticalAlign=top; align=center; html=1;
5. Containers use swimlane or group style with rounded corners
6. Arrows: edgeStyle=orthogonalEdgeStyle; rounded=1; html=1
7. Add mxPoint waypoints on any arrow that would cross an icon
8. All mxCell IDs must be unique snake_case strings
9. Replace and with "and" — never use bare and in XML
10. Output raw XML starting with <mxfile ...>

=============================================================
END OF PROMPT
=============================================================
```

---

## Final Instructions

1. Read the BRD carefully
2. Select ~20 most important AWS components
3. Assign each an icon from the icon reference table
4. Assign each the correct AWS category color
5. Plan x/y positions using the layout zones
6. Define all containers (VPC, subnets)
7. List all connections with labels
8. Fill in the template above completely
9. Output the populated prompt — nothing else

**Do not generate XML. Do not explain the architecture. Output only the filled prompt.**"""


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

    logger.info(f"[DESIGN] Generating architecture prompt v3.0 from {len(request.page_contents)} page(s)")
    prompt = _invoke_claude(ARCHITECTURE_SYSTEM_PROMPT, user_message, model_id=PROMPT_MODEL_ID, max_tokens=PROMPT_MAX_TOKENS)
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
    raw = _invoke_claude(system_prompt, user_message)

    # Strip any accidental markdown fences
    raw = raw.replace("```xml", "").replace("```", "").strip()

    # Extract the mxGraphModel block
    match = re.search(r"<mxGraphModel[\s\S]*?</mxGraphModel>", raw)
    xml = match.group(0) if match else raw

    logger.info(f"[DESIGN] XML generated ({len(xml)} chars)")
    return GenerateXMLResponse(xml=xml)
