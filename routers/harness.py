"""
Harness CI/CD Proxy Router
Proxies requests to the Harness API to avoid CORS issues from the browser.
All calls to app.harness.io are made server-side.
"""

import logging
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from auth import verify_azure_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/harness", tags=["harness"])

HARNESS_BASE = "https://app.harness.io"


# ─── Auth dependency ──────────────────────────────────────────────────────────

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    user_id = token_data.get("oid") or token_data.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token_data


# ─── Request Models ───────────────────────────────────────────────────────────

class HarnessQueryRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: Optional[str] = "default"
    project_id: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def harness_headers(api_key: str) -> dict:
    # Strip any whitespace/newlines that may have been included when copying the token
    clean_key = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    return {
        "x-api-key": clean_key,
        "Content-Type": "application/json",
    }


def _parse_harness_error(text: str) -> str:
    """Extract a clean message from Harness error response JSON."""
    try:
        import json
        data = json.loads(text)
        msg = data.get("message") or data.get("detail") or text
        return str(msg)
    except Exception:
        return text


async def harness_get(api_key: str, url: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=harness_headers(api_key))
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))
        return resp.json()


async def harness_post(api_key: str, url: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=harness_headers(api_key), json=body)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))
        return resp.json()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/account")
async def get_account(req: HarnessQueryRequest, _=Depends(get_current_user)):
    data = await harness_get(
        req.api_key,
        f"{HARNESS_BASE}/ng/api/accounts/{req.account_id}"
    )
    return data.get("data", data)


@router.post("/organizations")
async def list_organizations(req: HarnessQueryRequest, _=Depends(get_current_user)):
    data = await harness_get(
        req.api_key,
        f"{HARNESS_BASE}/ng/api/organizations?accountIdentifier={req.account_id}&pageSize=50"
    )
    content = data.get("data", {}).get("content", [])
    return [item["organization"] for item in content]


@router.post("/projects")
async def list_projects(req: HarnessQueryRequest, _=Depends(get_current_user)):
    # If no org specified, fetch projects across ALL orgs in the account
    if req.org_id:
        url = f"{HARNESS_BASE}/ng/api/projects?accountIdentifier={req.account_id}&orgIdentifier={req.org_id}&pageSize=50"
    else:
        url = f"{HARNESS_BASE}/ng/api/projects?accountIdentifier={req.account_id}&pageSize=50"
    data = await harness_get(req.api_key, url)
    content = data.get("data", {}).get("content", [])
    return [item["project"] for item in content]


@router.post("/pipelines")
async def list_pipelines(req: HarnessQueryRequest, _=Depends(get_current_user)):
    if not req.project_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    # If org_id not provided, look it up from the project to get the correct org
    if req.org_id:
        org = req.org_id
    else:
        projects_data = await harness_get(
            req.api_key,
            f"{HARNESS_BASE}/ng/api/projects?accountIdentifier={req.account_id}&pageSize=50"
        )
        projects = [i["project"] for i in projects_data.get("data", {}).get("content", [])]
        match = next((p for p in projects if p["identifier"] == req.project_id), None)
        org = match["orgIdentifier"] if match else "default"

    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/list"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={org}"
        f"&projectIdentifier={req.project_id}"
    )
    data = await harness_post(req.api_key, url, {"filterType": "PipelineSetup"})
    return data.get("data", {}).get("content", [])


class PipelineDetailRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: Optional[str] = "default"
    project_id: str
    pipeline_id: str


@router.post("/pipeline-detail")
async def get_pipeline_detail(req: PipelineDetailRequest, _=Depends(get_current_user)):
    org = req.org_id or "default"
    # Fetch pipeline metadata + YAML
    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/{req.pipeline_id}"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={org}"
        f"&projectIdentifier={req.project_id}"
    )
    data = await harness_get(req.api_key, url)
    pipeline_data = data.get("data", {})

    # Fetch last 5 executions for this pipeline
    exec_url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/execution/summary"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={org}"
        f"&projectIdentifier={req.project_id}"
        f"&pipelineIdentifier={req.pipeline_id}"
        f"&pageSize=5"
    )
    exec_data = await harness_post(req.api_key, exec_url, {"filterType": "PipelineExecution"})
    executions = exec_data.get("data", {}).get("content", [])

    return {
        "metadata": {
            "identifier": pipeline_data.get("identifier"),
            "name": pipeline_data.get("name"),
            "description": pipeline_data.get("description"),
            "tags": pipeline_data.get("tags", {}),
            "storeType": pipeline_data.get("storeType"),
            "created": pipeline_data.get("createdAt"),
            "updated": pipeline_data.get("lastUpdatedAt"),
        },
        "yaml": pipeline_data.get("yamlPipeline") or pipeline_data.get("yaml", ""),
        "recent_executions": executions,
    }


@router.post("/executions")
async def list_executions(req: HarnessQueryRequest, _=Depends(get_current_user)):
    if not req.project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    org = req.org_id or "default"
    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/execution/summary"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={org}"
        f"&projectIdentifier={req.project_id}"
        f"&size=30"
        f"&page=0"
    )
    data = await harness_post(req.api_key, url, {"filterType": "PipelineExecution"})
    return data.get("data", {}).get("content", [])


class ExecutionDetailRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: Optional[str] = "default"
    project_id: str
    execution_id: str  # planExecutionId


def _extract_failed_nodes(node: dict, failed: list):
    """Recursively walk execution graph and collect failed/errored nodes."""
    if not node:
        return
    status = node.get("status", "")
    if status in ("FAILED", "ERRORED", "ABORTED", "EXPIRED"):
        failed.append({
            "name": node.get("name") or node.get("identifier", ""),
            "identifier": node.get("identifier", ""),
            "type": node.get("stepType") or node.get("type", ""),
            "status": status,
            "startTs": node.get("startTs"),
            "endTs": node.get("endTs"),
            "failureInfo": node.get("failureInfo", {}),
            "errorMessage": (node.get("failureInfo") or {}).get("message", ""),
        })
    # Recurse into children
    for child in (node.get("children") or []):
        _extract_failed_nodes(child, failed)
    if node.get("next"):
        _extract_failed_nodes(node["next"], failed)


class AidaGenerateRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: Optional[str] = "default"
    project_id: Optional[str] = ""
    prompt: str  # e.g. "Kubernetes rolling deployment for Node.js app"


@router.post("/aida/generate-pipeline")
async def aida_generate_pipeline(req: AidaGenerateRequest, _=Depends(get_current_user)):
    """
    Probe multiple Harness AIDA pipeline generation endpoints/methods
    to find what is accessible on this account.
    """
    candidates = [
        # (method, url, body_or_params)
        ("POST", f"{HARNESS_BASE}/pm/api/v1/aida/pipeline/generate", {
            "account_id": req.account_id, "org_id": req.org_id or "default",
            "project_id": req.project_id, "prompt": req.prompt,
        }),
        ("POST", f"{HARNESS_BASE}/gateway/pm/api/v1/aida/pipeline/generate", {
            "account_id": req.account_id, "org_id": req.org_id or "default",
            "project_id": req.project_id, "prompt": req.prompt,
        }),
        ("POST", f"{HARNESS_BASE}/pm/api/v1/aida/chat", {
            "account_id": req.account_id, "prompt": req.prompt,
        }),
        ("POST", f"{HARNESS_BASE}/pm/api/v1/aida/generate", {
            "account_id": req.account_id, "prompt": req.prompt,
        }),
        ("GET",  f"{HARNESS_BASE}/pm/api/v1/aida/pipeline/generate", None),
    ]

    results = []
    async with httpx.AsyncClient(timeout=20) as client:
        for method, url, payload in candidates:
            try:
                if method == "GET":
                    resp = await client.get(url, headers=harness_headers(req.api_key))
                else:
                    resp = await client.post(url, headers=harness_headers(req.api_key), json=payload)
                ct = resp.headers.get("content-type", "")
                try:
                    body_parsed = resp.json()
                except Exception:
                    body_parsed = resp.text[:300]
                results.append({
                    "method": method,
                    "url": url,
                    "status_code": resp.status_code,
                    "response": body_parsed,
                })
            except Exception as e:
                results.append({"method": method, "url": url, "error": str(e)})

    return {"probed": results}


