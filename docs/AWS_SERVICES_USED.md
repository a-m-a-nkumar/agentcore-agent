# AWS services used in this project

Summary of AWS services and how they are used (agentcore-starter backend, Lambdas, and deployment).

---

## 1. **Amazon S3**

- **Use:** BRD storage (JSON structure, text file, transcripts, templates).
- **Config:** `S3_BUCKET_NAME` (e.g. `test-development-bucket-siriusai`).
- **Where:** `app.py` (`get_s3_client()`), `lambda_brd_chat.py` (`_get_s3_client()`), download/upload BRD, API `_load_brd_structure_from_s3`, Lambdas read/write `brds/{brd_id}/`.

---

## 2. **Bedrock AgentCore (Agent Runtime)**

- **Use:** Run the BRD and Analyst agents (invoke agent, tool calls, streaming).
- **Config:** `AGENT_ARN`, `ANALYST_AGENT_ARN`, `AWS_REGION`.
- **Where:** `app.py` `get_agent_core_client()` → `boto3.client('bedrock-agentcore')`, `invoke_agent_runtime()` for `/generate-from-s3`, `/chat`, `/analyst-chat`.

---

## 3. **Bedrock AgentCore (Identity)**

- **Use:** Intended for user identity and BRD access (currently placeholder logic).
- **Config:** `AWS_ACCOUNT_ID`, `AWS_REGION` (identity ARN format).
- **Where:** `auth.py` `get_agentcore_identity_client()`, `store_user_identity_in_agentcore`, `check_brd_access_via_agentcore`, `grant_brd_access_via_agentcore`, `revoke_brd_access_via_agentcore`.

---

## 4. **AgentCore Memory**

- **Use:** Chat session and conversation history for BRD editing (create event, list events).
- **Config:** `AGENTCORE_MEMORY_ID`, `AGENTCORE_ACTOR_ID`, `AGENTCORE_GATEWAY_ID`.
- **Where:** `lambda_brd_chat.py` (`boto3.client('bedrock-agentcore')`), create/list memory sessions, add user/assistant messages.

---

## 5. **Amazon Bedrock (Runtime – Claude)**

- **Use:** Claude model for BRD chat (Converse API, section updates, intent parsing).
- **Config:** `BEDROCK_MODEL_ID`, `BEDROCK_REGION`, `BEDROCK_MAX_TOKENS`, `BEDROCK_GUARDRAIL_ARN`, `BEDROCK_GUARDRAIL_VERSION`.
- **Where:** `lambda_brd_chat.py` `_get_bedrock_client()` → `boto3.client("bedrock-runtime")`, and other Lambda modules that call Claude.

---

## 6. **Amazon Bedrock (Guardrails)**

- **Use:** Content filters and safety on Bedrock model calls.
- **Config:** `BEDROCK_GUARDRAIL_ARN`, `BEDROCK_GUARDRAIL_VERSION`.
- **Where:** Used with Bedrock runtime in Lambdas when invoking Claude.

---

## 7. **AWS Lambda**

- **Use:** BRD generation, retrieval, chat, requirements gathering, BRD-from-history.
- **Config:** `LAMBDA_BRD_GENERATOR`, `LAMBDA_BRD_RETRIEVER`, `LAMBDA_BRD_CHAT`, `LAMBDA_REQUIREMENTS_GATHERING`, `LAMBDA_BRD_FROM_HISTORY` (and optional `*_ARN`).
- **Where:** `app.py` `get_lambda_client()`; agent tools invoke these Lambdas via the Bedrock AgentCore agent.

---

## 8. **Amazon RDS (PostgreSQL)**

- **Use:** Relational database for projects, sessions, integrations, and app data.
- **Config:** `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD` (RDS endpoint in `.env`).
- **Where:** `db_helper.py` (psycopg2 connection pool), `db_helper_vector.py`; used by routers (projects, sessions, integrations, sync, orchestration).

---

## 9. **Amazon ECR**

- **Use:** Store Docker images for backend and frontend (for ECS or other orchestration).
- **Config:** Account `448049797912`, region `us-east-1`, repository `deluxe-sdlc`.
- **Where:** `scripts/PUSH_ECR.ps1` – login, tag, and push `backend` and `frontend` images.

---

## 10. **AWS STS / IAM (implicit)**

- **Use:** Credentials and authorization for all AWS API calls above.
- **Where:** `app.py` `check_aws_credentials()` uses STS `get_caller_identity`; SDK uses default credential chain (env vars, profile, IAM role for ECS/Lambda).

---

## Quick reference

| Service              | Purpose                          | Main config / location        |
|----------------------|----------------------------------|-------------------------------|
| S3                    | BRD files, transcripts, templates| `S3_BUCKET_NAME`              |
| Bedrock AgentCore     | Agent runtime (BRD, Analyst)     | `AGENT_ARN`, `ANALYST_AGENT_ARN` |
| Bedrock AgentCore     | Identity (placeholder)           | `auth.py`                     |
| AgentCore Memory      | Chat sessions / history          | `AGENTCORE_*` in Lambdas      |
| Bedrock Runtime       | Claude (Converse) in Lambdas     | `BEDROCK_*`                   |
| Bedrock Guardrails    | Content safety                   | `BEDROCK_GUARDRAIL_ARN`       |
| Lambda                | BRD generator, chat, etc.        | `LAMBDA_*` function names     |
| RDS (PostgreSQL)      | App database                     | `DATABASE_*`                  |
| ECR                   | Backend/frontend images          | `PUSH_ECR.ps1`                |
| STS / IAM             | Credentials and auth             | Default credential chain      |
