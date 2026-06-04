# Backend Agent Audit — BRD Module & Architecture Module

**Repo:** `agentcore-agent` (dev backend)
**Date:** 2026-05-22
**Scope:** PM agent, Analyst agent, SAD agent — both their AgentCore Runtime code and their AWS Lambda backends, plus all in-process FastAPI wiring, prompts, persistence, and cross-cutting AWS components.

This document is a code-level audit. Every claim cites file:line so it can be traced. It also calls out code that is wired in but never reached, dead config, legacy artifacts, and misleading docstrings.

---

## 1. Executive summary

| Module | Agent | Primary runtime | Primary Lambda(s) | Frontend driver |
|---|---|---|---|---|
| BRD | **PM agent** (`my_agent.py`) | AgentCore Runtime `pm_agent-uDlkiNFagv` | `sdlc-dev-brd-generator`, `sdlc-dev-brd-chat`, (`sdlc-dev-brd-retriever` — dead) | `/generate`, `/api/chat`, `/api/generate-from-s3` |
| BRD | **Analyst agent** (`analyst_agent.py`) | AgentCore Runtime `analyst_agent-JAa3wMFKOK` (rarely exercised; module has a NameError) | `sdlc-dev-requirements-gathering`, `sdlc-dev-brd-from-history` | `/api/analyst-chat-stream` (production), `/api/analyst-chat` (fallback) |
| Architecture | **SAD agent** | None — direct Lambda invoke from FastAPI | `sdlc-dev-sad-orchestrator` | `/api/sad/turn`, `/api/sad/generate`, `/api/sad/audit`, `/api/sad/revert-section`, etc. |

**Shared infrastructure that is actually used:**

- AWS Bedrock AgentCore Runtime (BRD only — PM + Analyst). SAD does **not** use it.
- AWS Bedrock AgentCore **Memory** (`sdlc_dev_agentcore_memory-VF74Yf64ZB`) — all three agents write/read events here.
- AWS Bedrock AgentCore **Gateway** — **not used by any agent**. `DEFAULT_AGENTCORE_GATEWAY_ID` is `""` and unreferenced.
- AWS Lambda — 5 production functions deployed via `deploy_lambdas.py`.
- AWS S3 — bucket `sdlc-orch-dev-us-east-1-app-data`, prefixes `brds/`, `transcripts/`, `templates/`, `sessions/{id}/sad/`, `sessions/{id}/sources/`, `sessions/{id}/diagram/`.
- AWS RDS Postgres — `projects`, `analyst_sessions`, `design_sessions` tables.
- AWS KMS — bucket policy denies non-KMS puts; SSE-KMS used on writes.
- Deluxe DLX AI Gateway (`https://dlxai-dev.deluxe.com/proxy`, model `Claude-4.5-Sonnet`) — **single LLM transport for everything**. No direct Bedrock calls in VDI/prod path.

**Things to note up front:**

- All three agents talk to the same gateway and the same model. The `BedrockModel` branches in both BRD agents (`my_agent.py:476-478`, `analyst_agent.py:312-314`) are **dead in VDI** because `AGENT_MODEL_PROVIDER = "gateway"` (`env_vdi.py:84`).
- "Streaming" in this codebase is mostly fake: `/api/analyst-chat-stream` calls a Lambda synchronously and re-emits the body in fixed-size chunks (`app.py:2099-2108`). The SAD `/turn` endpoint and the SAD Lambda docstring both **claim** SSE — neither is implemented (`routers/sad.py:81`, `lambda_sad_orchestrator.py:9-11`).
- Real `chat_completion_stream` exists in `llm_gateway.py:156-251` but **no agent uses it**.

---

## 2. PM agent (BRD module)

### 2.1 Entry points

| Route | File:Line | Calls |
|---|---|---|
| `POST /generate` (uses uploaded files) | `app.py:781-954` (`generate_brd`) | `bedrock-agentcore.invoke_agent_runtime(agentRuntimeArn=AGENT_ARN, …)` |
| `POST /api/generate-from-s3` (uses S3 transcript) | `app.py:1019-1251` (`generate_brd_from_s3`) | same — invokes `AGENT_ARN` runtime |
| `POST /api/chat` | `app.py:1253-1414` (`chat_with_agent`) | same — invokes `AGENT_ARN` runtime with `prompt`, `brd_id`, `session_id` |

`AGENT_ARN` defaults to `arn:aws:bedrock-agentcore:us-east-1:590184044598:runtime/pm_agent-uDlkiNFagv` (`env_vdi.py:78`).

The AgentCore Runtime entrypoint is `invoke(payload)` in `my_agent.py:512-733`, decorated with `@app.entrypoint` (`my_agent.py:99` creates `BedrockAgentCoreApp()`).

### 2.2 Execution flow

`my_agent.py` is a Strands `Agent` with four `@tool`-decorated functions, but `invoke()` does NOT always let the LLM choose — there are three deterministic branches:

- **Branch A — Template + transcript present** (`my_agent.py:549-576`): bypasses the Strands LLM entirely and directly calls `generate_brd(...)` → Lambda `sdlc-dev-brd-generator`.
- **Branch B — `brd_id` present + user message contains an edit verb** (`my_agent.py:578-615`, "DIRECT PATH"): bypasses Strands and directly calls `chat_with_brd(...)` → Lambda `sdlc-dev-brd-chat`. Trigger verbs at `my_agent.py:591-595`: `change, update, modify, edit, replace, remove, add, delete, show, list, summarize, transfer, everywhere, all sections, entire document`.
- **Branch C — `brd_id` present but no edit verb** (`my_agent.py:616-680`, "AGENT PATH"): retrieves conversation history via `get_brd_conversation_history`, builds an `enhanced_message` prompt (`my_agent.py:633-652`), then calls `agent(enhanced_message)` letting the Strands LLM pick a tool. The prompt's "DECISION LOGIC" only mentions `chat_with_brd` and `fetch_brd`.

Strands token metrics are scraped via `_capture_strands_metrics(...)` and shipped back to the backend's `/api/internal/record-tokens` endpoint via fire-and-forget HTTP (`my_agent.py:33-96`).

The four `@tool` functions:

- `generate_brd(template, transcript, brd_id?)` → invokes Lambda `LAMBDA_GENERATOR` (`my_agent.py:187-247`)
- `fetch_brd(brd_id)` → invokes Lambda `LAMBDA_RETRIEVER` (`my_agent.py:249-287`) — **lambda not deployed, see §6**
- `chat_with_brd(action, brd_id, session_id?, message?, template?, transcript?)` → invokes Lambda `LAMBDA_CHAT` (`my_agent.py:289-392`)
- `get_brd_conversation_history(brd_id, session_id?)` → reads AgentCore Memory directly (`my_agent.py:394-461`)

### 2.3 LLM / model usage

