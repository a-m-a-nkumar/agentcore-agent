# Velox — SDLC Orchestration Platform

*Internal AI-powered platform that takes an SDLC project from "we have an idea" to "the code is shipping", end to end, with the engineer in the driver's seat and AI doing the rote work.*

---

## 1. The goal

Software delivery at the company runs across many systems — Confluence for product docs, Jira for tickets, Bitbucket for code, Harness for pipelines, Katalon for test execution, Lucid / draw.io for architecture diagrams, Figma for design. The act of pulling a coherent thread through all of them — drafting a BRD, deriving a Software Architecture Document from it, breaking that down into Jira stories, generating test scenarios, getting the right people access, configuring the pipeline — eats an enormous amount of senior-engineer and PM time. Every team reinvents the wheel slightly differently, and the artifacts drift apart almost immediately.

Velox is the platform that does the whole orchestration in one place. The engineer / PM uses it to:

- Generate a BRD from a meeting transcript, or from a conversational chat with an AI analyst
- Iteratively edit that BRD section-by-section using natural language
- Push the BRD or specific sections back to Confluence as the source of truth
- Turn the BRD (or a Confluence page) into Jira epics / stories / tasks, with acceptance criteria, and create them directly in Jira
- Draft architecture diagrams (logical, infrastructure, security) using draw.io or Lucid, store them per session, and embed them automatically into a generated Software Architecture Document (SAD) that follows the in-house template
- Generate Gherkin test scenarios from any Confluence test specification, push them to a Katalon-compatible GitHub repo, and use Katalon AI to materialize the actual test cases
- Set up the MCP integration so engineers' IDEs (Cursor, VS Code Copilot, Claude Code) can query the platform's project knowledge base from inside the editor
- Track every LLM token spent per user, plus a per-module activity ledger so leadership has visibility into adoption

The product is positioned as **the default way** engineers and PMs interact with the SDLC at Deluxe — not a side tool, not an emergency hatch. Once a project starts in Velox, every downstream artifact is reachable from the same place.

---

## 2. Product surface — the modules

Each module is a self-contained tool inside the same SPA. Sidebar entries are gated by Azure AD group, and what a user sees depends on whether they're in the TECH group, the BUSINESS group, both, or neither.

### 2.1 BRD Assistant

**Purpose.** Replace the multi-day "draft a Business Requirements Document by hand" exercise with an LLM-driven flow that produces a structured 16-section BRD in 60–120 seconds and then lets the user iterate on it in plain English. The BRD is the single source of product truth that downstream modules (Jira story generation, SAD authoring, Code Intelligence drift checks) all key off, so it has to be both fast to produce and reliable to edit.

**Who.** BUSINESS group users (PMs, Business Analysts). TECH users see the read-only surface but can't kick off generation.

**Entry points (UI).**
- "Create BRD from transcript" wizard — three-step: pick template, upload transcript, review & generate
- "Create BRD from conversation" — uses the existing Analyst session as input
- BRD chat pane — once a BRD exists, every project has a persistent chat thread

**Storage shape.**
```
s3://sdlc-s3-app-data/brds/{brd_id}/
├── brd_structure.json          # the 16-section structured BRD (single source of truth)
├── brd_text.txt                # plaintext mirror, used by RAG + Code Intelligence
├── transcript.txt              # input transcript (extracted)
└── source_template.docx        # template the user chose
```
The `brds` table on RDS holds `{brd_id, project_id, user_id, title, current_version, status, agentcore_session_id, created_at}`.

**Backend flow — transcript path.**
1. Frontend `POST /api/upload-transcript` (multipart, KMS-encrypted PUT into `transcripts/{session_id}/{filename}`).
2. Frontend `POST /api/generate-from-s3` with `{transcript_s3_key, template_s3_key, user_id}`.
3. Backend reads both files (`extract_text()` dispatches by extension: `pypdf` for PDF, `python-docx` for DOCX, native open for TXT), invokes the PM Agent via `bedrock-agentcore.invoke_agent_runtime`.
4. PM Agent recognises "template + transcript present" as the `generate_brd` shortcut, skips Strands reasoning, invokes the `brd-generator` Lambda directly.
5. Lambda issues one big `chat_completion` call (~60–120s, ~16K output tokens) against Claude Sonnet 4.5 with a structured-output system prompt.
6. Lambda parses the structured markdown → 16 section JSONs, writes `brd_structure.json` + `brd_text.txt` to S3, returns `brd_id`.
7. Frontend redirects to the BRD viewer; the viewer fetches `/api/brd/{brd_id}/sections` and paints.

**Backend flow — section editing (the iterative path).**
1. User types into the chat pane → `POST /api/chat` with `{brd_id, session_id: "brd-session-{brd_id}", message, user_id}`.
2. Backend invokes PM Agent. Agent extracts user_id, scans for unambiguous-command keywords (`update`, `show`, `expand`, `remove`, `list`, `summarize`, …) — if matched it takes the **DIRECT PATH**: skip the Strands LLM, hand the raw message straight to the `brd-chat` Lambda. This was added after Strands' reasoning loop was found to mis-route ~15% of clear commands.
3. `brd-chat` Lambda runs a small intent-classifier LLM call → `{intent, section_number, instruction}`.
4. For `EDIT_SECTION`: Lambda loads the BRD JSON, sends a section-specific prompt containing **the full current section JSON + the user instruction + the section schema**, gets a complete replacement JSON object, validates the title matches (guard against the model rewriting headers), swaps the section in `brd_structure.json`, regenerates `brd_text.txt`.
5. Both user + assistant turns persist to AgentCore Memory keyed by `brd-session-{brd_id}`, actor `brd-session`.
6. Response card returned to UI; that section rerenders in place. No full-document refresh.

**Notable details.**
- BRD generation is one-shot, not streamed — the user sees a progress dot for the duration. Streaming was tried and the UX was *worse* because the structured JSON shape is hard to render incrementally.
- The "title must match after edit" guard catches the ~3% of cases where Claude rewrites a section header. On mismatch the Lambda returns a soft error and the UI prompts the user to retry.
- Re-generation of a full BRD overwrites `brd_structure.json` in place; there's no version history in v1 (tracked as a known gap).
- The BRD can be pushed to Confluence via the Confluence module (creates a page or updates an existing one with the DOCX rendered server-side).

### 2.2 Analyst

**Purpose.** Replace the transcript upload with a guided conversation for PMs who don't have a transcript handy. The analyst LLM is trained (via prompt) to act like an experienced Business Analyst — proactively probing for the gaps in the user's narrative (stakeholders, success metrics, NFRs, risks, out-of-scope, integration points) rather than passively transcribing what the user says. Sessions persist forever; a PM can leave a session mid-conversation and return weeks later.

**Who.** BUSINESS group users primarily; TECH users have access for technical-requirements-gathering sessions.

**Entry points.**
- "Start new analyst session" button on the project workspace
- Session sidebar — list of all the user's analyst sessions for the current project, with stage chips ("Gathering", "BRD-ready", "BRD-generated")
- "Generate BRD from this conversation" CTA inside an active session, enabled once the conversation has enough material

**Storage shape.**
```
RDS: analyst_sessions(session_id PK, project_id, user_id, name, stage, created_at, last_activity_ts)
AgentCore Memory: actor_id="analyst-session", session_id=<UUID>
  → full message history, retained indefinitely
```
No S3 artifact during the gathering phase — everything lives in AgentCore Memory until BRD generation produces an S3 BRD.

**Backend flow — chat turn.**
1. Frontend `POST /api/analyst-chat-stream` (multipart: message + optional file) with `{session_id, user_id, message}`.
2. Backend resolves the session row from RDS, ensures stage is `GATHERING`, persists the user turn to AgentCore Memory.
3. Backend invokes the **Analyst Agent** (AWS Bedrock AgentCore Runtime, Strands-built). The agent has access to:
   - Full conversation history via AgentCore Memory's retrieval API
   - A `requirements-gathering` Lambda tool for follow-up question generation
   - A `summarize_session` tool for the user when they ask "what have we covered so far"
4. The agent's response streams back via SSE → frontend renders token-by-token.
5. Assistant turn persists to AgentCore Memory; activity event recorded.

**Backend flow — BRD generation from session.**
1. Frontend `POST /api/analyst/generate-brd` with `{session_id, template_s3_key}`.
2. Backend pulls the full message history from AgentCore Memory (typically 20–80 turns).
3. Invokes `lambda_brd_from_history` with `{messages, template, user_id}`.
4. Lambda runs one big `chat_completion` against the conversation transcript — same structured output as the transcript path produces.
5. Writes `brds/{brd_id}/...` to S3, links `brd_id` back onto the `analyst_sessions` row, returns `brd_id`.
6. Frontend redirects to the BRD viewer.