@router.post("/execution-logs")
async def get_execution_logs(req: ExecutionDetailRequest, _=Depends(get_current_user)):
    org = req.org_id or "default"

    # Fetch full execution detail with graph
    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/execution/{req.execution_id}"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={org}"
        f"&projectIdentifier={req.project_id}"
        f"&renderFullBottomGraph=true"
    )
    data = await harness_get(req.api_key, url)
    exec_data = data.get("data", {})

    # Extract top-level info
    s = exec_data.get("pipelineExecutionSummary", {})
    result = {
        "executionId": req.execution_id,
        "pipelineIdentifier": s.get("pipelineIdentifier", ""),
        "pipelineName": s.get("name", ""),
        "status": s.get("status", ""),
        "startTs": s.get("startTs"),
        "endTs": s.get("endTs"),
        "triggerType": s.get("executionTriggerInfo", {}).get("triggerType", ""),
        "triggeredBy": s.get("executionTriggerInfo", {}).get("triggeredBy", {}).get("identifier", ""),
        "errorMessage": s.get("errorMessage", ""),
        "failed_nodes": [],
        "stages": [],
    }

    # Walk execution graph for failed nodes
    graph = exec_data.get("executionGraph", {})
    node_map = graph.get("nodeMap", {})

    # Collect all stages with their status
    for node in node_map.values():
        node_type = node.get("stepType") or node.get("baseFqn", "")
        if node.get("nodeType") == "stage" or "STAGE" in str(node_type).upper():
            result["stages"].append({
                "name": node.get("name", ""),
                "identifier": node.get("identifier", ""),
                "status": node.get("status", ""),
                "startTs": node.get("startTs"),
                "endTs": node.get("endTs"),
                "failureInfo": node.get("failureInfo", {}),
                "errorMessage": (node.get("failureInfo") or {}).get("message", ""),
            })
        # Collect failed steps
        if node.get("status") in ("FAILED", "ERRORED", "ABORTED"):
            result["failed_nodes"].append({
                "name": node.get("name", ""),
                "identifier": node.get("identifier", ""),
                "type": node.get("stepType", ""),
                "status": node.get("status", ""),
                "startTs": node.get("startTs"),
                "endTs": node.get("endTs"),
                "errorMessage": (node.get("failureInfo") or {}).get("message", ""),
                "failureType": (node.get("failureInfo") or {}).get("failureTypeList", []),
            })

    return result


# ─── IDP (Internal Developer Portal) ─────────────────────────────────────────

class IdpRequest(BaseModel):
    api_key: str
    account_id: str


def _idp_headers(api_key: str, account_id: str) -> dict:
    """Harness IDP needs both x-api-key and Harness-Account header."""
    clean = api_key.strip().replace('\n', '').replace('\r', '').replace(' ', '')
    return {
        "x-api-key": clean,
        "Harness-Account": account_id,
        "Content-Type": "application/json",
    }


def _idp_catalog_urls(account_id: str) -> list:
    """All known Harness IDP catalog endpoint patterns."""
    return [
        # idp-service via gateway (most common in SaaS)
        f"https://app.harness.io/gateway/idp-service/api/v1/catalog/entities?harnessAccount={account_id}&page=0&size=100",
        f"https://app.harness.io/gateway/idp-service/api/catalog/entities?harnessAccount={account_id}&limit=100",
        # Direct idp-service
        f"https://app.harness.io/idp-service/api/v1/catalog/entities?harnessAccount={account_id}&page=0&size=100",
        # idp subdomain
        f"https://app.harness.io/idp/api/catalog/entities?accountIdentifier={account_id}&limit=100",
        f"https://app.harness.io/idp/api/v1/catalog/entities?accountIdentifier={account_id}&limit=100",
        # ng API
        f"https://app.harness.io/ng/api/idp/catalog/entities?accountIdentifier={account_id}&limit=100",
    ]


@router.post("/idp/catalog")
async def idp_list_catalog(req: IdpRequest, _=Depends(get_current_user)):
    """List all entities from the Harness IDP software catalog."""
    hdrs = _idp_headers(req.api_key, req.account_id)
    async with httpx.AsyncClient(timeout=20) as client:
        for url in _idp_catalog_urls(req.account_id):
            try:
                resp = await client.get(url, headers=hdrs)
                logger.info(f"IDP catalog: GET {url} → {resp.status_code} | {resp.text[:120]}")
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("items", data.get("data", data.get("entities", [])))
                    return {"entities": items}
            except Exception as e:
                logger.warning(f"IDP catalog error {url}: {e}")
    return {"entities": [], "error": "IDP catalog endpoint not reachable. Ensure IDP is enabled on your Harness account."}


@router.post("/idp/scorecards")
async def idp_list_scorecards(req: IdpRequest, _=Depends(get_current_user)):
    """List scorecards from Harness IDP."""
    hdrs = _idp_headers(req.api_key, req.account_id)
    async with httpx.AsyncClient(timeout=20) as client:
        for url in [
            f"https://app.harness.io/gateway/idp-service/api/v1/scorecards?harnessAccount={req.account_id}",
            f"https://app.harness.io/idp-service/api/v1/scorecards?harnessAccount={req.account_id}",
            f"https://app.harness.io/gateway/idp-service/api/v1/scorecards/checks/scores?harnessAccount={req.account_id}",
            f"https://app.harness.io/idp/api/v1/scorecards?accountIdentifier={req.account_id}",
        ]:
            try:
                resp = await client.get(url, headers=hdrs)
                logger.info(f"IDP scorecards: GET {url} → {resp.status_code} | {resp.text[:120]}")
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("items", data.get("data", data.get("scorecards", [])))
                    return {"scorecards": items}
            except Exception as e:
                logger.warning(f"IDP scorecards error {url}: {e}")
    return {"scorecards": [], "error": "IDP scorecards not reachable."}


@router.post("/idp/workflows")
async def idp_list_workflows(req: IdpRequest, _=Depends(get_current_user)):
    """List self-service workflows (templates) from Harness IDP."""
    hdrs = _idp_headers(req.api_key, req.account_id)
    async with httpx.AsyncClient(timeout=20) as client:
        for url in [
            f"https://app.harness.io/gateway/idp-service/api/v1/workflows?harnessAccount={req.account_id}",
            f"https://app.harness.io/idp-service/api/v1/workflows?harnessAccount={req.account_id}",
            # Templates are a catalog kind in Backstage-based IDP
            f"https://app.harness.io/gateway/idp-service/api/v1/catalog/entities?harnessAccount={req.account_id}&filter=kind=Template&page=0&size=50",
            f"https://app.harness.io/idp/api/v1/catalog/entities?accountIdentifier={req.account_id}&filter=kind%3DTemplate&limit=50",
        ]:
            try:
                resp = await client.get(url, headers=hdrs)
                logger.info(f"IDP workflows: GET {url} → {resp.status_code} | {resp.text[:120]}")
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("items", data.get("data", data.get("workflows", [])))
                    return {"workflows": items}
            except Exception as e:
                logger.warning(f"IDP workflows error {url}: {e}")
    return {"workflows": [], "error": "IDP workflows not reachable."}