- **Strands agent model** (`my_agent.py:475-485`): branched on `AGENT_MODEL_PROVIDER`. In VDI it is `"gateway"` → `OpenAIModel` pointed at `https://dlxai-dev.deluxe.com/proxy` with model `Claude-4.5-Sonnet`. The `BedrockModel` branch is unreachable in VDI.
- **Lambda-side models**: each Lambda calls `chat_completion(...)` from `environment.py` which in VDI is `llm_gateway.chat_completion`. Lambdas also have `BEDROCK_MODEL_ID` env var set (e.g. `lambda_brd_generator.py:18`), but the gateway path remaps any anthropic/bedrock model name to `DEFAULT_GATEWAY_MODEL = "Claude-4.5-Sonnet"` (`env_vdi.py:60-64`).

The "DIRECT PATH" branch (B) explicitly does NOT invoke the Strands LLM (`my_agent.py:599-600` "Bypassing Strands Agent LLM"). Only the AGENT PATH (C) and the generic-query path (`my_agent.py:683-686`) call `agent(...)`.

### 2.4 External dependencies actually wired up

| What | Where | Used by |
|---|---|---|
| AWS Lambda (`boto3.client('lambda')`) | `my_agent.py:123-136`, `app.py:253-262` | All tool invocations from PM agent |
| AgentCore Memory | `my_agent.py:138-144`, `app.py:264-266` | `get_brd_conversation_history`, `/api/brd-history` |
| AgentCore Runtime (`invoke_agent_runtime`) | `app.py:241-251` | All three FastAPI entry points |
| S3 (via `s3_put_object`, `get_s3_client`) | `lambda_brd_generator.py:706`, `lambda_brd_chat.py` (many) | BRD text + structure JSON storage |
| RDS `save_project_brd_session` | `app.py:1193-1200` | Persists project → `brd_id`/`session_id` linkage |
| HTTP callback `/api/internal/record-tokens` | `my_agent.py:33-72` | Token accounting for Strands |

### 2.5 State / persistence

- **S3 keys** (bucket `sdlc-orch-dev-us-east-1-app-data`):
  - `brds/{brd_id}/BRD_{brd_id}.txt` — plain text BRD (`lambda_brd_generator.py:705`)
  - `brds/{brd_id}/brd_structure.json` — structured JSON (`lambda_brd_generator.py:713`, read by `lambda_brd_chat.get_brd_from_s3` and `app.py:_load_brd_structure_from_s3`)
  - `brds/{brd_id}/BRD_{brd_id}.json` — alternate JSON saved by `lambda_brd_from_history.save_brd_to_s3` (`lambda_brd_from_history.py:326`)
  - `templates/Deluxe_BRD_Template.docx` — fetched in `/api/generate-from-s3`
  - `transcripts/{transcript_id}/{filename}` — uploaded transcripts (`app.py:974`)
- **AgentCore Memory**: `memoryId = sdlc_dev_agentcore_memory-VF74Yf64ZB`, `actorId = analyst-session`. Session naming: `brd-session-{brd_id}` (`my_agent.py:419, 585`, `lambda_brd_chat.py:118`).
- **RDS**: `projects.brd_id`, `projects.agentcore_session_id` (`db_helper.py:80-90, 583-619`).

### 2.6 Streaming vs non-streaming

PM agent path is **non-streaming**. `invoke_agent_runtime(...)` returns chunked bytes which `app.py:864-867, 1138-1142, 1314-1322` concatenate into a single string and return as `JSONResponse`.

### 2.7 Lambda vs in-process split

| Operation | Where it runs | File |
|---|---|---|
| Strands `Agent` orchestration | AgentCore Runtime container `pm_agent-uDlkiNFagv` | `my_agent.py` |
| BRD generation (template + transcript → BRD) | Lambda `sdlc-dev-brd-generator` | `lambda_brd_generator.py` |
| BRD chat / edit / list / show | Lambda `sdlc-dev-brd-chat` | `lambda_brd_chat.py` |
| BRD retrieval by ID | Lambda `sdlc-dev-brd-retriever` (defined but **not in `deploy_lambdas.py`**) | `lambda_brd_retriever.py` |
| File upload, S3 ops, response post-processing | FastAPI process | `app.py` |
| BRD section reads | FastAPI process (reads S3 directly) | `app.py:3666-3826` |

---

## 3. Analyst agent (BRD module)

### 3.1 Entry points

| Route | File:Line | Calls |
|---|---|---|
| `POST /api/analyst-warm` | `app.py:1498-1542` | Parallel ping to both analyst Lambdas |
| `POST /api/analyst-chat` (non-streaming) | `app.py:1545-1981` | `invoke_agent_runtime(ANALYST_AGENT_ARN, …)` |
| `POST /api/analyst-chat-stream` (SSE) | `app.py:1984-2133` | **Bypasses AgentCore Runtime**; invokes `LAMBDA_REQUIREMENTS_GATHERING_ARN` Lambda directly and fakes streaming by chunking 3 words at a time |
| `GET /api/analyst-history/{session_id}` | `app.py:2136-2206` AND duplicate at `app.py:3227-3293` | `bedrock-agentcore.list_events` |
| `POST /api/analyst-generate-brd` | `app.py:2208-2436`, redefined at `app.py:2438-2697`, redefined again at `app.py:2699-2980` | Lambda `LAMBDA_BRD_FROM_HISTORY` |

`ANALYST_AGENT_ARN = arn:aws:bedrock-agentcore:us-east-1:590184044598:runtime/analyst_agent-JAa3wMFKOK` (`env_vdi.py:79`).

**Important:** the frontend uses `/api/analyst-chat-stream` (`deluxe-sdlc-frontend/src/services/analystApi.ts:253`), which means the AgentCore Runtime path through `analyst_agent.py` is rarely exercised. `/api/analyst-chat` is still wired (`api.ts:25`) as a fallback.

The AgentCore Runtime entrypoint is `invoke(payload)` in `analyst_agent.py:366-507`, decorated with `@app.entrypoint`.

### 3.2 Execution flow

`analyst_agent.py` is a Strands `Agent` with two tools, but `invoke()` mostly bypasses the LLM:

- **Branch A — "generate" keywords** (`analyst_agent.py:407-429`): direct call to `generate_brd_from_history` → Lambda `sdlc-dev-brd-from-history`.
- **Branch B — anything else** (`analyst_agent.py:431-441`): direct call to `gather_requirements` → Lambda `sdlc-dev-requirements-gathering`.
- **Branch C — fallback** (`analyst_agent.py:443-493`): only reached on exception from Branch B; builds `enhanced_prompt` and lets the Strands `Agent` pick a tool. **This branch has a control-flow bug** — `enhanced_prompt` is constructed inside `except` (line 453), then a separate `try` starting at line 459 references it. If `gather_requirements` succeeds (the common case), `enhanced_prompt` is never defined and the inner try is skipped because of the early `return` at 437. So the Strands LLM path in this agent is effectively dead.

