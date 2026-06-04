# Velox / SDLC Orchestrator — Claude Session Handoff

A condensed brief so a fresh Claude Code chat can pick up without 20
follow-up questions. Read this **before** acting; it's faster than
re-deriving context from the repos.

---

## 1. Product

**Velox** (internal name) / **SDLC Orchestrator** — Deluxe's internal
AI-assisted software-delivery platform. Modules: BRD Assistant, Confluence
RAG, Jira, Design (SAD authoring with Draw.io / Lucid), Pair Programming
(MCP servers for IDE), Testing, Code Intelligence.

LLM is **Claude Sonnet 4.5** via the **Deluxe AI Gateway** (`https://dlxai-dev.deluxe.com/proxy`),
not direct Anthropic API. Same code paths in both envs.

---

## 2. Four repos, two environments

| Role | Local path (Windows / OneDrive) | Branch | Remote |
|---|---|---|---|
| Dev backend | `agentcore-agent` | `features/sdlc` | Bitbucket `sdlc_python_fastapi_backend` |
| Nonprod backend | `sdlc-backend-nonprod` | `features/sdlc-nonprod` | same remote, sibling branch |
| Dev frontend | `deluxe-sdlc-frontend` | `features/sdlc` | Bitbucket `sdlc_nextjs_frontend` |
| Nonprod frontend | `sdlc-frontend-nonprod` | `features/sdlc-nonprod` | same remote, sibling branch |
| MCP package | `sdlc-mcp/sdlc-mcp` | various | separate, not auto-deployed |

**They are separate Bitbucket repos**, not branches of one repo. To
mirror a fix dev→nonprod you `cp` files (not `git cherry-pick`). The
nonprod backend has a GitHub-mirror remote `agentcore-agent` for
optional cross-repo fetching.

**Push pattern**: most fixes go to **both** dev and nonprod in parallel
commits. Always do dev first, mirror, re-verify, push nonprod.

---

## 3. Environment-specific values — NEVER cross-contaminate

When mirroring dev → nonprod, PRESERVE these in the destination:

### Backend
| Key | Dev | Nonprod |
|---|---|---|
| AWS account | `590184044598` | `339713162037` |
| RDS host | `sdlc-orch-dev-us-east-1-pg-rds-db.cvmmysogs29x.us-east-1.rds.amazonaws.com` | `sdlc-orch-nonprod-us-east-1-pg-rds-db.chkioqgucnyn.us-east-1.rds.amazonaws.com` |
| S3 bucket | `sdlc-orch-dev-us-east-1-app-data` | `sdlc-orch-nonprod-us-east-1-app-data` |
| KMS key | `mrk-29bf4d8d90604305976882df6c91149e` | (different — read from nonprod `.env`) |
| Lambda names | `sdlc-dev-<name>` | `sdlc-nonprod-<name>` |
| AgentCore ARNs | `runtime/pm_agent-...`, `runtime/analyst_agent-...` in dev account | different ARNs in `339713162037` |

### Frontend
| Key | Dev | Nonprod |
|---|---|---|
| `apiUrl` (in `PairProgrammingDashboard.tsx`) | `https://sdlc-dev.deluxe.com` | `https://ai-labs.deluxe.com/sdlc/backend` |
| `apiKey` | `dev-key-aman` | `nonprod-key` |
| `BUSINESS_GROUP_OID` (in `AuthContext.tsx`) | `be88c38e-8a45-4026-ac85-f0f850b8cc03` | `b7d12cfc-f6a9-4e5b-a508-eea967fffc70` |
| `TECH_GROUP_OID` (in `auth.py`) | different OID | `68d9dbc2-78bc-4d75-b551-1ee55f6bd9b2` |
| Allowed module set | full | nonprod restricts to `brd, confluence, jira, pair-programming` (design/figma/testing/harness gated off) |
| Vite `base` path | `/` | `/sdlc/` (subpath hosted) |