@router.post("/idp/entity")
async def idp_get_entity(req: IdpRequest, kind: str, namespace: str, name: str, _=Depends(get_current_user)):
    """Fetch a single catalog entity detail."""
    hdrs = harness_headers(req.api_key)
    async with httpx.AsyncClient(timeout=15) as client:
        url = f"{IDP_BASE}/api/v1/catalog/entities/by-name/{kind}/{namespace}/{name}?accountIdentifier={req.account_id}&harnessAccount={req.account_id}"
        resp = await client.get(url, headers=hdrs)
        if resp.status_code == 200:
            return resp.json()
        raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))


# ─── Create Harness Code Repository ───────────────────────────────────────────

class CreateRepoRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: Optional[str] = "default"
    project_id: Optional[str] = None
    repo_name: str
    description: Optional[str] = ""
    default_branch: Optional[str] = "main"
    is_public: Optional[bool] = False


@router.post("/create-repo")
async def create_harness_repo(req: CreateRepoRequest, _=Depends(verify_azure_token)):
    """
    Create a new Harness Code repository via the Harness Code API.
    Proxied through the backend to avoid CORS issues.
    """
    # Build parent_ref: account/org/project or account/org or account
    parts = [req.account_id]
    if req.org_id:
        parts.append(req.org_id)
    if req.project_id:
        parts.append(req.project_id)
    parent_ref = "/".join(parts)

    code_base = "https://app.harness.io/code/api/v1"
    headers = harness_headers(req.api_key)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{code_base}/repos",
            headers=headers,
            json={
                "identifier": req.repo_name.strip(),
                "parent_ref": parent_ref,
                "default_branch": req.default_branch or "main",
                "description": req.description or "",
                "is_public": req.is_public,
            },
        )

    if resp.status_code == 401:
        raise HTTPException(status_code=400, detail="Invalid Harness API key. Check the token in Settings.")
    if resp.status_code == 403:
        raise HTTPException(
            status_code=400,
            detail=(
                "Permission denied (403). Your Harness PAT token does not have Code Repository write access. "
                "Go to Harness → My Profile → API Keys → edit your token → enable 'Code' scope with 'Write' permission, then regenerate."
            ),
        )
    if resp.status_code == 409:
        raise HTTPException(status_code=400, detail=f"Repository '{req.repo_name}' already exists.")
    if resp.status_code not in (200, 201):
        try:
            body = resp.json()
            detail = body.get("message") or body.get("error") or resp.text[:300]
        except Exception:
            detail = resp.text[:300]
        raise HTTPException(status_code=400, detail=f"Harness API error ({resp.status_code}): {detail}")

    # Build the browser URL for the new repo
    org = req.org_id or "default"
    proj = req.project_id or ""
    repo_url = (
        f"https://app.harness.io/ng/account/{req.account_id}/module/code"
        f"/orgs/{org}/projects/{proj}/repos/{req.repo_name}"
        if proj else
        f"https://app.harness.io/ng/account/{req.account_id}/module/code"
        f"/orgs/{org}/repos/{req.repo_name}"
    )

    return {
        "success": True,
        "repo_name": req.repo_name,
        "repo_url": repo_url,
    }


# ─── Trigger Pipeline Execution ───────────────────────────────────────────────

class TriggerExecutionRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    pipeline_id: str
    branch: Optional[str] = "main"
    notes: Optional[str] = ""


def _build_codebase_yaml(pipeline_id: str, branch: str) -> str:
    """
    Build the inputSetYaml that passes codebase branch to a CI pipeline.
    Harness expects the pipeline identifier in the body when using inputSetYaml.
    """
    return (
        f"pipeline:\n"
        f"  identifier: {pipeline_id}\n"
        f"  properties:\n"
        f"    ci:\n"
        f"      codebase:\n"
        f"        build:\n"
        f"          type: branch\n"
        f"          spec:\n"
        f"            branch: {branch}\n"
    )


def _build_codebase_yaml_v2(branch: str) -> str:
    """
    Alternative format — no pipeline wrapper, just the input values.
    Some Harness versions expect this flat format.
    """
    return (
        f"properties:\n"
        f"  ci:\n"
        f"    codebase:\n"
        f"      build:\n"
        f"        type: branch\n"
        f"        spec:\n"
        f"          branch: {branch}\n"
    )


@router.post("/trigger-execution")
async def trigger_execution(req: TriggerExecutionRequest, _=Depends(get_current_user)):
    import json as _json
    import urllib.parse as _up

    pid = req.pipeline_id
    branch = req.branch or "main"
    base_params = (
        f"accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
    )
    notes_param = f"&notesForPipelineExecution={_up.quote(req.notes)}" if req.notes else ""
    codebase_yaml = _build_codebase_yaml(pid, branch)
    base_hdrs = harness_headers(req.api_key)

    async with httpx.AsyncClient(timeout=30) as client:

        # ── Step 1: fetch pipeline detail to know storeType + git details ─────
        pipeline_branch = branch   # branch where pipeline YAML lives in git
        pipeline_repo   = ""
        pipeline_connector = ""
        store_type = "INLINE"

        try:
            detail_url = (
                f"{HARNESS_BASE}/pipeline/api/pipelines/{pid}"
                f"?accountIdentifier={req.account_id}"
                f"&orgIdentifier={req.org_id}"
                f"&projectIdentifier={req.project_id}"
            )
            detail_resp = await client.get(detail_url, headers=base_hdrs)
            if detail_resp.status_code == 200:
                d = detail_resp.json().get("data", {})
                store_type      = d.get("storeType", "INLINE")
                git_details     = d.get("gitDetails", {}) or {}
                pipeline_branch = git_details.get("branch") or branch
                pipeline_repo   = git_details.get("repoName") or git_details.get("repoIdentifier") or ""
                pipeline_connector = git_details.get("connectorRef") or ""
                logger.info(f"Pipeline storeType={store_type} git branch={pipeline_branch} repo={pipeline_repo}")
        except Exception as e:
            logger.warning(f"Could not fetch pipeline detail: {e}")

        # ── Step 2: build candidate list based on storeType ───────────────────
        # For REMOTE pipelines the run URL needs pipelineBranch + repoID params
        # so Harness can locate the YAML in Git before executing.
        remote_extra = ""
        remote_hdrs  = {}
        if store_type == "REMOTE" and pipeline_branch:
            remote_extra = f"&pipelineBranch={_up.quote(pipeline_branch)}"
            if pipeline_repo:
                remote_extra += f"&pipelineRepoID={_up.quote(pipeline_repo)}"
            if pipeline_connector:
                remote_extra += f"&connectorRef={_up.quote(pipeline_connector)}"
            remote_hdrs = {"Harness-Entity-Git-Branch": pipeline_branch}

        # Two possible base paths — Harness versions differ on /execute vs /execution/run
        execute_bases = [
            f"{HARNESS_BASE}/pipeline/api/pipelines/execute",           # older NG / most common
            f"{HARNESS_BASE}/pipeline/api/pipelines/execution/run",     # newer NG
            f"{HARNESS_BASE}/gateway/pipeline/api/pipelines/execute",   # via gateway
        ]

        # Each tuple: (url, extra_headers, content_type, body)
        candidates = []
        for base in execute_bases:
            # With codebase YAML (CI pipelines need branch input)
            candidates += [
                (f"{base}/{pid}?{base_params}{remote_extra}{notes_param}",
                 remote_hdrs, "application/yaml", codebase_yaml.encode()),
                (f"{base}/{pid}?{base_params}{remote_extra}&moduleType=ci{notes_param}",
                 remote_hdrs, "application/yaml", codebase_yaml.encode()),
                # No remote params (inline)
                (f"{base}/{pid}?{base_params}&moduleType=ci{notes_param}",
                 {}, "application/yaml", codebase_yaml.encode()),
                (f"{base}/{pid}?{base_params}{notes_param}",
                 {}, "application/yaml", codebase_yaml.encode()),
                # JSON body variant
                (f"{base}/{pid}?{base_params}{notes_param}",
                 {}, "application/json", _json.dumps({"inputSetPipelineYaml": codebase_yaml}).encode()),
                # Empty body — for pipelines with no runtime inputs needed
                (f"{base}/{pid}?{base_params}{notes_param}",
                 {}, "application/yaml", b""),
            ]

        for url, extra_hdrs, ct, body in candidates:
            hdrs = {**base_hdrs, "Content-Type": ct, **extra_hdrs}
            resp = await client.post(url, headers=hdrs, content=body)
            logger.info(f"Trigger [{store_type}/{ct.split('/')[-1]}] → {resp.status_code} | {resp.text[:300]}")

            if resp.status_code in (200, 201):
                data = resp.json()
                execution_id = (data.get("data") or {}).get("planExecutionId", "")
                return {"execution_id": execution_id, "status": "triggered"}

            if resp.status_code in (401, 403):
                raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))

    raise HTTPException(
        status_code=400,
        detail=(
            f"Could not trigger pipeline '{pid}' (storeType={store_type}). "
            "All execution endpoints returned 4xx — see backend logs for Harness error details."
        )
    )