The streaming endpoint (`app.py:1984-2133`) is the actual production path — it invokes `lambda_requirements_gathering` directly via `boto3.client('lambda').invoke(...)`, then yields SSE chunks (`yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"`, line 2106). It does NOT touch the AgentCore Runtime.

### 3.3 LLM / model usage

- **Strands agent model**: same `OpenAIModel` pointing at the Deluxe gateway with `Claude-4.5-Sonnet` (`analyst_agent.py:312-321`).
- **System prompt**: hardcoded "Mary" persona stub in `analyst_agent.py:324-341` — only used when the Strands LLM actually runs (dead fallback path). The **real** Mary persona is the much larger prompt in `prompts/requirements_gathering_prompts.py:MARY_REQUIREMENTS_PROMPT` (~117 lines) used by `lambda_requirements_gathering.py:175-178`.
- **Lambda LLM**: `chat_completion` → `llm_gateway.chat_completion` in VDI.

### 3.4 External dependencies actually wired up

| What | Where | Used by |
|---|---|---|
| AgentCore Memory | `lambda_requirements_gathering.py:_get_agentcore_memory_client`, `lambda_brd_from_history.py:_get_agentcore_memory_client`, `app.py:2155, 2271, 2762` | Stores every user/assistant turn |
| Lambda invocation | `app.py:2036, 2334`, `analyst_agent.py:159` | Streaming endpoint, generate-brd endpoint |
| AgentCore Runtime | `app.py:1604-1610` | `/api/analyst-chat` only |
| S3 | `lambda_brd_from_history.py:fetch_template_from_s3, save_brd_to_s3` | Template + final BRD |
| RDS via `track_event` | `app.py:2389-2402, 2933-2946` | Event `analyst_agent_brd_generated` |

### 3.5 State / persistence

- **AgentCore Memory** session naming: not auto-prefixed. `app.py:1573-1578` uses whatever the frontend sends (typically `analyst-session-{uuid}`). `analyst_agent.py:399-402` would generate `f"analyst-session-{uuid}"` if missing.
- **S3 keys** (same bucket): `brds/{brd_id}/BRD_{brd_id}.txt` and `brds/{brd_id}/BRD_{brd_id}.json` (`lambda_brd_from_history.py:317, 326`). **Note:** the analyst flow writes a different structure file path (`BRD_{brd_id}.json`) than the PM flow (`brd_structure.json`). Section reads in `app.py:3670` only look at `brd_structure.json`, so analyst-generated BRDs may not have section tabs work until a chat-side backfill runs.
- **RDS**: `analyst_sessions` table (`db_helper.py:650-740`): `id, project_id, user_id, title, brd_id, message_count, created_at, last_updated, is_deleted`.

### 3.6 Streaming vs non-streaming

Streaming = `/api/analyst-chat-stream`. It's NOT real LLM streaming — the Lambda is invoked synchronously (`InvocationType='RequestResponse'`, `app.py:2038`), the full response is received, then fake-streamed by splitting on spaces every 3 words with `asyncio.sleep(0.02)` (`app.py:2099-2108`). Genuine LLM streaming via `chat_completion_stream` (`env_vdi.py:46-73`) is defined but unused.

`/api/analyst-chat` is fully non-streaming.

### 3.7 Lambda vs in-process split

| Operation | Where it runs | File |
|---|---|---|
| Strands `Agent` orchestration | AgentCore Runtime container `analyst_agent-JAa3wMFKOK` — rarely exercised + has import-time NameError (§6) | `analyst_agent.py` |
| Mary chat turn (`gather_requirements`) | Lambda `sdlc-dev-requirements-gathering` | `lambda_requirements_gathering.py` |
| BRD generation from chat history | Lambda `sdlc-dev-brd-from-history` | `lambda_brd_from_history.py` |
| SSE stream wrapping, session-id management | FastAPI process | `app.py:1984-2133` |
| History fetch | FastAPI process (direct `list_events`) | `app.py:3227-3293` |

---

## 4. SAD agent (Architecture module)

### 4.1 Entry points (HTTP routes → Lambda)

All SAD HTTP routes are in `routers/sad.py` with prefix `/api/sad`. Router mounted at `app.py:41, 133`.

| Method | Path | Handler | Lines |
|---|---|---|---|
| POST | `/api/sad/turn` | `sad_turn` | `routers/sad.py:287-389` |
| POST | `/api/sad/generate` | `sad_generate` | `routers/sad.py:392-427` |
| POST | `/api/sad/audit` | `sad_audit` | `routers/sad.py:430-447` |
| POST | `/api/sad/revert-section` | `sad_revert` | `routers/sad.py:450-462` |
| POST | `/api/sad/save-section` | `sad_save_section` | `routers/sad.py:475-540` |
| GET | `/api/sad/{session_id}/sections` | `get_sections` | `routers/sad.py:558-578` |
| GET | `/api/sad/{session_id}/section/{n}` | `get_section` | `routers/sad.py:581-592` |
| GET | `/api/sad/{session_id}/diagram/{kind}` | `get_diagram` | `routers/sad.py:595-637` |
| GET | `/api/sad/{session_id}/facts` | `get_facts` | `routers/sad.py:640-653` |
| GET | `/api/sad/download-sad/{session_id}` | `download_sad` | `routers/sad.py:660-773` |

Lambda invocation: every "heavy" route calls `_invoke_sad_lambda` (`routers/sad.py:78-96`) which uses `boto3 lambda.invoke(FunctionName="sdlc-dev-sad-orchestrator", InvocationType="RequestResponse", …)`. Function name overridable via env var `LAMBDA_SAD_ORCHESTRATOR` (`routers/sad.py:45`). Lambda client has `read_timeout=300s`, `max_attempts=1` (`routers/sad.py:54-58`).

Deployed Lambda zip: `lambda_builds/sad-orchestrator.zip`, configured in `deploy_lambdas.py:66-71`.

Auth: each handler depends on `routers.projects.get_current_user` (Azure AD via `auth.verify_azure_token`). `_ensure_session_owned` (`routers/sad.py:69-75`) checks `design_sessions.user_id` matches caller before any S3 or Lambda call.

Frontend caller: `deluxe-sdlc-frontend/src/services/sadApi.ts`, driven by `SessionDesignAssistant.tsx` and `components/sad/SADChat.tsx`.

### 4.2 Execution flow

#### `/turn` (unified chat box)