### MCP
- Server name = `enhance-prompt` (from `FastMCP("enhance-prompt", ...)` in `enhance_server.py`)
- Tool name inside that server = `enhance_task` (Python function name, exposed via `@mcp.tool()`)
- Fully-qualified picker reference in IDE: `mcp.enhance-prompt.enhance_task`
- Package name (PyPI): `prompt-enhancer-mcp`
- Three executables: `prompt-enhancer-mcp`, `test-workflow-mcp`, `pipeline-analyzer-mcp`

---

## 4. CI/CD reality

| Layer | How it deploys | Gotchas |
|---|---|---|
| Backend (FastAPI on ECS Fargate) | Harness CI auto-triggers on push to `features/sdlc` and `features/sdlc-nonprod` | Each repo has its own Harness pipeline. Frontend nonprod hosted at `/sdlc/` subpath. |
| Backend Lambdas | **Manual**: `python deploy_lambdas.py --name <name>` from repo root, with AWS env vars or SSO profile `590184044598_PowerUser` (dev) | Lambdas DO NOT auto-deploy from git. Always deploy after code changes to `lambda_*.py`. |
| Frontend (React+Vite via nginx) | Harness CI builds Docker image, deploys to ECS | npm registry is `https://artifacts.deluxe.com/api/npm/deluxe.node/`. Some packages (e.g. `electron-to-chromium-1.5.359`, `ts-jest-29.4.10`) are blocked by jfrog curation — pin versions in `package.json`. `npm install --force --no-package-lock` ignores the lockfile, so `package.json` ranges have to be tight. |
| MCP package | Pre-built wheel in frontend `public/downloads/`, users `pip install <path>` | PEP 427 requires `{name}-{version}-{py}-{abi}-{platform}.whl`. NEVER rename the wheel file to drop the suffix. UI label may say `prompt_enhancer_mcp_1.0.0` but the served file is `prompt_enhancer_mcp-0.4.0-py3-none-any.whl`. |
| DB migrations | Dev: `setup_database.py` runs on container boot via Dockerfile. Nonprod Dockerfile **does NOT copy** `setup_database.py` — apply manually via `python apply_missing_migrations.py` from the repo root | Migration files added to dev's `migrations/` directory don't automatically apply to nonprod. The `apply_missing_migrations.py` shim is committed in nonprod for reapplication. |

---

## 5. AWS credential reality

- User has **SSO PowerUser** in dev account (`590184044598_PowerUser`). Nonprod uses `DevSupport` role in `339713162037`.
- SSO tokens **expire mid-session** (1-2 hours typical). When a Lambda deploy fails with `ExpiredToken`, ask the user to refresh.
- The `.env` file in each repo may contain temporary SSO credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`). When parsing them via shell `xargs`, leading-space lines cause mangling — use explicit `export` instead.
- `deploy_lambdas.py` honors env-variable credentials when `AWS_ACCESS_KEY_ID` is set; otherwise uses `--profile 590184044598_PowerUser`.

---

## 6. Recurring patterns to follow

### When user reports a UI/API bug

1. Always look at the **frontend service call** + **backend route** + **backend service** together. Don't assume one layer.
2. For pickers (Confluence space, Jira project): check pagination, lazy-load, search debounce, and cache. Common failure modes: server-side search not paginated, IntersectionObserver vs broken cmdk `onScroll`, stale-response race.
3. For auth issues: check `get_current_user` in `app.py`, `compute_access_role` and `compute_allowed_modules` in `auth.py`, and the access_role UPSERT in `db_helper.py`. The fix usually involves `email` + `name` being passed correctly to satisfy `users.email NOT NULL`.

### When porting dev → nonprod

1. Diff the relevant files first: `diff -q dev/path nonprod/path`.
2. For each differing file, distinguish **env-specific lines** (preserve) from **stale-version lines** (port).
3. Common env-specific surfaces: `apiUrl`, `apiKey`, AWS account ID, RDS host, Lambda name, group OIDs, allowed-module set, vite `base`, Dockerfile (nonprod Dockerfile is sometimes leaner).
4. Type-check (`npx tsc --noEmit`) before pushing frontend; syntax-check (`python -c 'import ast; ast.parse(...)'`) before pushing backend.
5. Mirror via `cp` for files with no env-specific lines; use targeted `Edit` for files that do.
6. Commit messages should call out **both** what changed AND what env-specific stuff was preserved.

### When user says "push to nonprod"

- **Always check `git status` first.** Nonprod working trees frequently contain in-progress local work. Stage selectively unless instructed otherwise.
- The historical 503 Graph-fallback port was uncommitted for many sessions; it's now committed across all four repos (`c59dc25` backend, `631c03a` frontend). Future similar in-progress ports should be treated the same way: keep them out of unrelated commits.

### When applying a backend caching pattern

The Confluence/Jira services use this pattern (good template):

```python
# Module-level cache, keyed by email, 5-min TTL
_LIST_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_LIST_CACHE_TTL_SECS = 300