@router.post("/trigger-probe")
async def trigger_probe(req: TriggerExecutionRequest, _=Depends(get_current_user)):
    """
    Diagnostic endpoint — tries every known trigger URL and returns
    all status codes + full response bodies so we can see what Harness says.
    """
    import json as _json
    pid = req.pipeline_id
    branch = req.branch or "main"
    base_params = (
        f"accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
    )
    codebase_yaml = _build_codebase_yaml(pid, branch)
    base_hdrs = harness_headers(req.api_key)

    probe_urls = [
        # /execute/ path (older Harness NG — most likely correct)
        f"{HARNESS_BASE}/pipeline/api/pipelines/execute/{pid}?{base_params}",
        f"{HARNESS_BASE}/pipeline/api/pipelines/execute/{pid}?{base_params}&moduleType=ci",
        f"{HARNESS_BASE}/gateway/pipeline/api/pipelines/execute/{pid}?{base_params}",
        # /execution/run/ path (newer Harness NG)
        f"{HARNESS_BASE}/pipeline/api/pipelines/execution/run/{pid}?{base_params}",
        f"{HARNESS_BASE}/gateway/pipeline/api/pipelines/execution/run/{pid}?{base_params}",
        # v1 API
        f"{HARNESS_BASE}/v1/orgs/{req.org_id}/projects/{req.project_id}/pipelines/{pid}/executions?accountIdentifier={req.account_id}",
    ]

    codebase_yaml_v2 = _build_codebase_yaml_v2(branch)

    results = []
    async with httpx.AsyncClient(timeout=20) as client:
        for url in probe_urls:
            for ct, body, label in [
                ("application/yaml", codebase_yaml.encode(), "pipeline_wrapper_yaml"),
                ("application/yaml", codebase_yaml_v2.encode(), "flat_yaml"),
                ("application/yaml", b"", "empty"),
                ("application/json", _json.dumps({"inputSetPipelineYaml": codebase_yaml}).encode(), "json_wrapped"),
            ]:
                hdrs = {**base_hdrs, "Content-Type": ct}
                try:
                    resp = await client.post(url, headers=hdrs, content=body)
                    try:
                        parsed_resp = resp.json()
                    except Exception:
                        parsed_resp = resp.text
                    results.append({
                        "url": url.replace(f"accountIdentifier={req.account_id}", "accountIdentifier=***"),
                        "content_type": ct,
                        "body_sent": label,
                        "status": resp.status_code,
                        "response": parsed_resp,
                    })
                    if resp.status_code in (200, 201):
                        break  # found working combo
                except Exception as e:
                    results.append({"url": url, "content_type": ct, "error": str(e)})

    return {"pipeline_id": pid, "branch": branch, "results": results}


# ─── Fetch Pipeline Triggers (webhook URLs) ───────────────────────────────────

class PipelineTriggersRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    pipeline_id: str


@router.post("/pipeline-triggers")
async def get_pipeline_triggers(req: PipelineTriggersRequest, _=Depends(get_current_user)):
    """
    Fetch all configured triggers for a pipeline.
    Webhook triggers have a webhookUrl we can call directly to run the pipeline,
    bypassing RBAC execute permission requirements.
    """
    url = (
        f"{HARNESS_BASE}/pipeline/api/triggers"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
        f"&targetIdentifier={req.pipeline_id}"
        f"&pageSize=20"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=harness_headers(req.api_key))
        logger.info(f"Pipeline triggers: GET {url} → {resp.status_code}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))
        data = resp.json()
        triggers = data.get("data", {}).get("content", [])
        result = []
        for t in triggers:
            trigger = t.get("trigger", t)
            result.append({
                "identifier": trigger.get("identifier", ""),
                "name": trigger.get("name", ""),
                "type": trigger.get("type", ""),          # Webhook, Scheduled, Artifact
                "enabled": trigger.get("enabled", True),
                "webhookUrl": trigger.get("webhookUrl", ""),
                "webhookSecret": trigger.get("webhookSecret", ""),
            })
        return {"triggers": result}


class TriggerDetailRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    pipeline_id: str
    trigger_id: str


@router.post("/trigger-detail")
async def get_trigger_detail(req: TriggerDetailRequest, _=Depends(get_current_user)):
    """Fetch full YAML of a specific trigger."""
    url = (
        f"{HARNESS_BASE}/pipeline/api/triggers/{req.trigger_id}"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
        f"&targetIdentifier={req.pipeline_id}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=harness_headers(req.api_key))
        if r.status_code == 200:
            data = r.json().get("data", {})
            trigger = data.get("trigger", data)
            return {
                "yaml": trigger.get("yaml", ""),
                "name": trigger.get("name", ""),
                "type": trigger.get("type", ""),
                "enabled": trigger.get("enabled", True),
            }
        return {"yaml": "", "error": _parse_harness_error(r.text)}



@router.post("/pipeline-input-sets")
async def get_pipeline_input_sets(req: PipelineTriggersRequest, _=Depends(get_current_user)):
    """
    Fetch all input sets defined for a pipeline.
    Input sets are saved collections of runtime input values.
    """
    url = (
        f"{HARNESS_BASE}/pipeline/api/inputSets"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
        f"&pipelineIdentifier={req.pipeline_id}"
        f"&pageSize=20"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=harness_headers(req.api_key))
        if r.status_code == 200:
            items = r.json().get("data", {}).get("content", [])
            result = []
            for item in items:
                result.append({
                    "identifier": item.get("identifier"),
                    "name": item.get("name"),
                    "description": item.get("description", ""),
                    "inputSetType": item.get("inputSetType", "INPUT_SET"),
                    "tags": item.get("tags", {}),
                })
            return {"inputSets": result}
        return {"inputSets": [], "error": _parse_harness_error(r.text)}


class InputSetDetailRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    pipeline_id: str
    input_set_id: str


@router.post("/input-set-detail")
async def get_input_set_detail(req: InputSetDetailRequest, _=Depends(get_current_user)):
    """Fetch full YAML of a specific input set."""
    url = (
        f"{HARNESS_BASE}/pipeline/api/inputSets/{req.input_set_id}"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
        f"&pipelineIdentifier={req.pipeline_id}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=harness_headers(req.api_key))
        if r.status_code == 200:
            data = r.json().get("data", {})
            return {
                "yaml": data.get("inputSetYaml") or data.get("yaml", ""),
                "name": data.get("name", ""),
                "inputSetType": data.get("inputSetType", "INPUT_SET"),
            }
        return {"yaml": "", "error": _parse_harness_error(r.text)}


@router.post("/trigger-via-webhook")
async def trigger_via_webhook(req: PipelineTriggersRequest, branch: str = "main", _=Depends(get_current_user)):
    """
    Fire the pipeline's first enabled Custom/Manual webhook trigger.
    This bypasses RBAC execute permissions — only needs the webhook secret.
    """
    # First fetch triggers
    triggers_url = (
        f"{HARNESS_BASE}/pipeline/api/triggers"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
        f"&targetIdentifier={req.pipeline_id}"
        f"&pageSize=20"
    )
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(triggers_url, headers=harness_headers(req.api_key))
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))

        triggers = resp.json().get("data", {}).get("content", [])
        webhook_trigger = None
        for t in triggers:
            trigger = t.get("trigger", t)
            # Prefer Custom webhook triggers (can be fired manually)
            if trigger.get("type") in ("Webhook", "WEBHOOK") and trigger.get("enabled", True):
                if trigger.get("webhookUrl"):
                    webhook_trigger = trigger
                    break

        if not webhook_trigger:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No enabled webhook trigger found for this pipeline. "
                    "Go to Harness → pipeline → Triggers → add a Custom Webhook trigger, "
                    "or ask your admin to grant Pipeline Execute permission to your user."
                )
            )

        webhook_url = webhook_trigger["webhookUrl"]
        webhook_secret = webhook_trigger.get("webhookSecret", "")

        # Fire the webhook with branch payload
        wh_headers = {"Content-Type": "application/json"}
        if webhook_secret:
            wh_headers["X-Harness-Webhook-Secret"] = webhook_secret

        wh_body = {"branch": branch}
        wh_resp = await client.post(webhook_url, headers=wh_headers, json=wh_body)
        logger.info(f"Webhook trigger: POST {webhook_url} → {wh_resp.status_code} | {wh_resp.text[:200]}")

        if wh_resp.status_code in (200, 201):
            return {"status": "triggered", "method": "webhook", "trigger": webhook_trigger.get("name", "")}

        raise HTTPException(
            status_code=wh_resp.status_code,
            detail=f"Webhook trigger failed: {wh_resp.text[:300]}"
        )


