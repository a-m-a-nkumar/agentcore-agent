# IMPORTANT — Lambda + AgentCore Runbook (dev account 590184044598)

**Read top to bottom before deploying.** The CodeBuild path and the
"obvious" `agentcore launch --agent X` both fail with the SSO PowerUser role
this team uses. Below is the path that actually works end-to-end.

---

## 0. Prereqs (every deploy session)

```bash
# AWS SSO temp creds (env-based, not a profile)
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...

# Lambda + agent token-tracking callbacks (deploy_lambdas.py reads these
# at deploy time and writes them onto each Lambda's environment)
export BACKEND_URL=https://sdlc-dev.deluxe.com
export INTERNAL_API_KEY=dev-key-aman             # one of the keys in backend's INTERNAL_API_KEYS
export INTERNAL_TLS_VERIFY=0                     # backend uses Deluxe-internal CA not in Lambda trust store

# Windows console UTF-8 (agentcore CLI prints emojis → cp1252 crashes without these)
export PYTHONIOENCODING=utf-8
export PYTHONLEGACYWINDOWSSTDIO=1

# Make sure Docker Desktop is running BEFORE attempting agentcore deploy
docker ps >/dev/null   # should not error
```

---

## 1. Lambdas (4 functions)

```bash
cd /path/to/agentcore-agent
python deploy_lambdas.py
```

What this does, and why each piece matters:

- **Builds linux/x86_64 zips** at `lambda_builds/{name}/`, copies handler +
  shared modules (`llm_gateway.py`, `environment.py`, `env_vdi.py`,
  `db_config.py`, `services/`), pip-installs `openai`, zips, uploads.
- **Reads `BACKEND_URL`/`INTERNAL_API_KEY` from your shell env** and writes
  them onto each Lambda's environment via `update-function-configuration`.
  If you forget to export them, the existing values stay (only non-empty
  vars overwrite).
- Uses env credentials when `AWS_ACCESS_KEY_ID` is set; falls back to
  `--profile 590184044598_PowerUser` otherwise.