1. `sad_turn` accepts multipart form: `session_id`, `message`, optional `file`, plus UI hints `viewing_section`, `last_card_type`, `last_proposed_section`.
2. If a file is attached, `app.extract_text` (`app.py:767-775`) extracts text for .docx/.pdf/.txt; raw bytes uploaded to `s3://{bucket}/sessions/{session_id}/sources/{filename}` via `services.s3_service.s3_put_object` with SSE-KMS (`routers/sad.py:329-335`).
3. If no file but the message contains Confluence URLs (`_CONFLUENCE_URL_PATTERN`, `routers/sad.py:112-115`), each URL is fetched via `_fetch_confluence_page` using `services.confluence_service.ConfluenceService.get_content_page_by_id`, HTML is stripped (`_strip_confluence_html`), and each becomes a `file_payload`. Failures produce a "warning card" via `_confluence_warning_card`.
4. Payload forwarded to Lambda with `action="turn"` plus `stage`, `file`, `files`, `viewing_section`, `last_card_type`, `last_proposed_section` (`routers/sad.py:353-365`).
5. Lambda entry: `lambda_handler` (`lambda_sad_orchestrator.py:513-533`) dispatches on `event["action"]`. For `turn` it calls `handle_turn` (`lambda_sad_orchestrator.py:743-849`):
   - Multi-file ingest path (`files: [...]`): iterate, call `_do_ingest_doc` for each, set `auto_regen=true` only on the last card (`:778-801`). Bypasses the intent router.
   - Single-file or text path: persist USER message to AgentCore Memory, then call `run_intent_router` (`:650-704`), then dispatch to one of `_do_add_info` / `_do_ingest_doc` / `_do_show_section` / `_do_edit_section` / `_do_regenerate_section` / `handle_audit` / `_do_suggest` / `_do_ask_question`, or return a `generation_starting` / `text` card for `GENERATE_NEW_SAD` / `LINK_DIAGRAM`.
6. Router normalises Lambda return into `{cards: [...]}` (`routers/sad.py:372-376`), then flips `design_sessions.stage` based on the last card's type (`:381-387`).

#### `/generate` (full-document generation)

1. `sad_generate` flips stage to `SAD_GENERATING`, reads `diagram_slots` from `db_helper.get_diagram_slots(session_id)` (`db_helper.py:1090-1126`), forwards `{action="generate_sad", session_id, project_id, brd_id, user_id, diagram_slots}`.
2. Lambda `handle_generate_sad` (`lambda_sad_orchestrator.py:1423-1532`):
   - If `load_sad(session_id)` exists, treats it as a regeneration: snapshots current section content into `previous_versions` (capped at 5), feeds it as `previous_content` to each worker.
   - Otherwise builds a fresh skeleton with the 10 fixed sections (`SAD_SECTIONS`, `:62-73`).
   - Persists skeleton, then runs `_generate_section_content` for each of the 10 sections in a `ThreadPoolExecutor(max_workers=5)` (env `SAD_MAX_PARALLEL_WORKERS`, default 5). Write-through to S3 after each worker (`:1526`).
   - Returns `{sad_id, sections_completed, duration_s}`.
3. Router bumps stage to `SAD_REFINING` (`:426`).

**Despite the Lambda's docstring claiming "streamed via SSE-style chunks", no streaming exists.** The router uses `InvocationType="RequestResponse"` and reads the whole `Payload` once (`routers/sad.py:81, 84`). The frontend simply blocks for 60–120s (`routers/sad.py:397-402`).

#### `/audit`

`sad_audit` forwards `{action="audit", section_number?}`. Lambda `handle_audit` (`lambda_sad_orchestrator.py:1535-1694`): runs `_audit_worker` per section in `ThreadPoolExecutor`, two-attempt retry with a JSON-only nudge on failure. Normalises via `_normalize_audit_payload`. Persists decorated `sad_structure.json` plus `audit_latest.json`. Returns `{badges, details}`.

#### `/revert-section`

`sad_revert` forwards `{action="revert_section", section_number, …}`. Lambda `handle_revert_section` (`:1697-1731`) pops `section["previous_versions"][0]`. Has self-heal logic for old `{ts, reason, content}` envelopes.

#### `/save-section` (no LLM)

`sad_save_section` reads `sad_structure.json` from S3 directly via `_read_sad`, validates block types against `{paragraph, heading, ordered_list, bullet_list, table, diagram}`, pushes previous content onto `previous_versions` (capped at 5), marks status `user_edited`, writes back via `s3_put_object`. Bypasses Lambda entirely.

#### Read endpoints (no Lambda)

`get_sections`, `get_section`, `get_facts`, `download_sad`, `get_diagram` all read directly from S3 via `boto3.client("s3")`. `get_diagram` tries `.png` first then `.svg` for each kind and 404s if absent.

#### DOCX export

`download_sad` walks `sad_structure.json`, builds a `python-docx` Document. Diagram blocks read the section's own `s3_key` and embed PNG/JPG directly or convert SVG via `cairosvg.svg2png` (`routers/sad.py:709-754`). The s3_key trust is critical — earlier the code hardcoded `logical.png` for every diagram block (now fixed per the comment at `:721-725`).

### 4.3 LLM / model usage

**One model, one transport.** The Lambda uses `environment.chat_completion` (`lambda_sad_orchestrator.py:34-39`), which in VDI mode is `llm_gateway.chat_completion` (`env_vdi.py:38`). That function targets `https://dlxai-dev.deluxe.com/proxy` with `Claude-4.5-Sonnet` — **not Bedrock**. Any Bedrock-style model ID is rewritten to the gateway model (`llm_gateway.py:111-112`). 300s timeout, singleton OpenAI SDK client.

**Token-source labels (passed to gateway and recorded in `users.token_usage`):**

| Label | Where | temp / max_tokens |
|---|---|---|
| `lambda_sad_orchestrator:router` | `:674-681` | 0.0 / 400 (env `SAD_ROUTER_MAX_TOKENS`) |
| `lambda_sad_orchestrator:section{N}` | `:1399-1406` | 0.3 / 4000 (env `SAD_SECTION_MAX_TOKENS`) |
| `lambda_sad_orchestrator:audit{N}` | `:1619-1626` | 0.0 / 1500 |
| `lambda_sad_orchestrator:edit` | `:1116-1123` | 0.2 / 3000 (env `SAD_EDIT_MAX_TOKENS`) |
| `lambda_sad_orchestrator:suggest` | `:1228-1234` | 0.4 / 900 |
| `lambda_sad_orchestrator:qa` | `:1258-1264` | 0.3 / 900 |
| `lambda_sad_orchestrator:gather` | `:884-890` | 0.5 / 300 |
| `lambda_sad_orchestrator:doc_relevance` | `:952-959` | 0.0 / 200 |

**Prompts** (all in `prompts/sad_*.py`):

