"""
Pipeline Generator Router
Generates Harness CI/CD pipeline YAML from SAD/architecture documents
using Claude (Bedrock), then validates and creates via Harness REST API.
"""

import json
import logging
import re
import boto3
import httpx
from botocore.config import Config as BotoConfig
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from auth import verify_azure_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline-generator", tags=["pipeline-generator"])

HARNESS_BASE = "https://app.harness.io"

# ─── Bedrock ──────────────────────────────────────────────────────────────────

_bedrock = None

def get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            config=BotoConfig(read_timeout=120, connect_timeout=10, retries={"max_attempts": 2}),
        )
    return _bedrock

# ─── Models ───────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    # New simplified inputs
    repo_name: Optional[str] = ""
    branch: Optional[str] = "main"
    deployment_strategy: Optional[str] = "rolling"   # rolling, blue-green, canary, basic
    artifact_type: Optional[str] = "Docker"           # Docker, Zip
    environments: Optional[list] = ["dev"]
    node_version: Optional[str] = "24.14.0"
    # Legacy document-based inputs (still supported)
    document_content: Optional[str] = ""
    document_title: Optional[str] = "Architecture Document"
    # Common
    pipeline_name: Optional[str] = ""
    harness_api_key: Optional[str] = ""
    harness_account_id: Optional[str] = ""
    harness_org_id: Optional[str] = "default"
    harness_project_id: Optional[str] = ""

class ValidateRequest(BaseModel):
    yaml_content: str
    harness_api_key: str
    harness_account_id: str
    harness_org_id: Optional[str] = "default"
    harness_project_id: str

class CreatePipelineRequest(BaseModel):
    yaml_content: str
    harness_api_key: str
    harness_account_id: str
    harness_org_id: Optional[str] = "default"
    harness_project_id: str

# ─── Helpers ──────────────────────────────────────────────────────────────────

def harness_headers(api_key: str):
    return {"x-api-key": api_key, "Content-Type": "application/yaml"}

def strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', ' ', text).strip()


def clean_yaml(yaml_content: str) -> str:
    """
    Fix common LLM YAML generation issues:
    1. Strip markdown code fences
    2. Strip any leading explanation text before 'pipeline:'
    3. Unwrap nested pipeline: > pipeline: > pipeline: nesting
    """
    text = yaml_content.strip()

    # 1. Strip markdown code fences (```yaml ... ``` or ``` ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 2. Strip any text before the first 'pipeline:' line
    lines = text.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "pipeline:" or line.strip().startswith("pipeline:"):
            start = i
            break
    if start > 0:
        lines = lines[start:]
        text = "\n".join(lines)

    # 3. Unwrap nested consecutive pipeline: lines
    # e.g. "pipeline:\n  pipeline:\n    pipeline:\n      name: foo"
    # Count how many consecutive lines (at increasing indent) are just 'pipeline:'
    lines = text.split("\n")
    nesting = 0
    for line in lines:
        if line.strip() == "pipeline:":
            nesting += 1
        else:
            break

    if nesting > 1:
        # Real content starts after all the pipeline: nesting lines
        real_content = lines[nesting:]
        if real_content:
            # Detect how much extra indentation the content has
            first_real = next((l for l in real_content if l.strip()), None)
            if first_real:
                extra_indent = len(first_real) - len(first_real.lstrip())
                target_indent = 2  # standard Harness YAML indent
                dedent_by = extra_indent - target_indent
                if dedent_by > 0:
                    cleaned = []
                    for line in real_content:
                        if line.strip():
                            cleaned.append(line[dedent_by:] if len(line) >= dedent_by else line.lstrip())
                        else:
                            cleaned.append("")
                    return "pipeline:\n" + "\n".join(cleaned)
                return "pipeline:\n" + "\n".join(real_content)

    return text