**Notable details.**
- The conversation continues even after BRD generation — the user can ask the analyst to refine specific areas and re-generate.
- File uploads inside a session (PDF reference docs, transcripts of separate meetings) extract via `extract_text()` and append to the next user turn as supplementary context — they're not stored separately for v1.
- The session sidebar is the same component pattern reused later by the Design Assistant's session sidebar (per-project session list + new/rename/delete).
- The "warm-up" endpoint `/api/analyst-warm` preheats the Analyst Agent on session open to keep first-turn latency under 4 seconds.
- Token cost per session is highly variable: ~$0.05 for short sessions, ~$0.40 for sessions that get into BRD generation territory.

### 2.3 Confluence

**Purpose.** Make Confluence — already the company's de-facto product documentation hub — a first-class input source to Velox. Any page that documents requirements, test scenarios, or architecture intent should be reachable inside Velox without copy-paste. The integration is per-user (each engineer's own PAT) because Atlassian's permission model is per-user; we never get bulk access.

**Who.** All users with the Confluence module enabled (typically everyone). Page-level RBAC is enforced upstream by Confluence itself.

**Entry points.**
- "Connect Atlassian" modal on `/profile` — single text input for the API token + the user's atlassian-cloud domain + their email
- Confluence Browser page — left rail of spaces, right pane with the selected space's page tree
- Inline page viewer — renders Confluence HTML with the platform's editorial styling
- "Generate Jira Items" button (BUSINESS only) on every page
- "Generate Test Scenarios" button on every page
- "Push BRD to Confluence" button on the BRD viewer

**Storage shape.**
```
RDS: users.atlassian_domain, users.atlassian_email,
     users.atlassian_api_token (KMS-encrypted "kms:<base64>" or plaintext),
     users.atlassian_linked_at
```
Page contents are never cached server-side — every render is a live fetch.

**Backend flow — link credentials.**
1. Frontend `POST /api/integrations/atlassian/link` with `{api_token, domain, email}`.
2. Backend `JiraService(domain, email, api_token).test_connection()` — cheap call to `/rest/api/3/myself`.
3. On 200: encrypt token with `_encrypt_token()` (KMS if `KMS_KEY_ARN` set, plaintext warning otherwise), `UPDATE users SET atlassian_* = ...`.
4. Invalidate the per-user validation cache (5-minute TTL on `/atlassian/status` responses).

**Backend flow — browse + render.**
1. `GET /api/confluence/spaces` → decrypts the PAT, calls Confluence REST API `/wiki/api/v2/spaces` paginated, returns shaped list.
2. `GET /api/confluence/pages?space_key=X` → list pages, with parent/child tree assembly server-side.
3. `GET /api/confluence/page/{page_id}` → fetches `body.storage` HTML; some inline image / macro post-processing happens server-side; HTML returned to frontend.

**Backend flow — push BRD to Confluence.**
1. Frontend on the BRD viewer clicks "Push to Confluence" → pick a space + parent page.
2. `POST /api/confluence/push-brd` with `{brd_id, space_key, parent_page_id, title}`.
3. Backend reads `brd_structure.json`, renders to Confluence-storage HTML (custom translator — preserves headings, tables, ordered/unordered lists, code blocks).
4. Creates a new page via `POST /wiki/api/v2/pages` or updates existing via `PUT`.
5. Records activity event `confluence_brd_pushed`.

**Notable details.**
- The Atlassian PAT save was a recurring outage in early rollouts: KMS AccessDenied on the encrypt call. Root cause was always either (a) the customer-managed KMS key's resource policy missed the ECS task role, or (b) the `KMS_KEY_ARN` env var wasn't set on the task. The fallback to plaintext storage in local dev avoids needing KMS at all for local work.
- "Generate Test Scenarios" routes through the Testing module's pipeline — see §2.7 below.
- "Generate Jira Items" hands off to the Jira module — see §2.4.
- "Page not found" / "no permission" upstream Confluence errors surface to the user with the exact Confluence error string, never a generic 500, so engineers can self-diagnose access issues.
- A user with no linked Atlassian credentials sees a friendly "Connect Atlassian to use this module" CTA instead of an error.

### 2.4 Jira

**Purpose.** Close the loop from "we have requirements" to "they're tracked tickets in Jira." Story generation is LLM-driven — given a BRD section or a Confluence page, the platform produces titled stories with acceptance criteria, points / priority hints, and reviewer fields the user can edit before push.

**Who.** All users with Jira module access. Project-level Jira permissions enforced upstream.

**Entry points.**
- Project setup wizard — pick a Jira project at project creation time
- Jira module main page — browse the linked project's issues, basic filters
- "Generate Jira Items" button on BRD section or Confluence page (BUSINESS-gated)
- "Edit + Push to Jira" preview pane

**Storage shape.**
```
RDS: projects.jira_project_key, projects.jira_project_id
     (no separate Jira creds — reuses users.atlassian_* from Confluence)
```

**Backend flow — list user's projects.**
1. `GET /api/jira/projects` (with optional `?search=` and `?cursor=` for pagination).
2. Backend decrypts the user's Atlassian PAT, calls `/rest/api/3/project/search?startAt=N&maxResults=50` in a loop until exhaustion (the older non-`/search` endpoint capped at 50 silently — a production bug we fixed).
3. Returns shaped list with `{key, name, projectTypeKey, lead}`.

**Backend flow — generate stories from a source.**
1. Frontend `POST /api/jira/generate-stories` with `{source_type: "brd_section"|"confluence_page", source_id, source_subset, target_project_key}`.
2. Backend fetches the source content (BRD section JSON, or Confluence storage HTML stripped to text).
3. Runs an LLM call with a story-generation prompt — outputs structured `{stories: [{title, description, acceptance_criteria[], story_points, priority, labels, components}]}`.
4. Returns to the preview pane without yet pushing.
5. User accepts / rejects / edits per row in the UI.
6. Frontend `POST /api/jira/create-issues` with the accepted subset.
7. Backend bulk-creates via Jira REST `/rest/api/3/issue/bulk`, returns issue keys.
8. Activity event `jira_stories_created` with count.

**Notable details.**
- The story generator is constrained by prompt to produce **stories**, not epics or sub-tasks — keeping the cognitive load manageable for a single LLM pass. Epic generation is a separate flow (also LLM-driven, but with stricter constraints on "1 epic per BRD section, max").
- The preview pane is mandatory — we never push to Jira without explicit per-row confirmation. The early-rollout version auto-pushed and produced ticket spam in two pilot teams; the preview gate is the fix.
- Story duplication detection (a story for "OAuth login flow" already exists in the target project) is not done in v1 — tracked as a gap.
- Jira's API rate limits (10 req/sec per user) constrain bulk creates; the backend chunks creates into batches of 20 with brief sleeps when story count > 50.

### 2.5 Architecture / Design Assistant

**Purpose.** The diagram-authoring half of the SAD pipeline. Engineers produce up to three architecture views — Logical, Infrastructure, Security — using their tool of choice, and the platform stores each one against the session so the SAD generator (§2.6) can embed them. Critically the module supports **partial authoring**: an engineer can save only the Logical view and explicitly skip the other two, and the SAD will produce placeholders for the missing sections rather than silently substituting one diagram for all three (the v0 behavior).

**Who.** TECH group primarily (engineers, architects). BUSINESS users can view sessions read-only.

**Entry points.**
- Architecture module main page → session sidebar (per-project)
- "Create new session" → tool picker (draw.io / Lucid) → hub
- Diagram hub — three slots (Logical / Infrastructure / Security), each with status badge + Generate/Reopen/Skip actions
- "Continue to SAD" CTA on the hub when at least one diagram is `done`

**Storage shape.**
```
RDS: design_sessions(session_id PK, project_id, user_id, name, stage, 
                     diagram_slots JSONB, sad_id, last_activity_ts)

diagram_slots = {
  "logical":        {"status": "done"|"pending"|"in_progress"|"skipped"|"failed",
                     "tool": "drawio"|"lucid",
                     "artifact_key": "sessions/{id}/diagram/logical.svg",
                     "lucid_document_id": "...", "saved_at": "..."},
  "infrastructure": {...},
  "security":       {...}
}

s3://sdlc-s3-app-data/sessions/{session_id}/
├── diagram/
│   ├── logical.xml          # mxGraph (drawio source of truth)
│   ├── logical.svg          # rendered, self-contained
│   ├── infrastructure.svg   # could be drawio or lucid-sourced
│   ├── security.svg
│   └── source_pages.json    # which Confluence pages fed the prompt
├── sad/...
└── sources/{upload_id}__{filename}  # docs attached during SAD chat
```