- **Intent router**: `prompts/sad_intent_router.py` — single system prompt with `SAD_INTENTS = ["EDIT_SECTION","SHOW_SECTION","ADD_INFO","INGEST_DOC","LINK_DIAGRAM","AUDIT","SUGGEST","ASK_QUESTION","REGENERATE_SECTION","GENERATE_NEW_SAD"]` (`:12-23`). Output JSON: `{intent, target_section, fact, edit_instruction, regen_proposed, confidence}`.
- **Section drafting**: `prompts/sad_section_prompts.py` — shared `SECTION_SYSTEM_PROMPT` (`:36-110`) + 10 per-section builders (`SECTION_PROMPT_BUILDERS`, `:496-507`). Builders introspect kwargs they accept (`lambda_sad_orchestrator.py:1391-1397`). Output: JSON array of content blocks. The Lambda prepends the `diagram` block for sections 4/6/7 via `_diagram_block_for_section` (`:1291-1351, 1413-1416`).
- **Audit**: `prompts/sad_audit_prompts.py` — fixed issue-code vocabulary (`EMPTY_OR_PLACEHOLDER`, `MISSING_RATIONALE`, `UNMEASURABLE_QA`, `TRACEABILITY_GAP`, `UNDEFINED_TERM`, `DIAGRAM_PROSE_MISMATCH`, `CONSTRAINT_VIOLATION`, `TABLE_INCOMPLETE`, `CATEGORY_MISSING`, `FORMAT_VIOLATION`). Scoring: start 100, −10 per issue.
- **Edit + Suggest**: `prompts/sad_edit_prompts.py:18-41` (`EDIT_SYSTEM_PROMPT`), `:75-98` (`SUGGEST_SYSTEM_PROMPT`).
- **Q&A**: `prompts/sad_qa_prompts.py:14-36` (`QA_SYSTEM_PROMPT`).
- **Gather follow-up**: `prompts/sad_gather_prompts.py:16-33` (`SAD_GATHER_SYSTEM_PROMPT`).
- **Doc-section relevance classifier**: inline at `lambda_sad_orchestrator.py:904-931` (not in `prompts/`).

**No tool/function calling.** `chat_completion_with_tools` exists (`llm_gateway.py:254-310`) but is never called from the SAD Lambda. Streaming (`chat_completion_stream`) also never called by SAD.

ARSR has two fixed category lists driving both the section prompt and the audit's `required_categories` check (`prompts/sad_section_prompts.py:257-279`, used at `lambda_sad_orchestrator.py:1604-1605`).

### 4.4 External dependencies

| Dependency | Where | What it does |
|---|---|---|
| AWS Lambda | `routers/sad.py:51-59, 78-96` | Backend → orchestrator Lambda |
| AWS S3 | `lambda_sad_orchestrator.py:84-88, 102-141`, `routers/sad.py:62-66, 547-553, 622-624, 730`, `services/s3_service.py` | `sad_structure.json`, `facts.json`, `audit_latest.json`, diagram artifacts, source uploads |
| AWS KMS | `services/s3_service.py:52-58` | SSE-KMS; bucket policy denies non-KMS puts |
| AgentCore Memory | `lambda_sad_orchestrator.py:91-95, 429-472`, `routers/design_sessions.py:336-343` | Chat history via `create_event` / `list_events` |
| RDS Postgres (`design_sessions`) | `db_helper.py:910-1215`, `routers/sad.py:34, 309, 404, 426` | Session metadata, stage flips, diagram_slots JSONB |
| Deluxe DLX AI Gateway (OpenAI SDK) | `llm_gateway.py:12, 19-25`, `env_vdi.py:85-87` | All LLM calls |
| Atlassian Confluence REST | `routers/sad.py:191-197`, `services/confluence_service.py` | Pull pages when user pastes Confluence URLs |
| python-docx + cairosvg | `routers/sad.py:670-678, 737-742` | DOCX export rendering; SVG→PNG |
| Internal HTTP callback `/api/internal/record-tokens` | `llm_gateway.py:53-92` | Lambda reports token usage back to backend |

### 4.5 State / persistence

#### S3 (`sdlc-orch-dev-us-east-1-app-data`, prefix `sessions/{session_id}/`)

| Key | Producer | Consumer | Notes |
|---|---|---|---|
| `sad/sad_structure.json` | `lambda_sad_orchestrator.save_sad` (`:132-133`); `routers/sad.py:526-530` | `_read_sad`, `load_sad`, DOCX export | Sections array, `previous_versions` stacks, audit decorations |
| `sad/facts.json` | `save_facts` (`:140-141`), `append_fact` (`:732-736`) | `get_facts` route, `_format_facts` in prompts | Mary-style facts buffer |
| `sad/audit_latest.json` | `handle_audit` (`:1692-1693`) | Read implicitly by frontend via `audit` card | Persisted decorations + ts |
| `sources/{filename}` | `routers/sad.py:331-332` (file upload) | None (audit trail only) | Raw uploaded bytes |
| `sources/{safe_title}.confluence.txt` | `routers/sad.py:212-214` (Confluence URL ingest) | None (audit trail only) | Stripped HTML text |
| `diagram/{kind}.xml` | `routers/design.py:1407` (drawio save) | `load_diagram_xml` (`:331-422`), `get_diagram` route | mxGraph XML — primary "diagram context" for LLM |
| `diagram/{kind}.svg` | `routers/design.py:1410` | `get_diagram` route fallback, DOCX export | drawio exported SVG |
| `diagram/{kind}.png` | `routers/design.py:1420` (drawio), `routers/design.py:2228-2236` (Lucid) | `get_diagram` route primary, DOCX export | rasterized export or Lucid PNG |
| `diagram/{kind}.lucid.json` | `routers/design.py:2229, 2240-2245` | `load_diagram_xml` (`:367-385`) via `_format_lucid_for_llm` | Lucid `/contents` JSON, formatted for LLM |

#### Postgres (`design_sessions`)

Schema in `migrations/add_design_sessions.py:24-38` and `migrations/add_design_diagram_slots.py:50-60`:

- `id` (UUID PK), `project_id`, `user_id`, `name`, `stage` (enum: `NEW|DIAGRAM_GATHERING|DIAGRAM_READY|SAD_GATHERING|SAD_GENERATING|SAD_REFINING`, `db_helper.py:893`).
- `diagram_s3_key`, `diagram_svg_s3_key` — legacy single-slot pointers (still bumped for `logical` for backcompat).
- `sad_id`, `confluence_page_id`.
- `diagram_slots` JSONB — `{logical, infrastructure, security}` each `{status, tool?, artifact_key?, saved_at?, error?}`. Read by `get_diagram_slots`, patched by `update_diagram_slot`.
- `authoring_tool` — `drawio` / `lucid` / null (`set_session_authoring_tool`).
- `created_at`, `last_activity_ts`. The `is_deleted` column was dropped.
- Indexes on `(project_id, last_activity_ts DESC)` and `(user_id, last_activity_ts DESC)`.

The Lambda **does not** touch RDS — the FastAPI router owns all writes. (`lambda_sad_orchestrator.py:20-22` docstring confirms.)

#### AgentCore Memory

- `actorId = "design-session"` (env `SAD_AGENTCORE_ACTOR_ID`, `lambda_sad_orchestrator.py:53`).
- `sessionId = design_session.id`.
- Memory ID `sdlc_dev_agentcore_memory-VF74Yf64ZB`.
- Writes: `add_message_to_memory` (`:429-444`) — one `create_event` per USER turn and per ASSISTANT response.
- Reads: `get_recent_history` (`:447-472`) called only by `_do_add_info` to suppress repeated follow-up questions; the `/history` GET endpoint that surfaces chat to UI lives in `routers/design_sessions.py:323-369`, not in the SAD router.