# ─── Fetch Pipeline Codebase Info ─────────────────────────────────────────────

class PipelineCodebaseRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    pipeline_id: str


@router.post("/pipeline-codebase")
async def get_pipeline_codebase(req: PipelineCodebaseRequest, _=Depends(get_current_user)):
    """
    Fetch a pipeline's YAML and extract the codebase section so the frontend
    can display the connected repo/branch before the user triggers a run.
    """
    import yaml as _yaml

    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/{req.pipeline_id}"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
    )
    data = await harness_get(req.api_key, url)
    pipeline_data = data.get("data", {})
    raw_yaml = pipeline_data.get("yamlPipeline") or pipeline_data.get("yaml", "")

    result = {
        "connector_ref": "",
        "repo_name": "",
        "default_branch": "main",
        "build_type": "",        # "branch" | "tag" | "PR" | "<+input>" | ""
        "branch_value": "",      # actual value — may be "<+input>" or "<+trigger.branch>"
        "is_runtime_input": False,
        "store_type": pipeline_data.get("storeType", "INLINE"),
        "git_branch": "",        # pipeline YAML's own git branch (for REMOTE pipelines)
    }

    # For REMOTE pipelines, capture the git branch where the YAML lives
    git_details = pipeline_data.get("gitDetails", {}) or {}
    result["git_branch"] = git_details.get("branch", "")

    if not raw_yaml:
        return result

    try:
        parsed = _yaml.safe_load(raw_yaml)
        pipeline = parsed.get("pipeline", {})
        props = pipeline.get("properties", {})
        codebase = props.get("ci", {}).get("codebase", {})

        if codebase:
            result["connector_ref"] = codebase.get("connectorRef", "")
            result["repo_name"] = codebase.get("repoName", "")
            build = codebase.get("build", {})

            if isinstance(build, str):
                # Runtime input: "<+input>"
                result["build_type"] = "runtime_input"
                result["branch_value"] = build
                result["is_runtime_input"] = True
            elif isinstance(build, dict):
                result["build_type"] = build.get("type", "")
                spec = build.get("spec", {}) or {}
                branch_val = spec.get("branch") or spec.get("tag") or spec.get("number") or ""
                result["branch_value"] = str(branch_val)
                # Check if any value is a runtime input or expression
                is_runtime = str(branch_val).startswith("<+")
                result["is_runtime_input"] = is_runtime
                if not is_runtime and branch_val:
                    result["default_branch"] = str(branch_val)

        logger.info(f"Pipeline codebase: {result}")
    except Exception as e:
        logger.warning(f"Could not parse pipeline YAML for codebase info: {e}")

    return result


# ─── List Branches for a Pipeline's Connected Repo ───────────────────────────

class RepoBranchesRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    connector_ref: str
    repo_name: str