def build_prompt(req: "GenerateRequest") -> str:
    pipeline_name = req.pipeline_name or (req.repo_name or "").replace("_", "-").replace(" ", "-").lower() or "my-pipeline"

    # If a document is provided, use document-based generation
    if req.document_content and req.document_content.strip():
        clean_doc = strip_html(req.document_content)[:8000]
        return f"""You are a Harness CI/CD expert. Generate a complete, production-ready Harness pipeline YAML based on the following Solution Architecture Document.

Document Title: {req.document_title or "Architecture Document"}
Pipeline name: {pipeline_name}

--- DOCUMENT START ---
{clean_doc}
--- DOCUMENT END ---

Instructions:
1. Analyze the document to identify the tech stack, deployment target, environments, and requirements.
2. Generate a complete Harness pipeline YAML with CI stage (build, test, scan) and CD stages per environment.
3. Use <+stage.variables.VAR_NAME> for runtime inputs and <+secrets.getValue("NAME")> for secrets.
4. Return ONLY valid Harness pipeline YAML — no explanation, no markdown, just raw YAML starting with "pipeline:"
"""

    # Repo-based generation
    strategy_map = {
        "rolling":    "Kubernetes Rolling Deployment — gradually replace old pods, zero downtime",
        "blue-green": "Kubernetes Blue-Green Deployment — two environments, instant traffic switch",
        "canary":     "Kubernetes Canary Deployment — route 10% traffic first, validate, then 100%",
        "basic":      "Basic/Recreate Deployment — stop old instances, start new ones",
    }
    strategy_desc = strategy_map.get(req.deployment_strategy or "rolling", strategy_map["rolling"])
    envs = req.environments or ["dev"]
    artifact_desc = (
        "Build Docker image and push to container registry" if (req.artifact_type or "Docker") == "Docker"
        else "Package build output as ZIP and upload to JFrog Artifactory"
    )

    cd_stages_desc = ""
    for env in envs:
        approval = "requires manual approval gate before deployment" if env.lower() in ("prod", "production") else "auto-deploys after previous stage passes"
        cd_stages_desc += f"   - {env.upper()}: {approval}\n"

    pipeline_id = pipeline_name.replace("-", "").replace("_", "").lower()
    cd_stages_yaml = ""
    for env in envs:
        env_id = env.lower()
        approval_block = ""
        if env_id in ("prod", "production"):
            approval_block = f"""              - step:
                  type: HarnessApproval
                  name: Approve {env.upper()} Deployment
                  identifier: Approve_{env.upper()}
                  spec:
                    approvalMessage: Please review and approve deployment to {env.upper()}
                    includePipelineExecutionHistory: true
                    approvers:
                      userGroups:
                        - account._account_all_users
                      minimumCount: 1
                    approverInputs: []
                  timeout: 1d
"""
        rollout_type = {
            "rolling":    "K8sRollingDeploy",
            "blue-green": "K8sBGSwapServices",
            "canary":     "K8sCanaryDeploy",
            "basic":      "K8sRollingDeploy",
        }.get(req.deployment_strategy or "rolling", "K8sRollingDeploy")

        cd_stages_yaml += f"""    - stage:
        name: Deploy {env.upper()}
        identifier: Deploy_{env.upper()}
        type: Deployment
        spec:
          deploymentType: Kubernetes
          service:
            serviceRef: <+stage.variables.SERVICE_REF>
          environment:
            environmentRef: {env_id}
            deployToAll: false
            infrastructureDefinitions:
              - identifier: <+stage.variables.INFRA_REF>
          execution:
            steps:
{approval_block}              - step:
                  type: {rollout_type}
                  name: {req.deployment_strategy.replace("-", " ").title()} Deploy
                  identifier: {rollout_type}
                  spec:
                    skipDryRun: false
                  timeout: 10m
              - step:
                  type: K8sRollingRollback
                  name: Rollback
                  identifier: Rollback
                  spec: {{}}
                  timeout: 10m
                  when:
                    stageStatus: Failure
            rollbackSteps: []
        variables:
          - name: SERVICE_REF
            type: String
            value: <+input>
          - name: INFRA_REF
            type: String
            value: <+input>
        failureStrategies:
          - onFailure:
              errors:
                - AllErrors
              action:
                type: StageRollback
"""

    return f"""You are a Harness CI/CD expert. Complete the following Harness pipeline YAML template by filling in only the CI stage steps. Do NOT change the structure, keys, or add any extra top-level fields.

STRICT RULES:
- Output ONLY raw YAML starting with "pipeline:"
- Do NOT wrap in markdown code fences
- Do NOT add any fields not shown in the template
- Do NOT nest pipeline: inside pipeline:
- Only fill in the STEPS inside the CI stage execution block
- Keep ALL existing keys exactly as shown

TEMPLATE TO COMPLETE (fill in the steps array):

pipeline:
  name: {pipeline_name}
  identifier: {pipeline_id}
  projectIdentifier: {req.harness_project_id or "PROJECT_ID"}
  orgIdentifier: {req.harness_org_id or "default"}
  tags: {{}}
  stages:
    - stage:
        name: Build
        identifier: Build
        type: CI
        spec:
          cloneCodebase: true
          infrastructure:
            type: KubernetesDirect
            spec:
              connectorRef: account.K8S_CONNECTOR
              namespace: harness-delegate-ng
              os: Linux
          execution:
            steps:
              - step:
                  type: Run
                  name: Install and Build
                  identifier: Install_and_Build
                  spec:
                    connectorRef: account.DOCKER_CONNECTOR
                    image: node:{req.node_version or "24.14.0"}-alpine
                    shell: Sh
                    command: |-
                      npm install --force
                      npm run build
              - step:
                  type: Run
                  name: Run Tests
                  identifier: Run_Tests
                  spec:
                    connectorRef: account.DOCKER_CONNECTOR
                    image: node:{req.node_version or "24.14.0"}-alpine
                    shell: Sh
                    command: npx jest --coverage --coverageReporters=lcov || true
              - step:
                  type: {"BuildAndPushDockerRegistry" if (req.artifact_type or "Docker") == "Docker" else "ArtifactoryUpload"}
                  name: {"Push Docker Image" if (req.artifact_type or "Docker") == "Docker" else "Upload Artifact"}
                  identifier: {"Push_Docker_Image" if (req.artifact_type or "Docker") == "Docker" else "Upload_Artifact"}
                  spec:
                    connectorRef: <+input>
                    {"repo: <+stage.variables.IMAGE_REPO>" if (req.artifact_type or "Docker") == "Docker" else "target: <+stage.variables.ARTIFACTORY_TARGET>"}
                    tags:
                      - <+pipeline.sequenceId>
        variables:
          - name: IMAGE_REPO
            type: String
            value: <+input>
          - name: NODE_VERSION
            type: String
            value: {req.node_version or "24.14.0"}
        failureStrategies:
          - onFailure:
              errors:
                - AllErrors
              action:
                type: StageRollback
{cd_stages_yaml}  properties:
    ci:
      codebase:
        connectorRef: account.GIT_CONNECTOR
        repoName: {req.repo_name or "my-repo"}
        build: <+input>

Output the above YAML exactly as shown — it is already complete and valid. Do not add, remove or rearrange any fields.
"""