**Function names that exist** (don't invent new ones):

```
sdlc-dev-brd-chat
sdlc-dev-brd-from-history
sdlc-dev-brd-generator
sdlc-dev-requirements-gathering
```

**Common failures**

| Symptom | Cause | Fix |
|---|---|---|
| `PermissionError [WinError 32]` on `lambda_builds/*.zip` | OneDrive sync holds files open | Wait 30-60s, retry; or run `until rm -rf lambda_builds/*; do sleep 5; done` then redeploy |
| `Unable to import module 'X': No module named 'dotenv'` | Lambda imports `services/s3_service.py` which used to do `from dotenv import load_dotenv` at module load | Already patched — the import is now inside `try/except ModuleNotFoundError` |
| `No module named 'requests'` in record-tokens callback | Lambda zips don't ship `requests` | `_record_tokens_async` already uses stdlib `urllib.request` — make sure your llm_gateway.py is current |
| `[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain` | Backend cert chain not in Lambda trust store | `INTERNAL_TLS_VERIFY=0` must be set on the Lambda env (deploy_lambdas.py does this if exported) |

---

## 2. Agents (pm_agent, analyst_agent)

**Don't use `agentcore launch --agent X` (the default CodeBuild path).** It
will try to create a fresh CodeBuild execution role
(`AmazonBedrockAgentCoreSDKCodeBuild-us-east-1-<hash>`) and fail with
`AccessDenied: iam:CreateRole` — the SSO PowerUser role doesn't have IAM
mutations. Until an admin pre-creates that role and writes its ARN into
`.bedrock_agentcore.yaml` under `codebuild.execution_role`, use
`--local-build` (Docker on your laptop builds the ARM64 image, then it's
pushed to ECR + the AgentCore runtime is updated).

```bash
# from repo root, venv activated
agentcore deploy --agent pm_agent       --local-build --image-tag pm_agent       --auto-update-on-conflict
agentcore deploy --agent analyst_agent  --local-build --image-tag analyst_agent  --auto-update-on-conflict
```

Each takes ~5 min (Docker build + push). The toolkit defaults to
`linux/arm64` regardless of the platform field in the yaml.

**Pre-flight gotchas**

- `bedrock-agentcore-starter-toolkit` provides the `agentcore` CLI. If
  `which agentcore` shows nothing, only the runtime SDK is installed:
  ```bash
  pip install bedrock-agentcore-starter-toolkit
  ```
- The toolkit looks for `.bedrock_agentcore/{agent_name}/Dockerfile`, where
  `{agent_name}` is the *agent name in yaml*, not the python module name.
  `pm_agent` → `.bedrock_agentcore/pm_agent/Dockerfile` (which is just a
  copy of `.bedrock_agentcore/my_agent/Dockerfile`). If the directory
  doesn't exist you'll get *"Dockerfile not found at … Please run
  'agentcore configure' first."*
- Don't run pm_agent + analyst_agent + Lambda deploy all in parallel —
  Docker daemon will OOM and you'll see *"failed to build: Canceled: rpc
  error: code = Canceled desc = grpc: the client connection is closing"*.
  Run agents one at a time, Lambda deploy can run alongside.

**Agent env vars** — deploy_lambdas.py only updates the *Lambdas*. Agents
need their env set separately, either via `agentcore configure` or directly
via the boto3 control plane:

```python
import boto3
ac = boto3.client('bedrock-agentcore-control', region_name='us-east-1')
for runtime_id, image in [
    ('pm_agent-uDlkiNFagv',      '590184044598.dkr.ecr.us-east-1.amazonaws.com/sdlc-dev-repo:pm_agent'),
    ('analyst_agent-JAa3wMFKOK', '590184044598.dkr.ecr.us-east-1.amazonaws.com/sdlc-dev-repo:analyst_agent'),
]:
    cur = ac.get_agent_runtime(agentRuntimeId=runtime_id)
    new_env = dict(cur.get('environmentVariables') or {})
    new_env['BACKEND_URL'] = 'https://sdlc-dev.deluxe.com'
    new_env['INTERNAL_API_KEY'] = 'dev-key-aman'
    new_env['INTERNAL_TLS_VERIFY'] = '0'
    kwargs = {
        'agentRuntimeId': runtime_id,
        'agentRuntimeArtifact': {'containerConfiguration': {'containerUri': image}},
        'roleArn': cur['roleArn'],
        'networkConfiguration': cur['networkConfiguration'],
        'environmentVariables': new_env,
    }
    if cur.get('protocolConfiguration'):  kwargs['protocolConfiguration']  = cur['protocolConfiguration']
    if cur.get('lifecycleConfiguration'): kwargs['lifecycleConfiguration'] = cur['lifecycleConfiguration']
    ac.update_agent_runtime(**kwargs)
```

`update_agent_runtime` rolls a new task with the new env; the runtime
goes `READY → UPDATING → READY` (~30-60s).

---

## 3. Verification

After any deploy:

```bash
# Smoke-test the backend's record-tokens endpoint (should be 401 with bad
# key, 204 with the right one — proves the route is live on the deployed
# backend, which the Lambdas POST to)
curl -sk -X POST https://sdlc-dev.deluxe.com/api/internal/record-tokens   -H "X-API-Key: dev-key-aman" -H "Content-Type: application/json"   -d '{"user_id":"00000000-0000-0000-0000-000000000000","tokens":1,"source":"manual-test"}' -w "\n%{http_code}\n"
```

After running a real BRD generation/edit/analyst chat:

```sql
-- in the dev RDS
SELECT email, token_usage FROM users WHERE email='aman.kumar@deluxe.com';
```

Should jump by tens of thousands per BRD generation, low thousands per edit
or chat turn.

If it doesn't, tail the Lambda you exercised and look for either:

```
[LLM Gateway] ... user=<uuid> source=<lambda_name> tokens prompt=N completion=M total=K     # ✅ recorder fired
[LLM Gateway] record-tokens callback failed for <uuid>: <reason>                            # ❌ next thing to fix
```

```bash
export MSYS_NO_PATHCONV=1   # prevent Git Bash mangling /aws/... paths
aws logs tail "/aws/lambda/sdlc-dev-brd-generator" --region us-east-1 --since 5m --follow
```

---

## 4. Long-term cleanup (not blocking, but flag in PR review)

- **Lambda CI script** uses `npm install --force --no-package-lock` on the
  *frontend* repo, which makes every transitive-dep version that hits JFrog
  curation a build break (e.g. `@swc/core@1.15.32`). Switch the pipeline to
  `npm ci`. Until then, pin offending versions in `package.json` `overrides`.
- `INTERNAL_TLS_VERIFY=0` is a workaround. The proper fix is shipping the
  Deluxe internal CA bundle into the Lambda runtime (Lambda layer or
  bundled `cacert.pem` referenced via `SSL_CERT_FILE`) and removing the
  opt-out.
- `agentcore launch` (CodeBuild path) becomes the right deploy method as
  soon as an admin pre-creates `AmazonBedrockAgentCoreSDKCodeBuild-us-east-1-*`
  and writes its ARN into `.bedrock_agentcore.yaml` under
  `codebuild.execution_role` — then PowerUser SSO can deploy without IAM
  mutations.
