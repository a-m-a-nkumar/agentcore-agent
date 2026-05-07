# Frontend Changes — lucid-frontend

## 1. `.env` — Dev Bypass Variables + Theme Fix

**What changed:**
- Moved inline comment off `VITE_THEME` line (inline comments in `.env` are read as part of the value by Vite, breaking the theme)
- Added three new dev bypass variables

**New variables:**
```
VITE_DEV_BYPASS_AUTH=true
VITE_DEV_USER_EMAIL=prabhat.kumar@siriusai.com
VITE_DEV_USER_NAME=Prabhat Kumar
```

---

## 2. `vite.config.ts` — Pre-bundle MSAL

**What changed:** Added `optimizeDeps.include` for `@azure/msal-browser` and `@azure/msal-react`.

**Why:** `@azure/msal-browser` v4.30+ does not ship a pre-built `dist/index.mjs`. Without this, Vite fails at startup with a missing module error.

```ts
optimizeDeps: {
  include: ["@azure/msal-browser", "@azure/msal-react"],
}
```

---

## 3. `src/contexts/AuthContext.tsx` — Azure AD Bypass

**What changed:** Added `DEV_BYPASS_AUTH` check at the top of the auth initialization and `login()` function.

**How it works:**
- `DEV_BYPASS_AUTH` and `DEV_USER` constants are defined **after** `ALL_MODULES` (order matters — `DEV_USER.allowedModules` references `ALL_MODULES`)
- If bypass is active, `setUser(DEV_USER)` is called immediately and the MSAL flow is skipped
- `DEV_USER.id` is set to the actual email so DB lookups by `user_id` work correctly

**Key addition:**
```ts
const DEV_USER = {
  id: import.meta.env.VITE_DEV_USER_EMAIL || "dev@local",
  email: import.meta.env.VITE_DEV_USER_EMAIL || "dev@local",
  name: import.meta.env.VITE_DEV_USER_NAME || "Dev User",
  groups: [] as string[],
  allowedModules: ALL_MODULES,   // full access
};
```

---

## 4. `src/services/authService.ts` — Token Bypass

**What changed:** Both `getAccessToken()` and `getEffectiveToken()` now return `"dev-bypass-token"` immediately when bypass is active.

**Why:** Without this, every API call would receive a `null` token, trigger a "Session expired" toast, and abort — even though the backend also has bypass mode enabled.

---

## 5. `src/pages/HarnessPage.tsx` — Fixed Sidebar Scrolling

**What changed:** Outer wrapper changed from `minHeight` + scrollable to fixed `height` + `overflow-hidden`. Sidebar gets its own `overflow-y-auto`.

**Before:**
```tsx
<div className="flex" style={{ minHeight: "calc(100vh - 64px)" }}>
  <div className="w-44 ... flex-shrink-0">
```

**After:**
```tsx
<div className="flex overflow-hidden" style={{ height: "calc(100vh - 64px)" }}>
  <div className="w-44 ... flex-shrink-0 overflow-y-auto">
```

**Why:** `minHeight` allowed the full page to scroll, which caused the left nav (Settings, Overview, Pipelines, etc.) to scroll away with the content. Fixed height constrains both panels independently.

---

## 6. `src/pages/TerraformGeneratorPage.tsx` — Major Redesign

### 6a. Two-Card Mode Chooser (Landing Screen)

When entering the Terraform section a card chooser is shown first:

| Card | Label | Mode |
|------|-------|------|
| Blue / Wrench icon | **Existing Project** — Manage Existing IaC | `brownfield` |
| Green / Wand icon  | **New Project** — Generate New Terraform   | `greenfield` |

State added: `mode: "choose" | "brownfield" | "greenfield"` (default `"choose"`).

Each card has a Back to start button to return to the chooser.

---

### 6b. Existing Project Flow (formerly Brownfield)

Full Bitbucket-to-edit-to-push flow without any stored Atlassian account:

**Phase 1 — Connect:**
- Email, API token, workspace fields inline
- Calls `repositories-direct/{workspace}` with credentials as query params
- Workspace field accepts full `https://bitbucket.org/...` URL — `parseBitbucketWorkspace()` extracts the slug

**Phase 2 — Pick repo:**
- Repository dropdown (populated from Phase 1 response)
- Branch dropdown (loaded on repo select via `branches-direct`)
- Optional subfolder filter
- Fetch button calls `fetch-files-direct`

**Phase 3 — View & Edit:**
- File tree sidebar + code viewer
- **Edit / Done** toggle on the code viewer — switches between `<pre>` (read) and `<textarea>` (edit)
- Edited content is saved back to `files` state in real time
- **Push changes to Bitbucket** panel with pre-populated credentials from Phase 1
- Push panel shows amber warning: requires `write:repository:bitbucket` scope

---

### 6c. New Project Flow (formerly Greenfield)

Unchanged from original — full SAD upload → component extraction → Terraform generation → Checkov scan → push/download flow.

**Additional changes within this flow:**
- Removed **Regenerate** button (no longer needed)
- Added **Edit / Done** inline code editor on the generated code viewer
- **Load from Bitbucket** panel now uses `*-direct` endpoints with inline credentials (same as Existing Project flow)
- Bitbucket Push panel now has inline email + token fields (no stored account required)

---

### 6d. Bitbucket Direct Endpoint Integration

All Bitbucket calls now use the `-direct` endpoints and pass credentials as query params or request body:

| Old endpoint | New endpoint |
|---|---|
| `bitbucket/repositories/{ws}` | `bitbucket/repositories-direct/{ws}?email=&api_token=` |
| `bitbucket/branches/{ws}/{repo}` | `bitbucket/branches-direct/{ws}/{repo}?email=&api_token=` |
| `bitbucket/fetch-files/{ws}/{repo}` | `bitbucket/fetch-files-direct/{ws}/{repo}?email=&api_token=` |

Push target `push-bitbucket` now sends `email` and `api_token` in the request body.

---

### 6e. Dead Code Removed

- `fetchWorkspaces` / `setFetchWorkspaces` state removed
- `bbWorkspaces` / `bbLoadingWs` / `bbStatus` state removed
- `loadBitbucketWorkspaces()` function removed (was calling `/workspaces` which needs `account` scope)
- `loadFetchRepos()` function replaced by inline call inside `connectFetchBitbucket()`

---

## Summary

All changes fall into two themes:

1. **Local dev without Azure AD** — bypass in `.env`, `AuthContext`, `authService`, and `vite.config`
2. **Bitbucket without stored Atlassian account** — direct credential endpoints, inline credential fields in both the Existing Project flow and the New Project push/fetch panels