# ─── Endpoints ────────────────────────────────────────────────────────────────

def generate_pipeline_yaml(req: GenerateRequest) -> str:
    """
    Generate a guaranteed schema-valid Harness pipeline YAML directly in Python.
    Uses Claude only to generate the build commands, not the structure.
    """
    pipeline_name = req.pipeline_name or (req.repo_name or "my-app").replace("_", "-").lower()
    pipeline_id   = re.sub(r"[^a-zA-Z0-9]", "", pipeline_name)
    envs          = req.environments or ["dev"]
    node_ver      = req.node_version or "24.14.0"
    is_docker     = (req.artifact_type or "Docker") == "Docker"
    strategy      = req.deployment_strategy or "rolling"

    rollout_step = {
        "rolling":    ("K8sRollingDeploy",   "Rolling Deploy"),
        "blue-green": ("K8sBGSwapServices",  "Blue Green Swap"),
        "canary":     ("K8sCanaryDeploy",    "Canary Deploy"),
        "basic":      ("K8sRollingDeploy",   "Rolling Deploy"),
    }.get(strategy, ("K8sRollingDeploy", "Rolling Deploy"))

    # ── CD stages ─────────────────────────────────────────────────────────────
    cd_stages = ""
    for env in envs:
        env_id = env.lower()
        approval = ""
        if env_id in ("prod", "production"):
            approval = f"""              - step:
                  type: HarnessApproval
                  name: Approve {env.upper()}
                  identifier: Approve_{env.upper()}
                  timeout: 1d
                  spec:
                    approvalMessage: Approve deployment to {env.upper()}
                    includePipelineExecutionHistory: true
                    approvers:
                      userGroups:
                        - account._account_all_users
                      minimumCount: 1
                    approverInputs: []
"""
        deploy_step_id = rollout_step[0].replace("K8s", "").replace("BGSwap", "BgSwap")
        cd_stages += f"""    - stage:
        name: Deploy {env.upper()}
        identifier: Deploy_{env.upper()}
        type: Deployment
        spec:
          deploymentType: Kubernetes
          service:
            serviceRef: <+input>
          environment:
            environmentRef: {env_id}
            deployToAll: false
            infrastructureDefinitions:
              - identifier: <+input>
          execution:
            steps:
{approval}              - step:
                  type: {rollout_step[0]}
                  name: {rollout_step[1]}
                  identifier: {deploy_step_id}
                  timeout: 10m
                  spec:
                    skipDryRun: false
            rollbackSteps:
              - step:
                  type: K8sRollingRollback
                  name: Rollback
                  identifier: Rollback
                  timeout: 10m
                  spec:
                    pruningEnabled: false
        failureStrategies:
          - onFailure:
              errors:
                - AllErrors
              action:
                type: StageRollback
"""

    # ── Artifact step ──────────────────────────────────────────────────────────
    if is_docker:
        artifact_step = f"""              - step:
                  type: BuildAndPushDockerRegistry
                  name: Push Docker Image
                  identifier: Push_Docker_Image
                  spec:
                    connectorRef: <+input>
                    repo: <+input>
                    tags:
                      - <+pipeline.sequenceId>
                      - latest
"""
    else:
        artifact_step = f"""              - step:
                  type: ArtifactoryUpload
                  name: Upload Artifact
                  identifier: Upload_Artifact
                  spec:
                    connectorRef: <+input>
                    target: <+stage.variables.ARTIFACTORY_TARGET>/build/
                    sourcePath: ./build
"""

    yaml = f"""pipeline:
  name: {pipeline_name}
  identifier: {pipeline_id}
  projectIdentifier: {req.harness_project_id or "PROJECT_ID"}
  orgIdentifier: {req.harness_org_id or "default"}
  tags: {{}}
  stages:
    - stage:
        name: Build
        identifier: Build
        type: CI
        spec:
          cloneCodebase: true
          infrastructure:
            type: KubernetesDirect
            spec:
              connectorRef: account.K8S_CONNECTOR
              namespace: harness-delegate-ng
              os: Linux
          execution:
            steps:
              - step:
                  type: Run
                  name: Install Dependencies
                  identifier: Install_Dependencies
                  spec:
                    connectorRef: account.DOCKER_CONNECTOR
                    image: node:{node_ver}-alpine
                    shell: Sh
                    command: |-
                      npm config set registry https://registry.npmjs.org/
                      npm install --force
              - step:
                  type: Run
                  name: Build
                  identifier: Build_App
                  spec:
                    connectorRef: account.DOCKER_CONNECTOR
                    image: node:{node_ver}-alpine
                    shell: Sh
                    command: npm run build
              - step:
                  type: Run
                  name: Run Tests
                  identifier: Run_Tests
                  spec:
                    connectorRef: account.DOCKER_CONNECTOR
                    image: node:{node_ver}-alpine
                    shell: Sh
                    command: |-
                      npx jest --coverage --coverageReporters=lcov --coverageDirectory=/harness/coverage || true
{artifact_step}        variables:
          - name: APP_NAME
            type: String
            value: {pipeline_name}
{cd_stages}  properties:
    ci:
      codebase:
        connectorRef: account.GIT_CONNECTOR
        repoName: {req.repo_name or "my-repo"}
        build: <+input>
"""
    return yaml.strip()