**Backend flow — draw.io path.**
1. User picks draw.io as the tool, hub opens.
2. User clicks Generate for Logical → editor frame mounts with `embed.diagrams.net` iframe.
3. Frontend optionally generates a Claude prompt from selected Confluence pages (`POST /api/design/generate-prompt`); user pastes into the iframe via the iframe's `setActions` `postMessage` API.
4. User iterates inside the iframe.
5. On Save: frontend sends `{action:"export", format:"xmlsvg", embedImages:true}` via `postMessage`. Iframe returns both the mxGraph XML and an embedded SVG.
6. Frontend `POST /api/design/save-diagram` with `{session_id, diagram_type:"logical", xml, svg}`.
7. Backend `s3_put_object` writes both to `sessions/{id}/diagram/logical.{xml,svg}`. `update_diagram_slot()` patches the JSONB column.

**Backend flow — Lucid path (REST API key).**
1. User picks Lucid as the tool.
2. **Plate 04 — Import** appears at the bottom of the Lucid pane. If the user hasn't linked their Lucid API key yet, the pane shows a "Link Lucid in Profile" CTA.
3. User generates the diagram inside Lucid.app as they normally would (we provide a Claude-generated prompt + an "open in Lucid AI" link).
4. Back in Velox, user clicks Refresh in Plate 04 → `GET /api/design/lucid/documents?search=...&suggest=...`.
5. Backend reads `users.lucid_api_key` (KMS-decrypted), instantiates `LucidAPIService`, calls Lucid's `POST /documents/search` with `{"product":["lucidchart"], "keywords": search, "pageSize":50, "excludeTrashed": true}` — returns the user's recent docs.
6. User picks one, clicks "Fetch & Save".
7. `POST /api/design/lucid/import` with `{session_id, document_id, diagram_type}`.
8. Backend `LucidAPIService.export_document(doc_id, fmt="svg")` → `GET /documents/{id}/contents?format=svg` → SVG bytes.
9. Writes to `sessions/{id}/diagram/{type}.svg`, `update_diagram_slot()` patches `diagram_slots` with `{status:"done", tool:"lucid", artifact_key, lucid_document_id}`.
10. Frontend renders preview, slot turns green on the hub.

**Backend flow — Lucid path (legacy OAuth/MCP).**
- Kept as a one-click shortcut: clicking "Issue to Lucid AI" hits an OAuth-protected MCP endpoint that opens lucid.app with the prompt pre-filled.
- This path captures only the edit URL, not the artifact — so the user has to come back and use the API-key import path to actually save the diagram for SAD generation.

**Notable details.**
- The session is the unit of isolation: a single project can have "Auth flow v3" and "Cart redesign v2" sessions in parallel, each with their own diagrams and SADs. Sidebar mirrors the analyst module.
- Per-type slots were added late: v0 had a single diagram per session and the SAD reused it for §4 / §6 / §7. The new schema (`diagram_slots JSONB`) is additive — old sessions auto-migrate on first read (`migrate_legacy_single_slot()`).
- Tool switching mid-session is allowed: artifacts from the old tool stay as read-only slots, new diagrams use the new tool.
- The diagram-phase frontend was redesigned end-to-end (see [`.interface-design/system.md`](.interface-design/system.md) in the frontend repo): tool selection → hub → focused single-diagram editor → pre-flight confirm → SAD generation. The editor uses `react-resizable-panels` for the Confluence-pages-pane + iframe split.
- Lucid's API has a quirk: `product` must be a JSON array (`["lucidchart"]`), not a string. Took two debug rounds to discover.
- All Lucid keys are region-pinned at issuance (`-Lucid-US` / `-Lucid-EU` suffix). v1 hardcodes the US base URL; EU support is a known gap.
- The frontend uses localStorage as a fallback for slot state during local dev when the backend hasn't been migrated — the backend writes win when present.

### 2.6 SAD — Software Architecture Document

**Purpose.** The capstone deliverable. A 10-section structured document matching the Deluxe SAD template — the artifact every architecture review at the company expects. Generation is parallelized across sections, embeds the user's saved diagrams, audits its own quality, and supports natural-language iteration after the fact. The SAD lives inside the same Design session as the diagrams so context flows through.

**The 10 sections (Deluxe template).**

| # | Section | Notable shape |
|---|---|---|
| 1 | Summary | 1 paragraph |
| 2 | Problem Statement | 1 paragraph |
| 3 | Architectural Significant Requirements | In-scope table (17 fixed category rows: Frontend, API Decisions, Data Storage, Auth, Scalability, Deployment, Backup, Monitoring, DR, Load Balancing, Agent Runtime, Processing Layer, AI/LLM Layer, Object Storage, API Protection, IAM, Networking) + Out-of-scope table |
| 4 | **Logical Architecture Diagram** | Embedded SVG + numbered narrative |
| 5 | Pending Decisions | Table |
| 6 | **Security View** | Embedded SVG (security diagram) + highlight bullets |
| 7 | **Infrastructure Architecture Diagram** | Embedded SVG + notes |
| 8 | Architecture Risks and Mitigations | 4-column table |
| 9 | Non-Functional Requirements | Numbered, grouped by Performance / Security / Maintainability / Observability / Backup-DR |
| 10 | Infra Cost Estimate | Link placeholder |

**Who.** TECH group (architects, engineers). BUSINESS users can read but not generate/edit.

**Entry points.**
- "Continue to SAD" button on the Design hub
- Direct URL to a session with stage ≥ `SAD_GATHERING`
- The session sidebar's "SAD draft" / "SAD final" chips

**Storage shape.**
```
s3://sdlc-s3-app-data/sessions/{session_id}/sad/
├── sad_structure.json    # the live SAD (single source of truth, atomic writes)
├── sad.txt              # plaintext mirror for grep / RAG
├── facts.json           # facts buffer accumulated from chat
└── audit_latest.json    # last audit result with per-section badges

RDS: design_sessions.sad_id (== session_id by convention; reserved for future split)
AgentCore Memory: same actor as the diagram phase ("design-session") — chat continues
```

**Backend flow — generation.**
1. User clicks "Generate SAD" → pre-flight confirm screen shows slot mapping (Logical→§4, Security→§6, Infra→§7) with status badges per row.
2. User confirms → `POST /api/sad/generate` (SSE).
3. Backend reads BRD JSON (if linked), `diagram_slots`, AgentCore Memory history, `facts.json`, any session-uploaded source docs.
4. Invokes `lambda_sad_orchestrator` with the assembled context (~50KB payload).
5. Lambda spawns 10 parallel workers via `concurrent.futures.ThreadPoolExecutor(max_workers=10)`.
6. Each worker runs a section-specific prompt (`prompts/sad_section_prompts.py`) → structured JSON.
7. For §4/§6/§7: if `diagram_slots[<matching_type>].status == "done"`, worker prepends `{type:"diagram", s3_key: <artifact_key>}` block. If skipped/missing: prepends a placeholder block.
8. Orchestrator assembles, writes `sad_structure.json` atomically, streams `section_complete` SSE events to the frontend as each worker returns.
9. Total time: 60–120 seconds typical (10 parallel sections, ~12s each).
10. **Auto-audit** kicks off immediately after generation completes: 10 parallel audit prompts run against each section's JSON, return per-section `{status: "green"|"amber"|"red", issues: [...]}`. Written to `audit_latest.json`.
11. Frontend SAD viewer paints badges (✅⚠️🚫) on the section list.