### 4.6 Architecture components in the SAD flow

#### AgentCore Memory — USED

Only for storing chat transcript. Actor `design-session`, session `{session_id}`. Lambda writes USER/ASSISTANT events; FastAPI `/api/design/sessions/{id}/history` reads them back. Not used for long-term semantic memory; not used by section workers.

#### AgentCore Gateway — NOT USED

`DEFAULT_AGENTCORE_GATEWAY_ID` defined in `env_vdi.py:109` but is `""` by default and has zero references in `lambda_sad_orchestrator.py`. The Lambda never instantiates a Gateway client.

#### Bedrock Knowledge Bases / RAG — NOT USED

`_generate_section_content` declares `rag: List[Dict[str, Any]] = []` with a comment "backend can pre-populate via `event['rag_chunks']` if available" (`lambda_sad_orchestrator.py:1387`), but **nothing in the codebase ever sets `event["rag_chunks"]`** for SAD. Section prompts have a `_format_rag` block that always renders "(no RAG context)" (`prompts/sad_section_prompts.py:152-158`). `services/rag_service.py` (666 lines) is only used by BRD / Jira flows.

#### Lucid integration — USED INDIRECTLY (as diagram source)

The SAD Lambda doesn't call Lucid. The flow:

1. User imports via `POST /api/design/lucid/import` (`routers/design.py:2189-2313`) using `services.lucid_api_service.LucidAPIService.export_document` (PNG) and `get_document_contents` (JSON).
2. PNG → `sessions/{id}/diagram/{type}.png`; JSON → `sessions/{id}/diagram/{type}.lucid.json`.
3. `design_sessions.diagram_slots[type]` patched to `{status:"done", tool:"lucid", artifact_key, saved_at}` (`routers/design.py:2280-2290`).
4. SAD Lambda reads it via `load_diagram_xml(session_id, diagram_type)` (`lambda_sad_orchestrator.py:331-422`): if a `.lucid.json` is present and the drawio `.xml` is missing, `_format_lucid_for_llm` (`:144-328`) walks `doc.pages[].items.{shapes,lines}` and produces text representation. Output fed into section prompts (3, 4, 6, 7, 8).
5. `_diagram_block_for_section` emits a `{type:"diagram", s3_key, alt}` block pointing at the Lucid PNG so DOCX export embeds it.

The legacy Lucid MCP create-from-description path (`routers/design.py:2022-2055`, `services/lucid_mcp_client.py`) is kept "alongside" the newer pull-PNG flow — produces a URL the user opens manually and never lands in the diagram slot. Dead w.r.t. SAD.

#### Figma integration — NOT USED by SAD

`services/figma_service.py` and `routers/figma.py` only wire up a Figma-prompt generator for Jira stories. Zero references from SAD.

#### GitHub integration — NOT USED by SAD

`services/github_service.py` only imported by `routers/test_generation.py`.

#### Lineage service — NOT USED by SAD

`services/lineage_service.py` is only used by test_generation, brd_comparison, jira_generation, db_helper, setup_database. SAD has no lineage tracking — there's no link from BRD sections → SAD sections, even though that would be natural.

#### Bedrock direct — NOT USED in VDI / prod

`env_local.py` would route to direct Bedrock, but `environment.py:23` activates `env_vdi.py` which redirects everything to the DLX AI Gateway. ECS task def confirms VDI config in prod.

---

## 5. Components actively used (cross-module summary)

**Agents (AgentCore Runtime):**

- `my_agent.py` → `pm_agent-uDlkiNFagv`
- `analyst_agent.py` → `analyst_agent-JAa3wMFKOK` (only when frontend uses non-streaming `/api/analyst-chat`; module has import bug)
- SAD: **no runtime** — direct Lambda invoke

**Lambdas (active, deployed by `deploy_lambdas.py:45-65`):**

- `sdlc-dev-brd-chat` (`lambda_brd_chat.py`)
- `sdlc-dev-brd-from-history` (`lambda_brd_from_history.py`)
- `sdlc-dev-brd-generator` (`lambda_brd_generator.py`)
- `sdlc-dev-requirements-gathering` (`lambda_requirements_gathering.py`)
- `sdlc-dev-sad-orchestrator` (`lambda_sad_orchestrator.py`)

**Prompts in active use:**

- `prompts/brd_generator_prompts.py` (PM agent)
- `prompts/brd_from_history_prompts.py` (Analyst agent)
- `prompts/requirements_gathering_prompts.py` (Analyst agent)
- `prompts/sad_intent_router.py`, `sad_section_prompts.py`, `sad_audit_prompts.py`, `sad_edit_prompts.py`, `sad_qa_prompts.py`, `sad_gather_prompts.py` (SAD agent)

**BRD endpoints (active):**

- `POST /generate`, `POST /api/generate-from-s3`, `POST /api/chat`, `POST /api/upload-transcript`
- `POST /api/analyst-chat`, `POST /api/analyst-chat-stream`, `POST /api/analyst-warm`, `POST /api/analyst-generate-brd`
- `GET /api/analyst-history/{session_id}`, `GET /api/brd-history/{session_id}`
- `GET /api/brd/{brd_id}/structure`, `/sections`, `/section/{n}`
- `GET /api/download-brd/{brd_id}`
- `GET /api/brd/access`, `POST /api/admin/grant-brd-access`, `POST /api/admin/revoke-brd-access`
- `GET/PUT /api/projects/{project_id}/brd-session`
- `POST /api/brd-sync/compare`, `POST /api/brd-sync/apply` (separate flow from PM/Analyst agents)

**SAD endpoints (active):** see §4.1.

**Shared infrastructure:**

- AWS Bedrock AgentCore Runtime (PM + Analyst)
- AWS Bedrock AgentCore Memory `sdlc_dev_agentcore_memory-VF74Yf64ZB`, actor `analyst-session` (BRD) / `design-session` (SAD)
- AWS Lambda (5 functions)
- AWS S3 `sdlc-orch-dev-us-east-1-app-data`
- AWS KMS key `arn:…:key/mrk-29bf4d8d90604305976882df6c91149e`
- AWS RDS Postgres — `projects`, `analyst_sessions`, `design_sessions`
- Deluxe DLX AI Gateway, model `Claude-4.5-Sonnet`
- AWS account `590184044598`

---

## 6. Dead / unused code

### 6.1 Duplicate FastAPI handlers in `app.py` (silent shadowing)

FastAPI silently uses the **last** definition. Earlier ones are unreachable.

- `POST /api/analyst-generate-brd` defined **three times**: `app.py:2208`, `app.py:2438`, `app.py:2699`. Only `2699` is reachable. The earlier two contain 200+ lines of stale logic including a session-discovery fallback (`list_sessions` + `list_events`) at lines 2454-2521.
- The reachable handler at line 2699 has a bug at lines 2836-2841 — it references a local `messages` that was never assigned (upstream variable is `conversation_messages` at line 2779) → `NameError` on success path.
- `GET /api/analyst-history/{session_id}` defined twice: `app.py:2136` and `app.py:3227`. Only 3227 wins. The two implementations differ in message order and payload shape.