@router.post("/repo-branches")
async def list_repo_branches(req: RepoBranchesRequest, _=Depends(get_current_user)):
    """
    List Git branches for a repo connected via a Harness connector.
    Strategy:
      1. Fetch the Harness connector to get the GitHub base URL (org or repo-level)
      2. Build the full org/repo path and call GitHub API
      3. Fallback: try Harness gitBranches API candidates
    """
    def _normalise_branches(data: dict):
        raw = (
            data.get("data", {}).get("branches", [])
            or data.get("data", {}).get("content", [])
            or (data.get("data") if isinstance(data.get("data"), list) else [])
            or data.get("branches", [])
            or []
        )
        names = []
        for b in raw:
            if isinstance(b, str):
                names.append(b)
            elif isinstance(b, dict):
                names.append(b.get("name") or b.get("branchName") or "")
        return [n for n in names if n]

    async with httpx.AsyncClient(timeout=15) as client:
        hdrs = harness_headers(req.api_key)

        # ── Step 1: Fetch the Harness connector to extract GitHub base URL ────
        # connector_ref format: "account.ConnectorId" | "org.ConnectorId" | "ConnectorId"
        connector_id = req.connector_ref.split(".")[-1]
        github_base_url = ""
        connector_type = ""
        try:
            conn_url = (
                f"{HARNESS_BASE}/ng/api/connectors/{connector_id}"
                f"?accountIdentifier={req.account_id}"
                f"&orgIdentifier={req.org_id}"
                f"&projectIdentifier={req.project_id}"
            )
            conn_resp = await client.get(conn_url, headers=hdrs)
            logger.info(f"Connector fetch: GET {conn_url} → {conn_resp.status_code}")
            if conn_resp.status_code == 200:
                spec = conn_resp.json().get("data", {}).get("connector", {}).get("spec", {})
                github_base_url = spec.get("url", "").rstrip("/")
                connector_type = spec.get("type", "Account")  # "Account" | "Repo"
                logger.info(f"Connector spec url={github_base_url} type={connector_type}")
        except Exception as e:
            logger.warning(f"Connector fetch failed: {e}")

        # ── Step 2: Build full repo URL from connector base URL + repo name ─────
        repo_path = req.repo_name.strip("/")
        full_repo_url = ""
        full_repo_path = ""   # "org/repo" for GitHub API

        if github_base_url and "github.com" in github_base_url:
            if connector_type == "Repo":
                full_repo_url = github_base_url  # already the full repo URL
                parts = github_base_url.replace("https://github.com/", "").strip("/").split("/")
                full_repo_path = "/".join(parts[:2]) if len(parts) >= 2 else repo_path
            else:
                # Account-level connector: base = https://github.com/my-org
                org = github_base_url.replace("https://github.com/", "").strip("/")
                full_repo_url = f"https://github.com/{org}/{repo_path}" if org else ""
                full_repo_path = f"{org}/{repo_path}" if org else repo_path
        elif "/" in repo_path:
            full_repo_path = repo_path
            full_repo_url = f"https://github.com/{repo_path}"

        # ── Step 3: Harness SCM proxy — uses connector's stored GitHub token ──
        import urllib.parse
        scm_base = (
            f"accountIdentifier={req.account_id}"
            f"&orgIdentifier={req.org_id}"
            f"&projectIdentifier={req.project_id}"
        )
        scm_post_body = {
            "connectorRef": req.connector_ref,
            "repoName": repo_path,
            "orgIdentifier": req.org_id,
            "projectIdentifier": req.project_id,
        }

        for scm_url in [
            f"{HARNESS_BASE}/ng/api/scm/listBranches?routingId={req.account_id}&{scm_base}",
            f"{HARNESS_BASE}/gateway/ng/api/scm/listBranches?routingId={req.account_id}&{scm_base}",
            f"{HARNESS_BASE}/pipeline/api/scm/listBranches?{scm_base}",
            f"{HARNESS_BASE}/gateway/pipeline/api/scm/listBranches?{scm_base}",
        ]:
            try:
                resp = await client.post(scm_url, headers=hdrs, json=scm_post_body)
                logger.info(f"SCM listBranches: POST {scm_url} → {resp.status_code}")
                if resp.status_code == 200:
                    names = _normalise_branches(resp.json())
                    if names:
                        return {"branches": names}
            except Exception as e:
                logger.warning(f"SCM listBranches error {scm_url}: {e}")

        # Connector listBranches with full repo URL (uses connector creds)
        if full_repo_url:
            for conn_url in [
                f"{HARNESS_BASE}/ng/api/connectors/{connector_id}/listBranches?{scm_base}&repoURL={urllib.parse.quote(full_repo_url, safe='')}",
                f"{HARNESS_BASE}/gateway/ng/api/connectors/{connector_id}/listBranches?{scm_base}&repoURL={urllib.parse.quote(full_repo_url, safe='')}",
            ]:
                try:
                    resp = await client.get(conn_url, headers=hdrs)
                    logger.info(f"Connector listBranches: GET {conn_url} → {resp.status_code}")
                    if resp.status_code == 200:
                        names = _normalise_branches(resp.json())
                        if names:
                            return {"branches": names}
                except Exception as e:
                    logger.warning(f"Connector listBranches error: {e}")

        # ── Step 4: GitHub API (works for public repos, or if repo is public) ─
        if full_repo_path and "/" in full_repo_path:
            try:
                gh_url = f"https://api.github.com/repos/{full_repo_path}/branches?per_page=100"
                gh_resp = await client.get(gh_url, headers={"Accept": "application/vnd.github+json"})
                logger.info(f"GitHub branches API: GET {gh_url} → {gh_resp.status_code}")
                if gh_resp.status_code == 200:
                    names = [b["name"] for b in gh_resp.json() if isinstance(b, dict) and b.get("name")]
                    if names:
                        return {"branches": names}
            except Exception as e:
                logger.warning(f"GitHub branches error: {e}")

        # ── Step 5: Harness gitSync fallback ──────────────────────────────────
        qs = (
            f"accountIdentifier={req.account_id}"
            f"&orgIdentifier={req.org_id}"
            f"&projectIdentifier={req.project_id}"
            f"&connectorRef={req.connector_ref}"
            f"&repoName={req.repo_name}"
            f"&page=0&size=100"
        )
        for url in [
            f"{HARNESS_BASE}/ng/api/gitSync/gitBranches?{qs}",
            f"{HARNESS_BASE}/ng/api/connector/gitBranches?{qs}",
        ]:
            try:
                resp = await client.get(url, headers=hdrs)
                logger.info(f"Harness gitSync branches: GET {url} → {resp.status_code}")
                if resp.status_code == 200:
                    names = _normalise_branches(resp.json())
                    if names:
                        return {"branches": names}
            except Exception as e:
                logger.warning(f"Harness gitSync branch error: {e}")

    return {"branches": []}


# ─── Update Pipeline YAML ─────────────────────────────────────────────────────

class UpdatePipelineRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str
    pipeline_id: str
    yaml: str


@router.post("/update-pipeline")
async def update_pipeline(req: UpdatePipelineRequest, _=Depends(get_current_user)):
    url = (
        f"{HARNESS_BASE}/pipeline/api/pipelines/{req.pipeline_id}"
        f"?accountIdentifier={req.account_id}"
        f"&orgIdentifier={req.org_id}"
        f"&projectIdentifier={req.project_id}"
    )
    headers = harness_headers(req.api_key)
    headers["Content-Type"] = "application/yaml"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, headers=headers, content=req.yaml.encode())
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=_parse_harness_error(resp.text))
    return {"success": True, "message": "Pipeline updated successfully"}


# ─── AI Analyze Pipeline YAML ────────────────────────────────────────────────

class AiAnalyzePipelineRequest(BaseModel):
    api_key: str
    account_id: str
    yaml: str
    error_context: str