**Backend flow — single-chat-box iteration (the SAD pane's defining UX).**
Every user turn (text + optional file + currently-viewed section) → `POST /api/sad/turn`. Backend invokes Lambda `handle_turn`, which:
1. Runs the **intent router prompt** (~600 tokens, ~200ms LLM call) → classifies into one of:

| Intent | Trigger example | Handler |
|---|---|---|
| `EDIT_SECTION` | "update section 3" | Section-edit LLM call → swap JSON |
| `SHOW_SECTION` | "show §4" | Read-only fetch |
| `ADD_INFO` | "we use Bedrock" (no edit verb) | Append to facts buffer, suggest target section |
| `INGEST_DOC` | file attached, or Confluence URL pasted | Extract text, persist as fact (50K cap), regen-proposed=true |
| `AUDIT` | "audit", "review" | Run audit pass, return badges |
| `SUGGEST` | "any risks I should add?" | LLM produces 3–5 suggestions with Apply buttons |
| `ASK_QUESTION` | "what does our SAD say about auth?" | RAG over SAD content + answer with citations |
| `REGENERATE_SECTION` | "regen §4" | Re-run section's drafting prompt with current facts |
| `GENERATE_NEW_SAD` | "generate now" | Stage transition, spawn full generation |

2. Dispatcher routes to the right handler, returns a typed **card** (one of: `text`, `fact_saved`, `doc_ingested`, `section_view`, `section_updated`, `audit`, `suggestions`, `generation_starting`, `generation_progress`, `generation_complete`).
3. Frontend renders the matching card component.

**Confluence URL ingestion.** A URL pasted into the SAD chat (e.g. `https://deluxe.atlassian.net/wiki/spaces/X/pages/12345/Title`) triggers a synthetic file ingest:
1. Router parses `(domain, page_id)` from the URL.
2. Backend resolves user's Atlassian creds. If tenant mismatch → synthetic warning card ("link your account in that tenant").
3. `ConfluenceService.get_content_page_by_id(page_id, expand="body.storage")` → strip HTML to text → build a `file_payload` indistinguishable from an upload.
4. `_do_ingest_doc()` persists as a fact. Multiple URLs in one message → multiple `doc_ingested` cards; `auto_regen` flag only on the last one.

**Notable details.**
- **Never silently rewrite user-edited content.** When a fact arrives that affects a section, the response card asks: *"I saved this fact. It looks relevant to §3 — Authentication. Update §3 now / Save for later / Just save the fact."*
- The intent router replaces a full Strands agent on purpose. With ~2–3s per turn vs Strands' 4–8s, and command-style messages being ~70% of the workload, the bypass is a major UX win. Same logic as the BRD chat's DIRECT PATH.
- DOCX export: `cairosvg` converts diagram SVGs to PNG inline; `python-docx` assembles the rest.
- v1 reuses the same SVG for §4 / §6 / §7 unless the user has explicitly authored separate diagrams per type — the per-type slots make this clean.
- The audit prompts include per-section type checks: §3 requires every category row filled (no "TBD"), §4 requires the narrative references components in the mxGraph XML, §8 requires all 4 columns per risk, §9 requires at least one NFR per group.
- The "facts buffer" is a separate JSON file (not part of `sad_structure.json`) so users can add context without forcing a regen.
- Section-level revert is supported via `POST /api/sad/revert-section` — keeps the prior version on every edit.

### 2.7 Testing

**Purpose.** Bridge from "we have a test specification in Confluence" to "Katalon Studio is executing tests." The platform handles the LLM-heavy middle: parsing natural-language scenarios into structured form, generating Gherkin `.feature` files with traceability tags, and pushing to a GitHub repo Katalon Studio is configured to pull from. Katalon AI Assistant then takes the Gherkin and produces executable Katalon test cases. We don't run the tests — Katalon does that.

**Who.** All users with Testing module access (TECH primarily; BUSINESS can read).

**Entry points.**
- Testing module landing page — left rail of past test generations, main pane shows the active generation
- "Generate Test Scenarios" button on any Confluence page viewer (in §2.3) — kicks off the streaming generation flow
- "Push to GitHub" pane after generation completes — feature files + GitHub repo + branch + PAT

**Storage shape.**
```
s3://sdlc-s3-app-data/test-generations/{generation_id}/
├── source.txt              # extracted source page content
├── scenarios.json          # structured test scenarios (LLM-parsed)
└── features/
    ├── login-flow.feature
    ├── checkout-flow.feature
    └── ...

RDS: test_generations(generation_id PK, project_id, user_id, source_type, 
                      source_id, github_repo, github_branch, pushed_at)
```

**Backend flow — generate from Confluence (streaming).**
1. User clicks "Generate Test Scenarios" on a Confluence page.
2. Frontend `POST /api/test-generation/from-confluence-stream` (SSE) with `{page_id}`.
3. Backend fetches Confluence page content via `ConfluenceService.get_content_page_by_id`, strips HTML.
4. Pipes into `chat_completion_stream` with a Gherkin-generation system prompt. Output streams token-by-token.
5. Frontend's UI reveals tokens as they arrive — the user watches the Gherkin form in real time.
6. On stream completion, `stream_options.include_usage` returns total tokens, which is recorded against the user (`source="test_scenarios_stream"`).
7. The full text + parsed `.feature` boundaries persist to S3.

**Backend flow — push to GitHub.**
1. User reviews the generated Gherkin in an editable preview pane.
2. Enters `{repo_url, branch_name, github_pat}` (PAT is not persisted in v1 — passed per-request).
3. Frontend `POST /api/test-generation/{generation_id}/push-to-github` with the above.
4. Backend uses the PAT to call GitHub REST API:
   - **Detect empty repo**: `GET /repos/{owner}/{repo}` — if `size == 0` and there are no branches, this is an empty repo.
   - **Empty repo, target = default branch**: write the first file directly via `PUT /repos/{owner}/{repo}/contents/Include/features/{filename}` — GitHub auto-creates the initial commit on the default branch (`main` typically).
   - **Empty repo, target ≠ default branch**: first write a `README.md` to the default branch to bootstrap it, then create the target branch from `main`, then push features.
   - **Non-empty repo**: find or create the target branch from `main` / `master`, then `PUT /contents/...` per file (each becomes a separate commit).
5. Optionally opens a PR if the user checked the "Open PR" box.
6. Activity event `test_scenarios_pushed_to_github` recorded.

**Notable details.**
- **File location must be `Include/features/`**, not `tests/features/`. Katalon Studio's BDD plugin and Katalon AI Assistant's "Attach files" picker both restrict to the `Profiles/`, `Keywords/`, and `Include/` folders at the project root. Files outside those locations are invisible to Katalon AI. Discovered after a pilot user's feature files weren't selectable.
- **Empty-repo handling** was a Day-1 production crash: the original code called `GET /repos/{owner}/{repo}/git/ref/heads/main` and got 404 because there was no `main` branch yet. Fix is the multi-branch empty-repo detection above.
- **GitHub PAT permissions**: fine-grained PATs need explicit `Contents: Write` + `Pull requests: Write` permissions on the target repo. Classic PATs need the `repo` scope. The 403 error is sometimes ambiguous; the backend surfaces GitHub's actual error message to help users self-diagnose.
- **`@TS-XXX` tags** on every Scenario for traceability — the LLM is prompted to issue sequential test IDs starting from `TS-001`. Lets QA correlate failures back to the source spec.
- **Streaming was added late.** v0 was a single blocking `chat_completion` call that took 30–60s with no UI feedback; the perceived performance gain from streaming is large even though total time is identical.
- PATs aren't persisted in v1 — users re-enter them on every push. Persisted GitHub PATs are a known gap (would mirror the Atlassian/Lucid pattern).

### 2.8 Pair Programming

**Purpose.** Push the platform's project knowledge into the engineer's IDE so their existing AI coding assistant (Cursor, Claude Code, Copilot CLI, etc.) can ask Velox for product/architecture context before answering coding questions. The engineer doesn't switch tools — they stay in their IDE, but their AI now knows that the team uses Bedrock for LLMs, has SOC2 constraints, and just merged a refactor of the auth flow last week.

**Who.** TECH group exclusively. The page itself is mostly instructional — the real value is in the engineer's IDE.

**Entry points.**
- Pair Programming module landing page — config blocks for each supported IDE
- "Sync Knowledge Base" button — triggers the ingestion pipeline that populates the vector DB
- "Generate API Key" CTA — produces the engineer's personal MCP server credential

**Storage shape.**
```
RDS: pg_embeddings table with pgvector extension
     (chunk_id PK, project_id, source_type, source_id, content TEXT, 
      embedding VECTOR(1024), metadata JSONB, created_at)
     IVF-Flat index on embedding for cosine similarity

RDS: users.mcp_api_key (random 32-byte token, plaintext — already a secret on its own)
```

**Backend flow — sync knowledge base.**
1. User clicks Sync → `POST /api/sync/projects/{project_id}/sync`.
2. Backend resolves project's linked Confluence space + Jira project from `projects` row.
3. **Confluence ingest**: paginate `/wiki/api/v2/spaces/{key}/pages`, fetch each page's `body.storage`, strip HTML, chunk to ~450 tokens with 68-token overlap.
4. **Jira ingest**: paginate `/rest/api/3/search` with JQL `project = {key}`, pull each issue's `summary + description + comments`, chunk similarly.
5. For each chunk, call DLX AI Gateway `/embeddings` with `model="amazon.titan-embed-text-v2:0"` → 1024-dim vector.
6. Bulk-insert into `pg_embeddings` with `{project_id, source_type:"confluence"|"jira", source_id, content, embedding, metadata}`.
7. Activity event `mcp_synced` with chunk count.

**Backend flow — IDE assistant queries via MCP.**
1. Engineer's IDE has the MCP server configured with `{API_URL, API_KEY, PROJECT_ID}` in `mcp.json`.
2. Engineer asks their AI assistant a coding question. The assistant (per its instructions) first calls the `enhance-prompt` MCP tool.
3. MCP server (`sdlc_mcp` package, separate Bitbucket repo, distributed as a Python CLI) calls `POST /api/orchestration/enhance-prompt-internal` with `{prompt, project_id, X-API-Key}`.
4. Backend validates API key against `users.mcp_api_key`, embeds the prompt via Titan, runs cosine similarity against `pg_embeddings` filtered by `project_id`, returns top-K chunks with their source metadata.
5. MCP server formats the chunks as an "enriched prompt" and returns to the IDE assistant.
6. Assistant continues its answer with that context in scope.

**Backend flow — pipeline RAG (similar tool).**
- `POST /api/orchestration/pipeline-rag-internal` — same shape but specifically for deployment/pipeline questions. Wider retrieval window, includes Harness pipeline configs when available.

**Notable details.**
- **No production code embeddings yet.** The vector DB only contains Confluence + Jira content; the user's repo isn't ingested. Code Intelligence (§2.9) is the related but separate flow that goes the other direction (code → BRD).
- **Chunk size 450 / overlap 68** chosen empirically against Titan v2's 1024-dim space — bigger chunks hurt retrieval precision, smaller hurt context completeness.
- **The MCP server is a separate codebase** (`sdlc-mcp/sdlc-mcp` in the workspace). Engineers install it via `pip install -e git+ssh://...` from the internal Bitbucket. The instruction page on Pair Programming has the exact paste-able `mcp.json` snippet for each supported IDE.
- **API keys are per-user, plaintext.** Each engineer generates one on the Pair Programming page and pastes it into their `mcp.json`. We don't rotate them automatically; rotation is a "click Regenerate, paste new value" flow.
- **The `enhance-prompt` tool returns shaped Markdown**, not raw chunks — so the IDE assistant sees a clean "Relevant context from project X" preamble it can incorporate naturally.
- Sync is **manual today**, not on-change. A scheduled sync (e.g. nightly) is a known gap; for now engineers re-sync when they know docs have changed.

### 2.9 Code Intelligence — BRD Sync + PR Sync

**Purpose.** Catch spec-vs-code drift before it becomes a production surprise. The BRD says what the product is supposed to do; the code says what it actually does. They diverge constantly — features get added in PRs without BRD updates, fields get renamed, OAuth replaces password login but the BRD still references both. Code Intelligence runs an LLM-driven comparison and surfaces a structured drift report so the team can decide which side is wrong.

**Who.** TECH users (engineers running the CLI, reviewers consuming the report). BUSINESS users can see the resulting drift summary.

**Entry points.**
- Code Intelligence module landing page — list of past sync reports per project
- CLI: `velox-cli code-sync --project <id> --repo <path>` — runs locally on the engineer's machine, produces summaries, uploads to Velox
- "Generate Drift Report" CTA on a completed code-summary upload
- PR Sync — separate flow that runs against a specific PR diff rather than the full codebase

**Storage shape.**
```
s3://sdlc-s3-app-data/code-summaries/{project_id}/{summary_id}/
├── module-summaries.json   # per-module structured summary (the CLI's output)
├── data-model.json         # extracted schema / type definitions
└── drift-report.json       # LLM-generated comparison vs BRD

RDS: code_sync_runs(run_id PK, project_id, user_id, source_commit_sha, 
                    summary_id, drift_report_id, created_at)
```

**Backend flow — BRD Sync (full-codebase drift check).**
1. Engineer runs `velox-cli code-sync --project X --repo .` locally.
2. CLI traverses the repo, identifies modules (heuristic + manifest detection), produces a per-module summary using a local lightweight pass + an LLM summarization call.
3. CLI uploads the structured `{modules: [...], data_model: {...}}` JSON to `/api/code-intel/upload-summary`.
4. Backend stores in S3, returns `summary_id`.
5. User in the Velox UI clicks "Generate Drift Report" → `POST /api/code-intel/drift-report` with `{summary_id, brd_id}`.
6. Backend loads BRD JSON + code summary, runs an LLM comparison prompt per BRD section:
   - "Section 3 says: '...'. Code summary says: '...'. List concrete drifts."
   - Output: structured list of `{section, kind: "missing_in_code"|"missing_in_brd"|"mismatch", description, evidence}`.
7. Drift report written to S3 + linked on `code_sync_runs`.
8. Frontend renders the report with grouping by section + severity badges.

**Backend flow — PR Sync (per-PR drift check).**
1. Engineer triggers (CLI or webhook) `POST /api/code-intel/pr-sync` with `{repo, pr_number, github_pat}`.
2. Backend fetches the PR diff via GitHub API.
3. Runs an LLM call to summarize what the PR changes (new features, modified APIs, removed code).
4. Compares against BRD sections most likely affected (LLM-driven section selection from the diff summary).
5. Returns a "what should change in the BRD" report — proposed BRD edits the engineer can review and apply.

**Notable details.**
- **The CLI is the boundary.** Source code never leaves the engineer's machine — only structured summaries do. This matters for compliance (some teams' code is under additional access controls).
- **LLM does the heavy lifting** of comparison. The platform doesn't try to AST-diff or schema-match — it relies on Claude to spot the semantic gaps. This is the right tradeoff for v1 because the BRD is in natural language, not formal spec.
- **Drift reports are advisory, not gating.** The platform doesn't block PRs or fail CI on drift; it just surfaces it. Teams adopt their own policy (e.g. "before any quarterly release, run a drift report and resolve").
- **The CLI is a separate codebase** under active development. It's distributed as an internal pip package similar to the MCP server.
- **Code Intelligence is the newest module** (post-MVP). Adoption is still pilot-stage.