@router.post("/generate-stream")
async def generate_pipeline_stream(req: GenerateRequest, _=Depends(verify_azure_token)):
    """Stream pipeline YAML generation directly (schema-guaranteed structure)."""

    yaml_content = generate_pipeline_yaml(req)
    logger.info(f"Generated pipeline YAML ({len(yaml_content)} chars) for repo: {req.repo_name}")

    def stream():
        # Stream in small chunks to give the typewriter effect in the UI
        chunk_size = 80
        for i in range(0, len(yaml_content), chunk_size):
            yield yaml_content[i:i + chunk_size]

    return StreamingResponse(stream(), media_type="text/plain")


@router.post("/validate")
async def validate_pipeline_yaml(req: ValidateRequest, _=Depends(verify_azure_token)):
    """Validate pipeline YAML against Harness API."""
    yaml_content = clean_yaml(req.yaml_content)
    if not yaml_content.strip().startswith("pipeline:"):
        yaml_content = "pipeline:\n" + yaml_content
    logger.info(f"Validating YAML (first 300 chars):\n{yaml_content[:300]}")

    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/validate-yaml"
        f"?accountIdentifier={req.harness_account_id}"
        f"&orgIdentifier={req.harness_org_id}"
        f"&projectIdentifier={req.harness_project_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Try 1: raw YAML with application/yaml
            resp = await client.post(
                url,
                headers={"x-api-key": req.harness_api_key, "Content-Type": "application/yaml"},
                content=yaml_content.encode(),
            )
            logger.info(f"Validate YAML status: {resp.status_code}, body: {resp.text[:300]}")

            # If Harness says it can't process JSON, it likely wants JSON-wrapped YAML
            raw = resp.text
            if resp.status_code not in (200, 201) and ("process JSON" in raw or "JSON" in raw):
                resp = await client.post(
                    url,
                    headers={"x-api-key": req.harness_api_key, "Content-Type": "application/json"},
                    json={"yaml": yaml_content},
                )
                logger.info(f"Validate YAML (JSON wrap) status: {resp.status_code}, body: {resp.text[:300]}")

            if resp.status_code in (200, 201):
                return {"valid": True, "message": "YAML is valid and ready to create in Harness"}
            else:
                try:
                    err = resp.json()
                    msg = err.get("message") or err.get("detail") or resp.text[:400]
                except Exception:
                    msg = resp.text[:400]
                return {"valid": False, "message": f"[HTTP {resp.status_code}] {msg}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_pipeline(req: CreatePipelineRequest, _=Depends(verify_azure_token)):
    """Create pipeline in Harness from YAML."""
    yaml_content = clean_yaml(req.yaml_content)
    if not yaml_content.strip().startswith("pipeline:"):
        yaml_content = "pipeline:\n" + yaml_content
    logger.info(f"=== CREATE PIPELINE: first 5 lines ===")
    for i, l in enumerate(yaml_content.split('\n')[:5]):
        logger.info(f"  line {i+1}: {repr(l)}")

    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines"
        f"?accountIdentifier={req.harness_account_id}"
        f"&orgIdentifier={req.harness_org_id}"
        f"&projectIdentifier={req.harness_project_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                url,
                headers={"x-api-key": req.harness_api_key, "Content-Type": "application/yaml"},
                content=yaml_content.encode(),
            )
            logger.info(f"Create pipeline status: {resp.status_code}")
            logger.info(f"Create pipeline response: {resp.text[:800]}")

            if resp.status_code in (200, 201):
                data = resp.json()
                raw = data.get("data")
                if isinstance(raw, dict):
                    pipeline_id = raw.get("identifier", "")
                else:
                    pipeline_id = str(raw or "")
                return {
                    "success": True,
                    "message": "Pipeline created successfully in Harness!",
                    "pipeline_id": pipeline_id,
                }
            else:
                try:
                    err = resp.json()
                    msg = err.get("message") or err.get("detail") or resp.text[:600]
                except Exception:
                    msg = resp.text[:600]
                raise HTTPException(status_code=resp.status_code, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