### 6.2 Lambdas defined but not deployed

- **`lambda_brd_retriever.py`** (226 lines) — exported as `DEFAULT_LAMBDA_BRD_RETRIEVER` in `env_vdi.py:99`, wired into `my_agent.py:269` as the `fetch_brd` tool, **not in `deploy_lambdas.py:45-71`**. The PM agent's `invoke()` never deterministically routes to `fetch_brd`; only the Strands LLM in the AGENT PATH branch could choose it, but the enhanced prompt steers toward `chat_with_brd`. Frontend reads BRD content via `/api/brd/{brd_id}/structure` (S3 direct). Dead in practice.

### 6.3 Dead prompts

- `prompts/brd_chat_prompts.py` defines `BRD_CHAT_PARSE_PROMPT` and `get_brd_chat_parse_prompt`. Not imported by `prompts/__init__.py`, not referenced anywhere except a copy in the build artifact `.deploy/sdlc-dev-brd-generator/prompts/`. The actual chat-parse prompt is now inlined in `lambda_brd_chat._parse_user_intent_with_llm` (`lambda_brd_chat.py:1031-1198`).

### 6.4 Backup & recovery artifacts

- `.agent_backup_20260116_124804/my_agent.py`, `.agent_backup_20260119_133722/my_agent.py` — older snapshots, dead.
- `BRD_recovered.json`, `BRD_recovered.txt`, `_recovered_messages.json` — leftover dumps at repo root, unused.

### 6.5 Stale Lambda build directories

- `lambda_chat_package/`, `lambda_generator_package/`, `lambda_retriever_package/` at repo root — superseded by `lambda_builds/` (per `deploy_lambdas.py:71`).
- `create_lambda_zip.py` — predecessor to `deploy_lambdas.py`, no consumer.

### 6.6 `analyst_agent.py` startup NameError

`analyst_agent.py:513-514` print statements reference `LAMBDA_REQUIREMENTS_GATHERING_ARN` and `LAMBDA_BRD_FROM_HISTORY_ARN`. The actual module locals (`:118-119`) are `LAMBDA_REQUIREMENTS_GATHERING` and `LAMBDA_BRD_FROM_HISTORY` (no `_ARN` suffix). **Module won't import cleanly**, meaning the AgentCore Runtime container `analyst_agent-JAa3wMFKOK` is effectively broken on cold start. Consistent with the frontend bypassing it via `/api/analyst-chat-stream` (which invokes Lambda directly).

### 6.7 Unreachable branches in `analyst_agent.invoke()`

- `enhanced_prompt`/`agent(...)` fallback at lines 449-493 reachable only if `gather_requirements(...)` raises, and even then the control flow has the early-`return` issue.
- Hardcoded "Mary" `system_prompt` at lines 324-341 only effective on that dead path.

### 6.8 `BedrockModel` branches in both BRD agents

`my_agent.py:476-478` and `analyst_agent.py:312-314`: `if AGENT_MODEL_PROVIDER == "bedrock"`. In VDI (production), `AGENT_MODEL_PROVIDER = "gateway"`. So `BedrockModel` is only reachable when someone uncomments `env_local` in `environment.py:22`. Dead in dev backend.

### 6.9 Tool `get_brd_conversation_history` registered but not picked

`my_agent.py:394-461`: registered in the Strands toolset (line 488) but the imperative branches A and B never call it, and AGENT PATH (C) calls it as a regular Python function (line 624), not as a tool. The LLM in C could theoretically pick it, but the enhanced prompt only documents `chat_with_brd` and `fetch_brd`. Dead as a tool; used only as a Python helper.

### 6.10 `/api/analyst-warm` only warms 2 Lambdas

The BRD chat Lambda (which the PM agent depends on) is not warmed. Probably intentional but worth flagging — there's a cold-start gap on the first chat after a quiet period.

### 6.11 Inconsistent S3 layout between PM and Analyst BRD output

- PM writes `brds/{id}/brd_structure.json` (`lambda_brd_generator.py:713`)
- Analyst writes `brds/{id}/BRD_{id}.json` (`lambda_brd_from_history.py:326`)
- Section-read endpoints (`app.py:3670`) only look at `brd_structure.json`. Analyst-generated BRDs can't be sectioned via `/api/brd/{id}/structure` unless `lambda_brd_chat.backfill_brd_structure` runs later via a chat operation.

### 6.12 Dead Lambda-side analyst metrics path

`_capture_strands_metrics` in `analyst_agent.py:56-76` is only called from the dead fallback path (`:462`). The active streaming path doesn't run it.

### 6.13 SAD: handled actions vs documented actions

- `get_history` listed in `lambda_sad_orchestrator.py:13` module docstring, **not in dispatch table** (`:517-522`). Callers sending `action:"get_history"` fall through to 400. History is served by `routers/design_sessions.py:323-369` instead.
- `LINK_DIAGRAM` intent declared in `SAD_INTENTS` and routed by the intent prompt, but the Lambda just returns a stub text card "Re-linking the saved diagram is handled by the backend; this is a no-op in the Lambda for now." (`lambda_sad_orchestrator.py:842-843`). No backend `/sad/link-diagram` endpoint exists.

### 6.14 SAD: dead RAG branch

- `_generate_section_content` declares `rag: List[Dict[str, Any]] = []`; no caller ever sets `event["rag_chunks"]`. Sections 3 (ARSR) and 8 (Risks) include `_format_rag(rag)` in their prompts (`prompts/sad_section_prompts.py:310, 441`) which always renders "(no RAG context)".
- `services/rag_service.py` (666 lines) is real but not used by SAD.

### 6.15 SAD: silently lost BRD grounding on `/turn`

- `_load_brd_for_session` (`lambda_sad_orchestrator.py:622-643`) reads `brd_id` from `load_sad(session_id)["brd_id"]`. The router never passes `brd_id` on `/turn` (only on `/generate`, `routers/sad.py:421`), and `load_sad` returns `None` until the first generation. The Lambda degrades to "(no BRD available)" on edit/regen turns until a SAD exists. Silent loss of grounding.

### 6.16 SAD: unused gateway features

- `return_metadata` parameter in `llm_gateway.chat_completion` (`llm_gateway.py:103, 148-152`) — SAD never passes it.
- `chat_completion_with_tools` (`llm_gateway.py:254-310`) — zero hits.
- `chat_completion_stream` (`llm_gateway.py:156-251`, `env_vdi.py:49-73`) — zero hits in SAD.

### 6.17 SAD: card types declared on frontend but never emitted

`generation_progress` / `generation_complete` declared in `deluxe-sdlc-frontend/src/services/sadApi.ts:27-28`. Lambda only emits `generation_starting` (`lambda_sad_orchestrator.py:841`). Frontend handlers for the other two are dead.