### 2.10 Figma

**Purpose.** Bridge from "we have a Jira story for a new feature" to "we have a Figma frame for it." The platform doesn't try to render Figma directly — instead it produces a high-quality structured prompt that Figma's own AI ("Figma Make" / "Figma AI") consumes to scaffold screens. The engineer/PM owns the design iteration in Figma; we just give them a great starting point.

**Who.** PMs, designers, frontend engineers. BUSINESS + TECH both have access.

**Entry points.**
- Figma module landing page — link to Figma PAT in profile + list of past prompt generations
- "Generate Figma Prompt" CTA on any Jira story or BRD section
- "Open in Figma Make" deep-link button on a generated prompt

**Storage shape.**
```
RDS: users.figma_pat, users.figma_team_id, users.figma_linked_at
     (plaintext in v1 — Figma PATs were the first integration added, before KMS was wired up;
      tracked as a migration gap)

RDS: figma_prompts(prompt_id PK, project_id, user_id, source_type, source_id,
                   prompt_text, created_at)
```

**Backend flow — generate prompt.**
1. User picks a source: Jira story (by issue key) or BRD section (by section number).
2. Frontend `POST /api/figma/generate-prompt` with `{source_type, source_id}`.
3. Backend fetches source content (Jira: `/rest/api/3/issue/{key}`; BRD: from S3 JSON).
4. Runs an LLM call with a Figma-Make-optimized prompt template:
   - Extracts user-facing surface (screens, components, states) from the source
   - Produces a structured prompt with: screen list, component hierarchy, content placeholders, interaction notes, accessibility hints, brand reference (Deluxe / Velox colors)
5. Stores in `figma_prompts`, returns to UI.
6. User reviews / edits the prompt, then clicks "Copy" or "Open in Figma Make" (deep-links to figma.com/make with the prompt URL-encoded where possible).

**Notable details.**
- **We don't render in Figma directly.** Figma's REST API supports reading but not creating frames programmatically (Figma Make is the only path); so the integration is "give the user the best possible prompt and let Figma's own AI do the construction."
- **PAT storage is plaintext (legacy).** Figma was the first integration shipped before KMS was wired up. A migration to `_encrypt_token` is a known gap — same path as the recent Atlassian/Lucid implementations.
- **Team ID is required** because Figma's PAT can access multiple teams, but most users only have one relevant team — we let them pick once and remember.
- **The prompt template encodes Deluxe brand tokens** (color palette HSL values, typography scale, spacing) so generated frames match the in-house design system out of the box. Maintained by the design team.
- **Adoption is opportunistic** — PMs use it when starting fresh; mature features go through the design team's normal flow.

### 2.11 Deployment (Harness)

**Purpose.** Give the engineer a single pane that shows "where is my code in the pipeline right now" without having to log into Harness. Read-only in v1 — we surface pipeline state, we don't trigger deploys. The intent is visibility, not control.

**Who.** TECH group. Deploy decisions stay in Harness; we only show status.

**Entry points.**
- Deployment module landing page — list of pipelines configured for the project
- Per-pipeline detail view — recent executions, current stage, who triggered, duration
- Drill-down link to the actual Harness URL for an execution (where the user can take action)

**Storage shape.**
```
RDS: projects.harness_org, projects.harness_project, projects.harness_pipelines JSONB
     (Harness account credentials configured per-environment via env var, not per-user)
```