@router.post("/ai-analyze-pipeline")
async def ai_analyze_pipeline(req: AiAnalyzePipelineRequest, _=Depends(get_current_user)):
    import boto3
    import json as _json
    import re as _re

    MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    prompt = f"""You are a Harness CI/CD pipeline expert. Analyze this pipeline YAML and the error, then identify ONLY the minimal fix needed.

Pipeline YAML:
{req.yaml}

Error from failed execution:
{req.error_context}

Your task:
1. Detect pipeline type: Is this a CI pipeline (has stages with type: CI) or CD pipeline (has stages with type: Deployment)?
2. Find the EXACT failing step by matching the step name/identifier from the error.
3. Identify the root cause — look only at that specific step's fields (image, command, shell, connectorRef, etc).
4. Propose the minimal fix — only the field(s) that need to change.
5. Do NOT suggest changes to: connectorRef, templateRef, stage variables (<+stage.variables.*>), infrastructure, resource limits, or any other step.

CRITICAL JSON RULES:
- current_value and proposed_value MUST be short single-line strings (max 120 characters).
- If the value is a multi-line command, show ONLY the specific line that contains the error (e.g. "npn run build").
- For proposed_value show only the corrected line (e.g. "npm run build").
- Never include newlines, tabs, or unescaped quotes inside any JSON string value.
- Respond with ONLY valid JSON — no markdown fences, no explanation text before or after.

{{
  "pipeline_type": "CI",
  "failing_step": "exact step name from YAML",
  "failing_step_identifier": "exact identifier from YAML",
  "root_cause": "one sentence explaining what is wrong",
  "proposed_changes": [
    {{
      "field": "exact field path e.g. spec.command or spec.image",
      "current_value": "only the specific line with the error (single line, max 120 chars)",
      "proposed_value": "only the corrected line (single line, max 120 chars)",
      "reason": "why this change fixes the error"
    }}
  ]
}}"""

    try:
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
        payload = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = bedrock.invoke_model(modelId=MODEL_ID, body=payload)
        result = _json.loads(response["body"].read())
        text = result["content"][0]["text"].strip()

        # Strip markdown fences if present (```json ... ``` or ``` ... ```)
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line if it's ```
            start = 1
            end = len(lines)
            if lines[-1].strip() == "```" or lines[-1].strip() == "```json":
                end = len(lines) - 1
            text = "\n".join(lines[start:end]).strip()

        # Try direct parse first
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            pass

        # Sanitize: replace literal newlines/tabs inside JSON string values
        # Replace unescaped newlines between quotes with \n
        def _sanitize_json_strings(s: str) -> str:
            # Replace actual newlines/tabs that appear inside JSON strings with escaped versions
            result = []
            in_string = False
            escape_next = False
            for ch in s:
                if escape_next:
                    result.append(ch)
                    escape_next = False
                elif ch == '\\':
                    result.append(ch)
                    escape_next = True
                elif ch == '"':
                    result.append(ch)
                    in_string = not in_string
                elif in_string and ch == '\n':
                    result.append('\\n')
                elif in_string and ch == '\r':
                    result.append('\\r')
                elif in_string and ch == '\t':
                    result.append('\\t')
                else:
                    result.append(ch)
            return ''.join(result)

        sanitized = _sanitize_json_strings(text)
        try:
            return _json.loads(sanitized)
        except _json.JSONDecodeError:
            pass

        # Last resort: extract first JSON object by braces
        json_match = _re.search(r'\{[\s\S]*\}', sanitized)
        if json_match:
            try:
                return _json.loads(json_match.group())
            except _json.JSONDecodeError:
                pass

        raise ValueError(f"No JSON object found in model response. Raw text (first 500 chars): {text[:500]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


# ─── AI Edit Pipeline YAML ────────────────────────────────────────────────────

class AiEditPipelineRequest(BaseModel):
    api_key: str
    account_id: str
    yaml: str
    instruction: str


@router.post("/ai-edit-pipeline")
async def ai_edit_pipeline(req: AiEditPipelineRequest, _=Depends(get_current_user)):
    import boto3
    import json as _json
    import re as _re

    MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    prompt = f"""You are a Harness YAML pipeline expert performing a MINIMAL surgical fix.

STRICT RULES — you MUST follow all of these:

1. PRESERVE EVERYTHING — copy the entire YAML exactly as-is. Only change the specific lines that directly cause the error described in the instruction.
2. DO NOT change: connectorRef values, image references, template references (templateRef, versionLabel, gitBranch), stage variables (<+stage.variables.*>), pipeline variables (<+pipeline.*>), secrets (<+secrets.*>), infrastructure config, cloneCodebase, sharedPaths, resource limits, when conditions, failureStrategies, or any step/stage not mentioned in the error.
3. DO NOT add new steps, stages, variables, or configuration.
4. DO NOT remove existing steps, stages, variables, or configuration.
5. DO NOT reformat, reorder, or re-indent lines you are not changing.
6. Harness expressions like `<+stage.variables.Node_Version>`, `<+input>`, `<+pipeline.sequenceId>` are VALID — never replace them with hardcoded values.
7. `failureStrategies` is ALWAYS a list (each item starts with `-`).
8. Fix ONLY the minimum number of lines needed to resolve the error.

Current YAML:
{req.yaml}

Error to fix: {req.instruction}

Return ONLY the complete YAML. No explanation, no markdown fences, no comments."""

    try:
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
        payload = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = bedrock.invoke_model(modelId=MODEL_ID, body=payload)
        result = _json.loads(response["body"].read())
        modified = result["content"][0]["text"].strip()

        # Strip markdown fences if Claude added them
        if modified.startswith("```"):
            lines = modified.split("\n")
            modified = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Post-process: fix failureStrategies written as a map instead of a list.
        # Pattern: "failureStrategies:\n      onFailure:" → add "- " before onFailure
        def fix_failure_strategies(yaml_text: str) -> str:
            lines = yaml_text.split("\n")
            result_lines = []
            i = 0
            while i < len(lines):
                line = lines[i]
                stripped = line.rstrip()
                # Detect "failureStrategies:" followed by "onFailure:" without a leading dash
                if _re.match(r'^(\s*)failureStrategies:\s*$', stripped):
                    result_lines.append(line)
                    i += 1
                    if i < len(lines):
                        next_line = lines[i]
                        next_stripped = next_line.lstrip()
                        indent = len(next_line) - len(next_line.lstrip())
                        # If next line is "onFailure:" without a dash, it's a map — fix it
                        if next_stripped.startswith("onFailure:") and not _re.match(r'^\s*-\s+', next_line):
                            base_indent = " " * (indent - 2) if indent >= 2 else ""
                            result_lines.append(f"{base_indent}- {next_stripped}")
                            i += 1
                            continue
                    continue
                result_lines.append(line)
                i += 1
            return "\n".join(result_lines)

        modified = fix_failure_strategies(modified)
        return {"yaml": modified}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI edit failed: {str(e)}")


# ─── AI Summarize Execution Logs ──────────────────────────────────────────────

class AiSummarizeLogsRequest(BaseModel):
    api_key: str
    account_id: str
    execution_data: dict


@router.post("/ai-summarize-logs")
async def ai_summarize_logs(req: AiSummarizeLogsRequest, _=Depends(get_current_user)):
    import boto3
    import json as _json

    MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    data = req.execution_data
    prompt = f"""A Harness CI/CD pipeline execution failed. Analyze this data and provide a concise, plain-English summary of what went wrong and how to fix it.

Pipeline: {data.get("pipelineName", "unknown")}
Status: {data.get("status", "unknown")}
Error: {data.get("errorMessage", "none")}

Failed steps:
{_json.dumps(data.get("failed_nodes", []), indent=2)}

All stages:
{_json.dumps(data.get("stages", []), indent=2)}

Provide:
1. What failed (1-2 sentences)
2. Likely root cause (1-2 sentences)
3. Recommended fix (bullet points)

Keep it brief and actionable."""

    try:
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
        # Truncate large payloads to avoid token limits
        prompt_safe = prompt[:12000] if len(prompt) > 12000 else prompt
        payload = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt_safe}],
        })
        response = bedrock.invoke_model(modelId=MODEL_ID, body=payload)
        result = _json.loads(response["body"].read())
        return {"summary": result["content"][0]["text"].strip()}
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"AI summary failed: {str(e)} | {traceback.format_exc()[-500:]}")


# ─── Natural Language Chat Agent ──────────────────────────────────────────────

class HarnessChatRequest(BaseModel):
    api_key: str
    account_id: str
    org_id: str = "default"
    project_id: str = ""
    message: str
    history: list = []


HARNESS_CHAT_TOOLS = [
    {
        "name": "get_account_info",
        "description": "Get Harness account information (name, plan, status, company).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_organizations",
        "description": "List all organizations in the Harness account.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_projects",
        "description": "List all projects, optionally filtered by org.",
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "Org identifier"},
            },
        },
    },
    {
        "name": "list_pipelines",
        "description": "List pipelines in a specific project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string"},
                "project_id": {"type": "string"},
            },
            "required": ["org_id", "project_id"],
        },
    },
    {
        "name": "list_executions",
        "description": "List recent pipeline executions in a project. Pass status=['Failed'] to filter only failed runs, status=['Success'] for successful, or omit for all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string"},
                "project_id": {"type": "string"},
                "status": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by status. Valid values: 'Failed', 'Success', 'Running', 'Aborted'. Omit for all statuses."
                },
            },
            "required": ["org_id", "project_id"],
        },
    },
    {
        "name": "get_pipeline_detail",
        "description": "Get detailed info about a pipeline: metadata and recent executions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string"},
                "project_id": {"type": "string"},
                "pipeline_id": {"type": "string"},
            },
            "required": ["org_id", "project_id", "pipeline_id"],
        },
    },
    {
        "name": "get_execution_logs",
        "description": "Get logs, stages, and failed steps for a specific pipeline execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string"},
                "project_id": {"type": "string"},
                "execution_id": {"type": "string"},
            },
            "required": ["org_id", "project_id", "execution_id"],
        },
    },
]


