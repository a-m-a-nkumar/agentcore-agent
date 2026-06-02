# Engineering Notes — Findings, Learnings, Resolutions

Running log of non-obvious issues, root causes, and how we resolved them. Newest entries on top within each section. Keep each entry to 2–3 lines max — link to commits or code paths for detail.

---

## Identity, Auth & RBAC

### Azure AD groups >200 — overage trap (resolved by DevOps)
AAD truncates the `groups` JWT claim at 200 entries; replaces it with `_claim_names.groups` overage marker. **Fix**: DevOps configured the app's groups claim to **"Groups assigned to the application"** (option 4 in MS docs) — JWT now contains at most the SDLC OIDs (TECH + BUSINESS), so the 200 limit never triggers. Defense-in-depth Graph fallback retained in [auth.py](auth.py) with 5-min per-worker cache.

### Org Usage dashboard showed real TECH/BUSINESS users as "NONE"
`update_user_access_role` was a plain UPDATE; on a brand-new user's first request the row didn't exist yet (created later by `create_or_update_user`), so the write affected 0 rows silently. Per-worker cache then locked in the wrong state. **Fix**: UPSERT (`INSERT ... ON CONFLICT DO UPDATE`) + bool return so cache only updates on confirmed write. Self-healing on next login after deploy.

### Fail-open RBAC in `require_module`
Old guard `if allowed and module_name not in allowed` was False when `allowed == []` — let users with no SDLC groups through. **Fix**: fail-closed (empty `allowed` → 403). Graph errors raise `GraphResolutionError` → caller emits **503** (retryable, distinct from permanent 403). Frontend `authApi.ts` retries 3× with backoff before surfacing the new `ServiceUnavailable` page.

### AccessDenied page vs Microsoft AADSTS50105 — Azure team's position is correct
Azure recommends denying at the identity layer (no token issued) over a Velox-branded page: stronger defense in depth, no info leak, no curl bypass risk, single audit log surface. **Compromise**: keep `Assignment required = Yes`, customize Microsoft Entra **Company Branding** to add the Velox User Guide link to the sign-in page. Our `AccessDenied` page stays as a defense-in-depth fallback for the edge case of "assigned to app, no SDLC groups".

### Backend RBAC coverage gap — 13 routers ungated
Only 3 routers use `require_module` (design, jira, testing). 13 others rely on `get_current_user` alone (authentication only). Authenticated insider threat — UI hides modules but direct API calls go through. **Fix queued** (one line per router): `dependencies=[Depends(require_module("X"))]` on the `APIRouter()`.

---

## Lucid Integration

### Lucid REST API can't export images via API key — fundamental limitation
- `/contents?format=svg` → returns JSON document structure, not SVG bytes
- `/contents/image/{pageId}` → 404 (endpoint doesn't exist on api.lucid.co)
- `/embeds/token` → 403; the "Embed" grant doesn't exist in Lucid's API token UI

**Conclusion**: image export and embeds require OAuth, not API keys. Auto-import path scaffolded but non-functional. OAuth-based plan documented (4 phases including Playwright DOCX screenshots) — deferred until prioritized.

### Lucid API quirks
- `POST /documents/search` requires `product` as an **array** (`["lucidchart"]`), not a string — 400 otherwise.
- No `/users/me` endpoint — `/documents/search` with `pageSize=1` is the cheapest validity check.

### Browser `<img src>` can't carry Bearer auth → 401 on protected SVG endpoints
**Fix**: fetch bytes with `fetch()` + Authorization header, wrap as Blob, use `URL.createObjectURL(blob)` as `<img src>`. Helper: `fetchLucidPreviewBlobUrl` in [lucidApi.ts](../deluxe-sdlc-frontend/src/services/lucidApi.ts). Same pattern used by SAD viewer.

---

## MCP Package Distribution

### Private Bitbucket repo blocks new users from `pip install`
Engineers without access to `deluxe-development/sdlc_mcp` get auth errors on both global and venv install options. **Fix**: pre-built wheel (`prompt_enhancer_mcp-0.4.0-py3-none-any.whl`, 23 KB) served from `public/downloads/` is now the primary install path on the Pair Programming page. Git-based Options A and B commented out pending Artifactory rollout. Vite's `public/` convention copies the wheel into `dist/` — no Dockerfile change required.

### Long-term: JFrog Artifactory (owned by DevOps)
Once internal PyPI is live, replace the wheel download with `pip install prompt-enhancer-mcp --index-url https://artifactory.deluxe.com/...`.

---

## AWS / KMS

### Expired AWS SSO creds cause silent KMS failures locally
Symptoms: `/lucid/status` and `/atlassian/status` return 500; logs show `ExpiredTokenException` from `kms:Decrypt`. **Fix**: `aws sso login` + restart the backend. We intentionally don't fall back to plaintext or silently swallow — loud failure is by design so misconfig doesn't leak unencrypted secrets.

---

## Azure AD / My Apps

### "App launch failed" from `myapps.microsoft.com`
Cause: Enterprise App's **Homepage URL** / **User access URL** not set; My Apps doesn't know where to deep-link. **Fix**: set Homepage URL in Enterprise App → Properties. Dev: `https://sdlc-dev.deluxe.com/`. Nonprod: `https://ai-labs.deluxe.com/sdlc/`.

### Velox supports My Apps launch out of the box
MSAL's `handleRedirectPromise` already handles being landed-on directly from My Apps. User clicks tile → app loads → silent OIDC roundtrip (existing AAD session) → home. No code change needed beyond the Homepage URL config.

---

## Activity Tracking

### Tokens recorded but `track_event` not called for many endpoints
`llm_gateway.chat_completion` auto-records tokens on every LLM call. `track_event` is **manual** per route handler — only 8 places call it (BRD-generated, story-pushed, MCP-prompt-enhanced, etc.). Result: dashboard Events counter is "artifacts produced", not "interactions". A user can rack up 20K tokens via the Pair Programming chat box (`POST /api/orchestration/query`) with 0 events. **Status**: not in current batch; fix is either gateway-side auto-record (~1 hr) or per-handler track_event additions (~8 endpoints).

---

## Process / Patterns

### Verify upstream API capability before building integration
Pattern across the Lucid integration: built schema + endpoints + UI, then discovered the upstream API doesn't support the use case. Multiple rebuilds. **Rule**: before any new external API integration, do a 30-min curl/Postman probe to confirm the API actually returns the bytes/shape we need. Document supported endpoints + auth model before committing to schema.

### Per-environment AAD app registrations have separate redirect URIs
Dev (client ID in `.env.client`) and nonprod (`b9b2bfa9-...`) have independent redirect URI lists. When adding a new redirect (e.g. OAuth callback for a new integration), update both. Use the same env-var name (e.g. `LUCID_REDIRECT_URI`) so the code stays env-agnostic.

### `Assignment required` ≠ groups overage
These are independent Azure AD knobs. Assignment-required gates who can sign into the app; groups overage controls JWT shape for high-group users. Don't conflate — they need separate decisions.

---

*Keep entries short. Link to specific files / line numbers / commits for the detail. Update this file when something non-obvious resolves.*