**Backend flow — list executions.**
1. `GET /api/deployment/pipelines/{project_id}` → Backend calls Harness REST API with the service-account token.
2. Returns shaped pipeline list with current state + most-recent execution per pipeline.
3. Drill-down `GET /api/deployment/pipelines/{project_id}/{pipeline_id}/executions` → recent N executions with stages, durations, triggering user.

**Notable details.**
- **Service-account auth, not per-user.** Harness API auth uses a platform-wide service account configured via env var. Per-user Harness PATs were considered but rejected — engineers don't all have Harness API tokens, and the visibility use-case doesn't require user-attribution.
- **Cached aggressively** (60s TTL on pipeline lists, 15s on the active-execution view) to keep load off Harness's API.
- **v1 is read-only by design.** Triggering deploys from Velox would change the trust model significantly (we'd become a deploy origin in CI). Deferred to a later phase if there's demand.
- **Pipeline mapping is manual.** Each Velox project maps to a Harness org+project+pipeline-set, configured at project setup time. Auto-discovery of pipelines based on the project's git repo is a known gap.

---

## 3. Platform architecture

### 3.1 Layered view

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Browser (React 18 + Vite, served at /sdlc/ in nonprod, / in dev)         │
│   - Azure AD MSAL for SSO                                                 │
│   - tanstack/react-query for server state                                 │
│   - shadcn/ui + Tailwind, two themes via data-theme attribute             │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ HTTPS (ALB)
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ FastAPI backend on ECS Fargate (Python 3.12)                              │
│   - Verifies Azure AD JWT on every request                                │
│   - Computes per-user `allowed_modules` from Azure AD group membership    │
│   - Persists users, projects, sessions, integrations, activity to RDS     │
│   - Orchestrates: invokes AgentCore Runtime + AWS Lambda + DLX AI Gateway │
│   - Streams SSE for long-running LLM operations                           │
│   - Exposes /api/internal/record-tokens for Lambda → backend token reports│
└──┬──────────────────┬───────────────────┬──────────────────────┬─────────┘
   │                  │                   │                      │
   ▼                  ▼                   ▼                      ▼
┌──────┐   ┌────────────────────┐   ┌────────────┐    ┌─────────────────┐
│ RDS  │   │ AWS Bedrock        │   │ AWS Lambda │    │ DLX AI Gateway  │
│ pg   │   │ AgentCore Runtime  │   │ workers ×5 │    │ (OpenAI-compat) │
│      │   │  · PM Agent        │   │            │    │   ↓             │
│ Vec  │   │  · Analyst Agent   │   │ Each ships │    │ Claude 4.5      │
│ tor  │   │                    │   │ with the   │    │ Sonnet via      │
│      │   │ AgentCore Memory   │   │ same       │    │ Bedrock         │
│      │   │ (chat persistence) │   │ llm_gateway│    │                 │
└──────┘   └────────────────────┘   └────────────┘    └─────────────────┘
   │                  │                   │                      │
   └──────────────────┴────────────┬──────┴──────────────────────┘
                                   │
                                   ▼
                             ┌──────────────┐
                             │ S3 (KMS-enc) │
                             │ sdlc-s3-app- │
                             │ data         │
                             └──────────────┘
```

### 3.2 Identity, authorization, and tenancy

Single Azure AD tenant. Every request the browser makes carries a JWT bearer token; the FastAPI dependency `get_current_user` validates it (via the AAD JWKS, cached per worker), extracts the user's group membership claim, and computes the modules the user is allowed to see. For users with very many groups, Azure AD truncates the `groups` claim and emits a `_claim_names` overage marker — the platform detects this and falls back to Microsoft Graph's `checkMemberGroups` to resolve.

Two Azure AD groups drive RBAC: BUSINESS and TECH. The `GROUP_MODULE_MAP` in the backend says which modules each group can see (BUSINESS → BRD / Confluence / Jira; TECH → Confluence / Jira / Pair Programming; users in both see the union). The frontend doesn't trust itself — every protected route makes a backend `/api/user/info` call after login that returns the authoritative `allowed_modules` list, and the sidebar filters from that. The backend separately enforces the same RBAC via a `require_module(name)` FastAPI dependency on every router prefix.

There's a finer-grained `access_role` column on the `users` table that's BOTH / TECH / BUSINESS / NONE, derived on every login from the same group claim; this powers the Organization Usage page and is shown on My Profile as a chip.

### 3.3 LLM orchestration patterns

Every LLM call in the system goes through a single client (`llm_gateway.py`) that points at the DLX AI Gateway — an internal OpenAI-compatible proxy that fronts AWS Bedrock with budget controls and observability. Two function shapes:

- `chat_completion(...)` — blocking, returns the full string when the model is done. Used for one-shot generation: BRD chat replies, section edits, intent routing, audit checks, test scenario parsing.
- `chat_completion_stream(...)` — real SSE streaming via the OpenAI SDK with `stream=True` and `stream_options={"include_usage": true}`. Used for long-form generation where the user wants to see text appear: BRD initial draft, SAD section workers, "Generate Test Scenarios" from Confluence. The token-by-token reveal in the UI is identical to chatting with the underlying model directly.

Two LLM-execution surfaces:

- **AWS Bedrock AgentCore Runtime** for the two persistent agents (PM Agent, Analyst Agent). They're written with the Strands Agents SDK, packaged as Docker containers, deployed via `agentcore deploy --local-build`, and invoked via `bedrock-agentcore.invoke_agent_runtime`. They have persistent conversation memory (AgentCore Memory), tool registries (AgentCore Gateway), and Strands' built-in reasoning loop for multi-step plans. They're used when we want autonomous orchestration — the PM agent decides on its own whether a user message is an `update_section` vs `show_section` vs `regenerate` vs `general_question`, and invokes the right Lambda tool with the right payload.
- **AWS Lambda** for stateless per-feature workers (`brd-chat`, `brd-generator`, `brd-from-history`, `requirements-gathering`, `sad-orchestrator`). Each Lambda is a thin wrapper around its prompt logic that calls `chat_completion(...)` and writes results to S3 / AgentCore Memory. They're invoked synchronously from the backend or from inside an AgentCore agent as a tool.

The agents and Lambdas both record token usage. Lambdas can't reach RDS directly, so `llm_gateway.py` falls back to an HTTP callback: POST `/api/internal/record-tokens` with `{user_id, tokens, source, X-API-Key}`. The backend validates the API key (from a JSON `INTERNAL_API_KEYS` env var) and increments `users.token_usage`. The ECS backend skips the HTTP hop and writes directly. Every increment is logged with the originating `source` label (`lambda_brd_chat`, `pm_agent_general`, `lambda_requirements_gathering`, `test_scenarios_stream`, etc.) so we can later attribute spend to features.

### 3.4 Persistence

Three persistence surfaces, each chosen for what it's good at:

- **Postgres (RDS)** — the system of record for users, projects, design sessions, BRD metadata, integration credentials (KMS-encrypted at rest for sensitive ones), per-module activity events, lifetime token totals, vector embeddings for the Pair Programming knowledge base (via `pgvector`).
- **S3** (`sdlc-s3-app-data`, KMS-encrypted) — content artifacts. BRDs (JSON + text mirror), SAD structures + facts buffers, architecture diagram XML / SVG / PNG, uploaded transcripts, BRD templates, Gherkin feature files, support docs. Keyed by either `brds/{brd_id}/...`, `sessions/{session_id}/...`, or `templates/...`. Reads stream straight back; writes go through a single `s3_put_object` helper that always sets the KMS SSE header.
- **AWS Bedrock AgentCore Memory** — conversation history for both the PM Agent (one session per BRD: `brd-session-{brd_id}`) and the Analyst Agent (one session per analyst conversation, UUID-based). The memory IDs are environment-specific (`sdlc_dev_agentcore_memory-...` vs `sdlc_nonprod_agentcore_memory_...`); actor IDs are `brd-session` and `analyst-session` respectively. Memory survives Lambda cold starts and Pod restarts; we never read it on the hot path of a single request, only when restoring a session or piping history into a section regeneration.

### 3.5 Third-party credentials

Four external systems require per-user authentication, all stored in the `users` table:

| System | Auth method | Storage | Encryption |
|---|---|---|---|
| Atlassian (Jira + Confluence) | Personal API token | `users.atlassian_{domain,email,api_token,linked_at}` | KMS via `_encrypt_token` |
| GitHub | Personal access token | passed per-request, not persisted yet | — |
| Lucid (new) | Personal REST API key | `users.lucid_{api_key,linked_at}` | KMS via `_encrypt_token` |
| Figma | PAT + team ID | `users.figma_{pat,team_id,linked_at}` | plaintext (legacy) |

Each integration follows the same UX shell: a Link / Update modal in the user's profile that takes the credential, the backend validates it by calling a cheap "who am I" endpoint on that vendor's API (Atlassian: `JiraService.test_connection`; Lucid: `POST /documents/search?pageSize=1`), and on success encrypts + writes the column + clears a per-user validation cache.

---

## 4. End-to-end workflows

A few canonical flows, in order of typical SDLC progression:

### 4.1 New project setup

User goes to the Project Workspace dropdown → "Create new project" → fills in project name, picks a Jira project, picks a Confluence space, picks a BRD template. The backend creates a `projects` row, persists the Jira / Confluence mapping, sets up the AgentCore Memory session ID for this project's BRD chat thread. No LLM calls happen here — it's pure metadata.

### 4.2 BRD generation from transcript

User uploads a transcript file. Frontend uploads to S3 at `transcripts/{session_id}/{filename}` via `/api/upload-transcript` (multipart, KMS-encrypted). Frontend then calls `/api/generate-from-s3` with the transcript S3 key + template S3 key + the user_id. Backend reads both files (pypdf for PDF / python-docx for DOCX / text for TXT), builds a structured payload, invokes the PM Agent via `bedrock-agentcore.invoke_agent_runtime`. The agent recognizes "template + transcript provided" as the `generate_brd` shortcut (not the full reasoning loop), invokes the `brd-generator` Lambda with the user_id baked in. The Lambda runs one big LLM call (typically 60-120 seconds for a 50KB transcript producing a 16K-token BRD), splits the structured markdown into 16 sections, writes `brds/{brd_id}/brd_structure.json` + a flat text mirror to S3, and returns the brd_id back through the agent to the frontend. The agent also reports its own routing-call tokens; the Lambda reports the big-generation tokens. Total: ~25K tokens per BRD, ~5 separate `users.token_usage` increments per generation.

### 4.3 BRD section editing

User types "expand the business triggers in section 3" into the BRD chat box. Frontend POSTs to `/api/chat` with the brd_id, session_id (`brd-session-{brd_id}`), message, and user_id. Backend invokes the PM Agent. The agent stashes user_id, looks at the keywords (`update`, `expand`, `modify`, ...) and takes the DIRECT PATH (skip the Strands LLM, send the user message straight to the Lambda) for command-style messages. The `brd-chat` Lambda loads the BRD JSON from S3, runs an LLM call to parse intent + extract section number + the actual instruction, runs another LLM call to produce the updated section JSON (passing the full current section + the user instruction + the BRD schema), validates the response, writes the patched JSON + text mirror to S3, persists the user + assistant turns to AgentCore Memory, and returns the updated section text. The UI rerenders that section in place.

### 4.4 Architecture session → SAD

User opens the Architecture module, picks an existing session or creates one. Inside, they pick draw.io or Lucid for this session. They go through diagram type by diagram type (logical, then optionally infrastructure, then security) — each one opens its own editor scoped to that slot. After saving, the hub shows the slot as "done" with timestamp + tool used.

When they're ready, they click "Generate SAD". The frontend calls `/api/sad/generate`. The backend reads the `diagram_slots` JSONB + the linked BRD ID + the session's AgentCore Memory ID and invokes the `sad-orchestrator` Lambda with all of that as an event payload. The Lambda spawns 10 parallel section workers via a thread pool, one per section. Each worker:

1. Pulls section-specific context from the payload (BRD content, diagram_slots, facts buffer, conversation history)
2. Runs a section-specific drafting prompt
3. For sections 4, 6, 7: checks if the slot for the matching diagram type is "done" with a non-empty `artifact_key`. If yes, prepends a `{type: "diagram", s3_key: …}` block to the section content. If no, writes an explicit placeholder paragraph
4. Returns its structured JSON

The orchestrator assembles all 10 into a `sad_structure.json`, writes to S3, kicks off the auto-audit (10 more parallel prompts), and writes `audit_latest.json`. The frontend SAD viewer polls (or streams) for each `section_complete` event and paints the section list with the audit badges.

### 4.5 Test scenarios → GitHub → Katalon

User picks a Confluence page that documents test scenarios. Clicks "Generate Test Scenarios". The backend fetches the page content via `ConfluenceService.get_page_content` (using the user's stored Atlassian PAT), strips HTML to plain text, and pipes it into an LLM call with `chat_completion_stream` — Gherkin `.feature` text streams into the UI as it's generated. The user reviews, optionally edits inline, then enters a GitHub repo URL + a PAT + a branch name and clicks "Push to GitHub". The backend uses the PAT to:

1. Detect if the repo is empty (`/repos/{owner}/{repo}` has `size: 0`)
2. If empty, write the first `.feature` file directly to the default branch via `PUT /contents/{path}` — GitHub auto-creates the initial commit
3. If non-empty, find or create the target branch from `main` / `master`
4. Write each `.feature` file as a separate commit
5. Open a PR if the user requested one

Files land at `Include/features/{name}.feature` so Katalon Studio's BDD plugin sees them. The user opens the project in Katalon Studio, refreshes, attaches the feature files to Katalon AI Assistant, and asks "Generate Katalon test cases from these Gherkin files." Katalon AI does the rest.

### 4.6 Pair Programming setup

User goes to the Pair Programming module. The page renders a step-by-step config block for their IDE — copies a JSON snippet they paste into their `mcp.json`. The snippet includes the user's `API_URL` (their environment's backend URL), `API_KEY` (from the backend's `INTERNAL_API_KEYS` dict), and `PROJECT_ID`. The MCP server in their IDE — built separately, distributed via Bitbucket as `sdlc_mcp` — calls back to two backend endpoints: `/api/orchestration/enhance-prompt-internal` (RAG over the project's Confluence + Jira embeddings to produce an enriched prompt), and `/api/orchestration/pipeline-rag-internal` (similar but pipeline-focused). Their IDE assistant then has on-demand access to the project's product context.

---

## 5. Cross-cutting capabilities

These don't belong to any one module — they're properties of the platform that every flow benefits from.

### 5.1 Token usage tracking

A single counter per user (`users.token_usage`, BIGINT) that's incremented atomically on every LLM call. The increment path is:

- ECS backend: `chat_completion(...)` calls `_record_tokens_async(user_id, total_tokens, source=...)` which writes directly to RDS via a backgrounded thread.
- Lambda: same call site, but the direct DB write fails (`db_helper` import works but the Lambda has no DB credentials), and the fallback HTTP POST to `/api/internal/record-tokens` fires.
- AgentCore agent: same again — agents have a similar callback (`_record_tokens_via_callback`).

Every call is tagged with a `source` label so we can later partition spend by feature: `lambda_brd_chat`, `lambda_brd_generator`, `lambda_brd_from_history`, `lambda_requirements_gathering`, `lambda_sad_*`, `pm_agent_general`, `pm_agent_chat`, `test_scenarios_stream`, etc. The Organization Usage page (admin-gated by email allowlist) sorts users by total tokens descending and shows their access role + last login.

### 5.2 Activity tracking

A `user_module_activity` table records per-module events (`brd_generated`, `brd_section_edited`, `confluence_page_pushed`, `jira_stories_created`, `test_scenarios_generated_confluence`, `mcp_synced`, etc.). Used for the "most-used modules" analytics on the Org Usage page.

### 5.3 SSE streaming for long-form generation

Real streaming via `stream=True` on the OpenAI SDK against the DLX AI Gateway, with `stream_options={"include_usage": true}` so the final chunk carries the total token count. The route handler is a plain FastAPI `StreamingResponse`. The gateway proxies SSE through unchanged, so the user sees text reveal token-by-token. Used for: SAD section generation, "Generate Test Scenarios" from Confluence. Replaced an earlier "fake streaming" shim that buffered the whole response then yielded it as a single chunk (no perceptual win).

### 5.4 Cache invalidation / BUILD_ID auto-purge

After a deploy, the frontend SPA's bundle hash changes but users with the old `index.html` cached keep loading the previous build's asset hashes. Two fixes operate together:

- **nginx no-cache on `index.html`** — the bundle entry point never caches; only the hashed JS/CSS files do (forever, since their names change with content).
- **Boot-time build ID check** — every build bakes a `__BUILD_ID__` constant into `main.tsx` via Vite's `define`. On boot, before React or MSAL touches anything, the code compares `__BUILD_ID__` to `localStorage["velox-build-id"]`. On mismatch (first load after a deploy), it clears `localStorage` and `sessionStorage`, writes the new ID, and reloads once. This auto-cleans stale MSAL tokens that would otherwise leave users stuck on a 404 login redirect.

### 5.5 Subpath hosting

Dev runs at `https://sdlc-dev.deluxe.com/` (root). Nonprod runs at `https://ai-labs.deluxe.com/sdlc/` (subpath). All static asset paths use `import.meta.env.BASE_URL` (set by Vite via `VITE_BASE_PATH=/sdlc/` at build time) so favicons, logos, and embedded images resolve correctly under either layout. nginx routes `/sdlc/*` to the SPA, `/api/*` and `/sdlc/backend/*` to the FastAPI backend. The Azure AD redirect URI is computed at runtime from `${window.location.origin}${import.meta.env.BASE_URL}` so SSO callbacks land at the right path in both envs.

### 5.6 KMS-encrypted credential storage

All sensitive credentials (Atlassian PAT, Lucid API key) go through one helper pair: `_encrypt_token` writes a `kms:<base64>` prefix value, `_decrypt_token` reads either an encrypted or plaintext value (transparently). When `KMS_KEY_ARN` is set, the helper calls KMS via `boto3.client("kms").encrypt(...)`; when it isn't (local dev), it stores plaintext with a logged warning. The ECS task role needs `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey` permissions on the specific KMS key — and the key's own resource policy must also grant access (this dual-layer requirement caused a deploy outage early on).

### 5.7 Health & resilience

- Per-Pod connection pool to RDS via `psycopg2.pool.ThreadedConnectionPool`; lazy initialization on first request.
- AgentCore agents have a 15-minute idle timeout; cold start is ~3-6 seconds.
- Lambda warm-up endpoint (`/api/analyst-warm`) preheats workers when the user opens a feature.
- Backend `/api/orchestration/health` is the ALB target group health check.
- Each LLM call has a tight timeout (typically 5-10 minutes for sad-orchestrator workers, 60 seconds for chat turns).
- `_record_tokens_async` is fire-and-forget — accounting failures never break user-facing flows.

---

## 6. Environments

| | Dev | Nonprod |
|---|---|---|
| AWS account | 590184044598 | 339713162037 |
| Frontend URL | sdlc-dev.deluxe.com | ai-labs.deluxe.com/sdlc/ |
| Backend URL | sdlc-dev.deluxe.com | ai-labs.deluxe.com/sdlc/backend |
| RDS host | sdlc-orch-dev-us-east-1-pg-rds-db.* | sdlc-orch-nonprod-us-east-1-pg-rds-db.* |
| S3 bucket | sdlc-orch-dev-us-east-1-app-data | sdlc-s3-app-data |
| KMS key | (none — plaintext OK in dev) | mrk-56ea35df871d40a7a5b31da5f08aff36 |
| PM agent runtime | pm_agent-uDlkiNFagv | sdlc_nonprod_pm_agent-OyZe1q8pcv |
| Analyst agent runtime | analyst_agent-JAa3wMFKOK | sdlc_nonprod_analyst_agent-gNwouH6BUn |
| AgentCore Memory | sdlc_dev_agentcore_memory-VF74Yf64ZB | sdlc_nonprod_agentcore_memory_78a2a-ii88023RyD |
| Lambda prefix | sdlc-dev-* | sdlc-nonprod-* |
| Git branch | features/sdlc | features/sdlc-nonprod |
| Azure AD groups | Business `be88c38e-...` / Tech `670e52fc-...` | Business `b7d12cfc-...` / Tech `68d9dbc2-...` |
| Internal API key | dev-key-aman | nonprod-key |
| Allowed modules | all 11 | brd / confluence / jira / pair-programming only (the rest are RBAC-restricted) |

Source code is in two Bitbucket repos: `sdlc_python_fastapi_backend` (backend) and `sdlc_nextjs_frontend` (frontend, despite the name it's actually Vite + React not Next). Branches: `features/sdlc` for dev, `features/sdlc-nonprod` for nonprod. CI/CD via Harness pipelines triggered by branch pushes.

---

## 7. Tech stack summary

**Frontend**
- React 18 with Vite + TypeScript
- Tailwind CSS + shadcn/ui components
- React Router (with subpath base support)
- TanStack Query for server state
- @azure/msal-browser for Azure AD SSO
- Two themes (`data-theme="deluxe"` / `data-theme="siriusai"`) via HSL CSS variables
- Container: nginx serving the Vite build, proxying `/api/*` to backend

**Backend**
- FastAPI on Python 3.12 (Lambda runtime is also 3.12; cp312 wheels matter for native deps like pydantic-core)
- psycopg2-binary for Postgres
- boto3 for everything AWS (Lambda, Bedrock, KMS, S3, AgentCore Memory)
- python-docx, pypdf, cairosvg for document handling
- OpenAI Python SDK pointed at the DLX AI Gateway for LLM calls (chat completions, streaming, function calling)
- httpx for outbound HTTP (Confluence, Jira, GitHub, Lucid, Figma)
- JWT verification via PyJWT against the Azure AD JWKS
- Container: uvicorn on ECS Fargate

**AI / ML**
- AWS Bedrock — Claude Sonnet 4.5 (`global.anthropic.claude-sonnet-4-5-20250929-v1:0`) for all chat; Titan Embed v2 (1024-dim) for embeddings
- AWS Bedrock AgentCore Runtime — for the PM and Analyst agents
- AWS Bedrock AgentCore Memory — for conversation persistence
- AWS Bedrock AgentCore Gateway — for agent tool registration
- Strands Agents SDK — agent framework, Python
- DLX AI Gateway — internal OpenAI-compatible proxy in front of Bedrock with usage caps + observability

**Infrastructure**
- AWS ECS Fargate for backend
- AWS Lambda for stateless workers
- AWS RDS PostgreSQL (with pgvector extension)
- AWS S3 (KMS-encrypted)
- AWS KMS (customer-managed key, per-environment)
- AWS ALB (path-based routing for /api, /sdlc/backend, /sdlc/)
- AWS ECR for Docker images
- Harness CI/CD for builds + deploys
- Bitbucket for source

**Integrations**
- Atlassian Cloud (Jira + Confluence REST APIs, OAuth-style PATs)
- GitHub REST API (PATs, fine-grained or classic)
- Lucid REST API (`api.lucid.co`, personal API keys with region suffix)
- Figma REST API
- Microsoft Graph (for Azure AD group overage)
- Katalon (offline — engineers use Katalon Studio + Katalon AI; we just push the Gherkin files)

**Local-dev conveniences**
- KMS fallback to plaintext when `KMS_KEY_ARN` unset
- Pydantic-validated Pydantic models for every request
- Type-checked frontend (`npx tsc --noEmit` in CI)
- Per-Lambda dev zip build script (`deploy_lambdas.py --build-only`) with platform-correct manylinux wheels

---

## 8. What's deliberately not built (yet)

Some choices we made to keep the platform shippable. These are tracked but explicitly out of scope for v1:

- **Concurrent edits with conflict resolution** on the same BRD or SAD session from two users / two browser tabs. Today the last write wins.
- **Inline diagram editing of a Lucid-imported SVG**. Once imported the diagram is read-only inside Velox; edits happen back in lucid.app.
- **Per-page Confluence push for SAD sections** (we push full BRDs / full SADs as DOCX; not section-level).
- **Vision-based diagram comprehension** for the SAD generator. Today the diagram XML is passed as text to Claude; vision would improve accuracy on complex diagrams.
- **Cross-session pattern reuse** (recognizing "we already did this kind of architecture in another session"); single-session RAG only.
- **Webhook-based Lucid sync** — once the user has imported a Lucid SVG, the platform doesn't notice if the source diagram changes upstream. Manual re-import.
- **Server-side Jira sync** for the Jira-stories-pushed activity events (today these are recorded but not surfaced back as "your stories changed").
- **A "branch / fork session" action** ("clone Auth flow v3 into Auth flow v4 and tweak").

---

## 9. How we work

- **Branching**: `features/sdlc` is the dev branch; `features/sdlc-nonprod` is the nonprod branch. Both repos use the same convention.
- **Deploy**: Harness CI builds Docker images on every push, Harness CD rolls ECS task definitions. Lambdas deploy via a Python script that bundles handlers + shared utilities + a manylinux-wheel pip install, zips, and pushes to S3 → `update-function-code`. AgentCore agents deploy via `agentcore deploy --agent X --local-build` (the CodeBuild path doesn't work with our SSO PowerUser role; the local-build path always works).
- **Database migrations**: ad-hoc Python scripts under `migrations/` that the dev/SRE runs against the target RDS. No Alembic in v1; we add columns idempotently with `ADD COLUMN IF NOT EXISTS`.
- **Token rotation**: any time a credential lands in a chat transcript or a paste buffer, it gets rotated. We have a documented runbook for both Atlassian PAT rotation and Lucid API key rotation.

---

That's the platform end to end — what it does, why it exists, how it's wired together, what's stable, and what's the next set of choices to make. Anyone reading this should be able to pick up an outstanding bug or feature and have enough context to make a non-trivial decision about where it belongs.