### 6.18 SAD: dead service references for SAD specifically

These services exist and are functional elsewhere but are NOT used by SAD:

- `services/figma_service.py` + `routers/figma.py` — Jira flow only.
- `services/github_service.py` — `routers/test_generation.py` only.
- `services/lineage_service.py` — BRD/Jira/Test only.
- `services/bitbucket_service.py`, `services/checkov_service.py` — pipeline / test flows.
- `services/rag_service.py`, `services/embedding_service.py`, `services/search_service.py` — BRD-side RAG only.
- `services/sync_service.py` — Atlassian sync.
- `services/lucid_mcp_client.py` — legacy one-click Lucid MCP path; superseded by `POST /api/design/lucid/import`.

### 6.19 SAD: Lambda bundle doesn't ship most services

The deployed `lambda_builds/sad-orchestrator/services/` ships only `__init__.py` + `s3_service.py` (`deploy_lambdas.py:83`). `confluence_service`, `figma_service`, `lucid_*` are NOT in the zip — the Lambda has no path to those services even if it tried to import them.

### 6.20 SAD: dead config flags

- `DEFAULT_AGENTCORE_GATEWAY_ID` (`env_vdi.py:109`) — never read by SAD.
- `DEFAULT_AGENTCORE_ACTOR_ID = "analyst-session"` (`env_vdi.py:108`) — SAD overrides with `SAD_AGENTCORE_ACTOR_ID` defaulting to `"design-session"` (`lambda_sad_orchestrator.py:53`). The base default is for the analyst path.
- `BEDROCK_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` (`env_vdi.py:92-93`) — used by `embedding_service`, not SAD.
- `DEFAULT_LAMBDA_*` ARNs for BRD lambdas — not used by SAD.

### 6.21 SAD: orphan DB fields

- `design_sessions.confluence_page_id` — set only by the diagram-save path (`routers/design.py:1476`); never read by any SAD code.
- `design_sessions.sad_id` — declared in schema, exposed in `update_design_session(sad_id=...)` (`db_helper.py:999, 1019-1020`); **not set by any SAD code path**. The Lambda uses `session_id` as the `sad_id` (`lambda_sad_orchestrator.py:1455, 1480`) and persists it inside `sad_structure.json` rather than back to the DB column. The column is effectively orphaned.
- Legacy `diagram_s3_key`, `diagram_svg_s3_key` — still bumped on `logical` saves (`routers/design.py:1465-1477`); `routers/sad.py` reads exclusively from JSONB + S3-by-convention paths.

### 6.22 Misleading docstrings (do not match code)

- `routers/sad.py:11` — claims `/turn` supports "SSE-style chunks". It does not; single JSON response.
- `lambda_sad_orchestrator.py:9-11` — claims streaming via SSE-style chunks "when invoked by the backend with InvocationType=RequestResponse using a chunked-response shim". **No such shim exists.**
- `lambda_sad_orchestrator.py:13` — lists `get_history` as a handled action; dispatcher doesn't register it.
- `prompts/sad_section_prompts.py:24-26` — claims "Workers that produce the diagram-bearing sections (4, 6, 7) are responsible for prepending the `diagram` block themselves". Actually the orchestrator (not the LLM) prepends it via `_diagram_block_for_section` (`:1413-1416`). The LLM is explicitly told NOT to emit diagram blocks (`:49`).

### 6.23 Secret hygiene flag

`env_vdi.py:86`: `DEFAULT_DLXAI_GATEWAY_KEY = "sk-2cdb551cf35f418ea88b36"`. Not dead code, but a hardcoded API key worth rotating and moving to a secret store.

### 6.24 Repo-level noise (not deployed)

- `.scratch/`, `confluence_dump/`, `node_modules/`, `package-lock.json`, `package.json` — local-only.
- `__pycache__/` directories shipped inside `lambda_builds/sad-orchestrator/` — harmless but adds weight to the zip.

---

## 7. Files / lines worth opening first

For PM agent work: `my_agent.py`, `lambda_brd_chat.py`, `lambda_brd_generator.py`, `app.py:781-1414`.

For Analyst agent work: `analyst_agent.py`, `lambda_requirements_gathering.py`, `lambda_brd_from_history.py`, `app.py:1498-2980`, `prompts/requirements_gathering_prompts.py`.

For SAD agent work: `routers/sad.py`, `lambda_sad_orchestrator.py`, `prompts/sad_*.py`, `db_helper.py:910-1215`, `migrations/add_design_sessions.py`, `migrations/add_design_diagram_slots.py`.

For deployment & infra: `deploy_lambdas.py`, `env_vdi.py`, `llm_gateway.py`, `environment.py`, `Dockerfile`, `ecs-task-def-dev.json`, `DEPLOY_RUNBOOK.md`.

For prompts not yet in use: `prompts/brd_chat_prompts.py` (dead), section-prompt RAG block (dead).

---

## 8. Suggested clean-up backlog (prioritised)

The audit didn't make these changes — listed here for your decision.

1. **Remove dead duplicates** in `app.py`: the two earlier `analyst-generate-brd` handlers (lines 2208, 2438) and the earlier `analyst-history` handler (2136). High risk of future confusion.
2. **Fix `analyst_agent.py:513-514`** NameError (rename to drop the `_ARN` suffix) — the AgentCore Runtime is currently broken at import.
3. **Fix `app.py:2836-2841`** `messages` vs `conversation_messages` NameError in the reachable `/api/analyst-generate-brd` handler.
4. **Decide on `lambda_brd_retriever`**: either deploy it and route `fetch_brd` deterministically, or delete the file + its env var + its tool wiring.
5. **Decide on `prompts/brd_chat_prompts.py`**: delete, or move the inlined prompt back into it for consistency.
6. **Decide on `LINK_DIAGRAM` intent**: either implement the backend endpoint or remove the intent from `SAD_INTENTS` and the stub card.
7. **Decide on RAG for SAD**: either wire `services/rag_service.py` into `/sad/turn` and `/sad/generate` payloads (set `rag_chunks` from BRD content), or drop the `_format_rag` block from section prompts.
8. **Pass `brd_id` on `/sad/turn`** to avoid silent loss of grounding when editing sections (`routers/sad.py:353-365` + Lambda persistence).
9. **Resolve S3 layout mismatch** between PM and Analyst BRD output (`brd_structure.json` vs `BRD_{id}.json`) so section reads work uniformly.
10. **Fix docstrings** in `routers/sad.py:11`, `lambda_sad_orchestrator.py:9-13`, `prompts/sad_section_prompts.py:24-26`.
11. **Rotate the hardcoded gateway key** in `env_vdi.py:86`.
12. **Remove backup/legacy directories** at repo root once you've confirmed nothing references them (`.agent_backup_*`, `lambda_chat_package/`, `lambda_generator_package/`, `lambda_retriever_package/`, `BRD_recovered.*`, `_recovered_messages.json`, `create_lambda_zip.py`).