class XService:
    def __init__(self, ...):
        self._session = requests.Session()  # connection pooling + warm TLS
        ...

    def get_list(self, ...):
        now = time.time()
        cached = _LIST_CACHE.get(self.email)
        if cached and (now - cached[0]) < _LIST_CACHE_TTL_SECS:
            return cached[1]
        items = self._fetch_all()
        _LIST_CACHE[self.email] = (now, items)
        return items
```

For parallel pagination: **wave_size ≤ 4** against Atlassian (8+ trips
their WAF with `SSL: UNEXPECTED_EOF_WHILE_READING`). Use
`concurrent.futures.ThreadPoolExecutor` + a shared `requests.Session`.

### When user reports "I changed something in DB and it didn't pick up"

- The access_role UPSERT NOW correctly passes email + name from the JWT
  (commit `0ece9a8` dev, `c59dc25` nonprod). Check logs for
  `[access_role] INSERTED/UPDATED/no-op` lines to confirm writes.
- If the user expects an admin edit to persist, look for `COALESCE`
  guards in the UPSERT (admin-edited values must not be stomped by
  auth-flow defaults).

### When user reports build / install errors

- **Pip `Invalid wheel filename`**: usually a `(1)` suffix appended by
  the browser on re-download. There's an inline tip on Pair Programming
  page. Otherwise, advise rename.
- **npm 403 on `artifacts.deluxe.com`**: jfrog catalog block. Pin the
  exact version in `package.json` (`^X.Y.Z` → `~X.Y.Z` or exact via
  `overrides`).
- **AWS SSO ExpiredToken**: `aws sso login --profile 590184044598_PowerUser`.

---

## 7. RAG (Retrieval Augmented Generation) architecture

### Recent improvements landed
- **v2 Confluence API** (`/api/v2/spaces/{id}/pages`) — recovers ~11.6% folder-nested pages v1 missed
- **Batch embedding** via Titan-v2 (up to 25 chunks/call, 15-25× fewer API calls)
- **Concurrent page sync** via `asyncio.gather + Semaphore(8)` — 75min → 5min on 2,601 pages
- **Atomic embedding swap** (delete + bulk insert in one txn) — no orphan rows
- **Recency re-ranking** (`utils/recency.py`) — exponential time-decay multiplier on retrieval score
- **`source_updated_at` column** on `document_embeddings` — backfilled for 20K+ rows

### Known gaps (not yet built)
- No similarity threshold in retrieval — top-K returned even when scores are 0.30 (noise)
- No per-citation "mark stale" / "remove from index" UI
- No corpus curation (label/length filters at sync time)
- No LLM-driven page classification

### Pattern for any RAG improvement
Order of leverage: **retrieval ranking > curation > LLM classification**.
Add recency / similarity threshold / source quality scoring before
indexing-time filtering.

---

## 8. Outstanding pieces of work (as of last session)

| Item | Status | Notes |
|---|---|---|
| Confluence sync pagination + parallelism + caching | ✅ shipped both envs | wave_size=3-4 (was 8 — caused SSL EOF) |
| Jira project list caching | ✅ shipped both envs | mirror pattern |
| MCP wheel v1.0.0 label | ✅ shipped both envs | display-only, file unchanged |
| Wheel `(1)` suffix warning tip | ✅ shipped both envs | inline amber strip |
| Access_role UPSERT email+name fix | ✅ shipped both envs | logs `[access_role] INSERTED/UPDATED/no-op` |
| 503 Graph-fallback | ✅ shipped all four repos | backend raises 503, frontend renders ServiceUnavailable |
| Per-type diagram slots backend | ⚠️ NOT shipped | frontend uses single-slot still; SAD generator reads only latest write |
| Citations in chat responses | ⚠️ partial | backend returns sources, frontend renders in orchestration chat |
| Similarity threshold in retrieval | ⚠️ designed, not built | would filter top-K below e.g. 0.45 |
| Per-citation "mark stale" UI | ⚠️ designed, not built | bottom-up curation |
| Sync-time label/length filters | ⚠️ proposed but not built | tier-1 curation |
| MCP package on jfrog Artifactory | ⚠️ not done | current distribution = wheel file in frontend |

---

## 9. User preferences and rules

These are the user's standing instructions across sessions. **Apply
without re-asking**:

1. **Do NOT push to dev or nonprod without explicit ask.** (Strict — even when an obvious next step.)
   - Past exception: when actively fixing a bug they've been waiting on, they sometimes say "push" once and expect subsequent fixes in the same session to also get pushed. Read the room.
2. **Preserve nonprod-specific config when porting from dev.** Never mirror env-specific values blindly.
3. **Both dev and nonprod usually get parallel commits.** Don't ship to one without the other unless explicitly told.
4. **Be honest about diagnoses.** If a theory turns out wrong, say so plainly and recalibrate. Don't over-assert.
5. **Reuse existing helpers; don't reimplement.** Especially `s3_put_object`, `_encrypt_token/_decrypt_token`, `get_user_atlassian_credentials`, `chat_completion`, AgentCore Memory helpers.
6. **Commit messages explain why, not just what.** Multi-paragraph if needed, including what was preserved and why.
7. **Don't add unnecessary error handling, fallbacks, or validation.** Trust internal code. Only validate at system boundaries.
8. **No emojis in code or commits unless explicitly asked.**
9. **No `--no-verify`, no `--force` push, no `--amend` published commits.** Hooks failing = fix the cause.
10. **Tone: terse, direct, no filler.** State results and decisions, skip narration of intent.

---

## 10. Tools / files / commands worth memorizing

### Backend (`agentcore-agent` / `sdlc-backend-nonprod`)
- `app.py` — FastAPI entrypoint, route definitions, `get_current_user` auth dependency
- `auth.py` — JWT verification, `compute_allowed_modules`, `compute_access_role`, `GraphResolutionError`
- `db_helper.py` — RDS connection pool + most CRUD helpers; `update_user_access_role` is the auth-flow tracker
- `db_helper_vector.py` — pgvector queries (`search_embeddings`, `hybrid_search`, bulk inserts)
- `services/sync_service.py` — Confluence + Jira sync orchestrator (async, semaphore-bounded)
- `services/confluence_service.py` — v2 API client + space-list cache + session
- `services/jira_service.py` — project listing cache + session, issue pagination
- `services/embedding_service.py` — Titan-v2 batch embeddings, chunk splitter
- `services/rag_service.py` — query orchestration, citation surfacing
- `services/search_service.py` — semantic + BM25 + RRF fusion
- `services/lucid_api_service.py` — Lucid REST wrapper (export_document, list_documents)
- `services/s3_service.py` — KMS-encrypted S3 helpers (`s3_put_object`)
- `routers/` — modular FastAPI routers grouped by module
- `prompts/` — LLM prompt templates (one file per concern)
- `lambda_*.py` — Lambda function entrypoints (deployed manually via `deploy_lambdas.py`)
- `migrations/` — DB schema migrations
- `setup_database.py` — runs all migrations in order (dev only on container boot)
- `apply_missing_migrations.py` — manual reapplication shim (nonprod)

### Frontend (`deluxe-sdlc-frontend` / `sdlc-frontend-nonprod`)
- `src/contexts/AuthContext.tsx` — MSAL + backend user info; `permissionsUnavailable` state
- `src/services/authApi.ts` — `GraphUnavailableError`, retry harness for 503
- `src/services/integrationsApi.ts` — Atlassian + Lucid + Figma integration API
- `src/services/orchestrationApi.ts` — main chat / orchestration SSE endpoints; `triggerIncrementalSync`
- `src/services/sadApi.ts`, `lucidApi.ts`, `designSessionApi.ts` — SAD-side APIs
- `src/components/modals/CreateProjectModal.tsx` — project-create with Jira + Confluence picker
- `src/components/dashboard/PairProgrammingDashboard.tsx` — MCP setup page
- `src/components/dashboard/LucidDashboard.tsx` — Lucid-only diagram authoring (Plate 04 import)
- `src/components/design/DiagramPhaseHost.tsx` — diagram state machine (Lucid-only path)
- `src/pages/Dashboard.tsx` — sidebar + module gating
- `src/pages/ServiceUnavailable.tsx` — 503 Graph fallback page

### MCP (`sdlc-mcp/sdlc-mcp`)
- `src/prompt_enhancer_mcp/enhance_server.py` — `FastMCP("enhance-prompt")`, tool `enhance_task`
- `src/prompt_enhancer_mcp/test_server.py` — `FastMCP("test-workflow")`, multiple tools
- `src/prompt_enhancer_mcp/pipeline_analyzer_server.py` — `FastMCP("pipeline-analyzer")`
- `pyproject.toml` — package metadata, entry points
- `dist/` — built wheel + sdist

---

## 11. Verification recipes

| What you changed | How to verify deployed correctly |
|---|---|
| Backend Lambda | `aws lambda get-function-configuration --function-name <name> --query LastModified` should be recent |
| Backend ECS | Harness Pipeline → last run timestamp + image SHA |
| Frontend | Hard refresh (Ctrl+Shift+R) + DevTools Network → find `index-<hash>.js` and grep for a string you added |
| DB migration | `SELECT column_name FROM information_schema.columns WHERE table_name='<table>'` |
| RAG re-sync | CloudWatch → grep `[Confluence] space=<key> sync_complete top_level=N descendants=M total=N+M` |
| Access role write | CloudWatch → grep `[access_role] (INSERTED\|UPDATED\|no-op) user <id>` |

---

## 12. Common-pitfall reminders

- **Don't trust the deployed code based on git push timestamp alone.** Harness may not have triggered or may have failed. Check Harness UI.
- **Don't forget the per-worker `_LAST_ACCESS_ROLE_CACHE`.** A stale value here can mask a fixed DB. Container restart wipes it.
- **Don't forget the Confluence space-list cache.** 5-min TTL means user-visible behavior may lag.
- **Don't `cp` dev's PairProgrammingDashboard.tsx to nonprod.** It clobbers `apiUrl` + `apiKey`. Use targeted `Edit` instead, or `cp` then re-apply the env-specific lines.
- **Don't bundle the 503 Graph-fallback port with unrelated work.** Historically the user kept it separate; both halves now shipped.
- **Don't apply Confluence parallel pagination at wave_size > 4.** Tested: 8 trips Atlassian's WAF with `SSL: UNEXPECTED_EOF_WHILE_READING`. 3-4 is the sweet spot.
- **PEP 427 wheel filenames are sacred.** `pip install` parses them. `name (1).whl` fails. `name_1.0.0.whl` fails. Only `{name}-{version}-{py}-{abi}-{platform}.whl` works.

---

## 13. If the user asks "what's the state of <X>"

Don't guess. Run:

```bash
# branch state
git -C <repo> status -sb

# recent commits  
git -C <repo> log --oneline -5

# unpushed
git -C <repo> log @{u}..HEAD --oneline

# uncommitted
git -C <repo> diff --stat
git -C <repo> status --short
```

Across all four repos in parallel where possible.

---

## 14. The user is

Aman Kumar (T479888) — works on Velox at Deluxe. Strong technical
background, makes architectural calls fast, prefers to be shown
evidence (logs, diffs, file paths) rather than reassured. Comfortable
with detailed answers but appreciates a clear opinionated take on
tradeoffs. Tolerates honest "I don't know, let me check" — does not
tolerate hand-wavy guesses.