async def _execute_chat_tool(req: HarnessChatRequest, tool_name: str, tool_input: dict) -> dict:
    import json as _json
    hdrs = harness_headers(req.api_key)
    acct = req.account_id
    org  = tool_input.get("org_id")  or req.org_id or "default"
    proj = tool_input.get("project_id") or req.project_id or ""
    base = f"accountIdentifier={acct}"

    async with httpx.AsyncClient(timeout=20) as c:

        if tool_name == "get_account_info":
            r = await c.get(f"{HARNESS_BASE}/ng/api/accounts/{acct}?{base}", headers=hdrs)
            if r.status_code == 200:
                d = r.json().get("data", {})
                return {k: d.get(k) for k in ("name","identifier","accountType","accountStatus","companyName","cluster")}
            return {"error": _parse_harness_error(r.text)}

        elif tool_name == "list_organizations":
            r = await c.get(f"{HARNESS_BASE}/ng/api/organizations?{base}&pageSize=50", headers=hdrs)
            if r.status_code == 200:
                items = r.json().get("data", {}).get("content", [])
                return {"organizations": [{"identifier": i["organization"]["identifier"], "name": i["organization"]["name"]} for i in items]}
            return {"error": _parse_harness_error(r.text)}

        elif tool_name == "list_projects":
            r = await c.get(f"{HARNESS_BASE}/ng/api/projects?{base}&orgIdentifier={org}&pageSize=50&hasModule=true", headers=hdrs)
            if r.status_code == 200:
                items = r.json().get("data", {}).get("content", [])
                return {"projects": [{"identifier": i["project"]["identifier"], "name": i["project"]["name"], "orgIdentifier": i["project"]["orgIdentifier"], "modules": i["project"].get("modules",[])} for i in items]}
            return {"error": _parse_harness_error(r.text)}

        elif tool_name == "list_pipelines":
            url = f"{HARNESS_BASE}/pipeline/api/pipelines/list?{base}&orgIdentifier={org}&projectIdentifier={proj}"
            r = await c.post(url, headers={**hdrs, "Content-Type": "application/json"}, content=_json.dumps({"filterType": "PipelineSetup"}).encode())
            if r.status_code == 200:
                items = r.json().get("data", {}).get("content", [])
                return {"pipelines": [{"identifier": p["identifier"], "name": p["name"], "lastExecutionStatus": p.get("executionSummaryInfo",{}).get("lastExecutionStatus")} for p in items]}
            return {"error": _parse_harness_error(r.text)}

        elif tool_name == "list_executions":
            url = f"{HARNESS_BASE}/pipeline/api/pipelines/execution/summary?{base}&orgIdentifier={org}&projectIdentifier={proj}&pageSize=20"
            exec_body = {"filterType": "PipelineExecution"}
            if tool_input.get("status"):
                exec_body["status"] = tool_input["status"]
            r = await c.post(url, headers={**hdrs, "Content-Type": "application/json"}, content=_json.dumps(exec_body).encode())
            if r.status_code == 200:
                items = r.json().get("data", {}).get("content", [])
                return {"executions": [{"planExecutionId": e.get("planExecutionId"), "pipelineIdentifier": e.get("pipelineIdentifier"), "status": e.get("status"), "runSequence": e.get("runSequence"), "startTs": e.get("startTs")} for e in items]}
            return {"error": _parse_harness_error(r.text)}

        elif tool_name == "get_pipeline_detail":
            pid = tool_input["pipeline_id"]
            r = await c.get(f"{HARNESS_BASE}/pipeline/api/pipelines/{pid}?{base}&orgIdentifier={org}&projectIdentifier={proj}", headers=hdrs)
            if r.status_code == 200:
                d = r.json().get("data", {})
                yaml_preview = d.get("yamlPipeline", "")
                return {"identifier": pid, "storeType": d.get("storeType"), "yaml_preview": yaml_preview[:600] + "..." if len(yaml_preview) > 600 else yaml_preview}
            return {"error": _parse_harness_error(r.text)}

        elif tool_name == "get_execution_logs":
            eid = tool_input["execution_id"]
            r = await c.get(f"{HARNESS_BASE}/pipeline/api/pipelines/execution/{eid}?{base}&orgIdentifier={org}&projectIdentifier={proj}", headers=hdrs)
            if r.status_code == 200:
                d = r.json().get("data", {}).get("pipelineExecutionSummary", {})
                nodes = d.get("layoutNodeMap", {})
                failed = [{"name": v.get("name"), "status": v.get("status"), "failureInfo": v.get("failureInfo", {}).get("message","")} for v in nodes.values() if v.get("status") in ("Failed","FAILED")]
                return {"status": d.get("status"), "pipelineName": d.get("name"), "startTs": d.get("startTs"), "endTs": d.get("endTs"), "errorMessage": d.get("executionErrorInfo",{}).get("message"), "failed_nodes": failed}
            return {"error": _parse_harness_error(r.text)}

    return {"error": f"Unknown tool: {tool_name}"}


@router.post("/chat")
async def harness_chat(req: HarnessChatRequest, _=Depends(get_current_user)):
    import boto3
    import json as _json

    MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    system_prompt = f"""You are a helpful Harness CI/CD assistant inside an SDLC platform.
You can query and control the user's Harness account using the tools provided.

Default context:
- Account: {req.account_id}
- Org: {req.org_id or "default"}
- Project: {req.project_id or "not set"}

Rules:
- Be concise. Use bullet points for lists.
- Show pipeline names, statuses, and timestamps when listing.
- Format epoch timestamps as readable dates (e.g. "4 Feb 2026, 15:44").
- If a tool returns an error, explain it clearly and suggest what to do.
- You cannot trigger pipelines — if asked, tell the user to use the Deployments tab instead."""

    messages = list(req.history) + [{"role": "user", "content": req.message}]

    try:
        bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
        tool_calls_made = []

        for _ in range(6):
            payload = _json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "system": system_prompt,
                "tools": HARNESS_CHAT_TOOLS,
                "messages": messages,
            })
            resp = bedrock.invoke_model(modelId=MODEL_ID, body=payload)
            result = _json.loads(resp["body"].read())
            stop_reason = result.get("stop_reason")
            content = result.get("content", [])

            if stop_reason == "end_turn":
                text = next((c["text"] for c in content if c.get("type") == "text"), "")
                messages.append({"role": "assistant", "content": content})
                return {"answer": text, "tool_calls": tool_calls_made, "history": messages}

            if stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": content})
                tool_results = []
                for block in content:
                    if block.get("type") != "tool_use":
                        continue
                    tool_calls_made.append({"name": block["name"], "input": block.get("input", {})})
                    try:
                        tr = await _execute_chat_tool(req, block["name"], block.get("input", {}))
                        tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": _json.dumps(tr)})
                    except Exception as e:
                        tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": f"Error: {e}", "is_error": True})
                messages.append({"role": "user", "content": tool_results})

        raise HTTPException(status_code=500, detail="Agent exceeded maximum iterations")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat agent failed: {str(e)}")
