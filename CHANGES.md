# Backend Changes — lucid-backend

## 1. `auth.py` — Dev Bypass for Azure AD

**What changed:** Added a development mode that skips Azure AD JWT validation entirely.

**Why:** The app is blocked by Azure AD cross-tenant policy in the local dev environment. Bypass allows running and testing without valid Azure tokens.

**How it works:**
- Reads `DEV_BYPASS_AUTH` env var (set in `.env`)
- If `true`, returns a mock user dict instead of validating the JWT
- Mock user is assigned to both `BUSINESS_GROUP_OID` and `TECH_GROUP_OID` (full access)
- User identity (`oid`, `sub`, `preferred_username`) is set to `DEV_USER_EMAIL` so DB lookups work correctly

**Config required in `.env`:**
```
DEV_BYPASS_AUTH=true
DEV_USER_EMAIL=prabhat.kumar@siriusai.com
DEV_USER_NAME=Prabhat Kumar
```

---

## 2. `routers/integrations.py` — Bitbucket Direct Credential Endpoints

**What changed:** Added 4 new Bitbucket endpoints that accept credentials directly as request parameters instead of reading stored Atlassian account credentials from the database.

**Why:** The existing endpoints require the user to have linked their Atlassian account in Settings first. In practice the stored token often has the wrong scope. The direct endpoints let the user paste credentials inline — matching the working Streamlit app approach (`HTTPBasicAuth(email, api_token)`).

**New endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/integrations/bitbucket/connect-direct` | Test credentials and return workspace list |
| `GET`  | `/api/integrations/bitbucket/repositories-direct/{workspace}` | List repos for a workspace |
| `GET`  | `/api/integrations/bitbucket/branches-direct/{workspace}/{repo_slug}` | List branches for a repo |
| `GET`  | `/api/integrations/bitbucket/fetch-files-direct/{workspace}/{repo_slug}` | Fetch `.tf` / `.tfvars` / `.hcl` files |

**How credentials are passed:**
- `POST connect-direct` — body JSON: `{ "email": "...", "api_token": "..." }`
- `GET` endpoints — query params: `?email=...&api_token=...`

**Required token scope:** `read:repository:bitbucket`

**Important:** The `repositories-direct/{workspace}` endpoint calls `/repositories/{workspace}` directly — it does NOT call `/user` or `/workspaces` first, which avoids the `account` scope requirement that caused 401 errors with read-only tokens.

---

## 3. `routers/terraform_generator.py` — Bitbucket Push Uses Inline Credentials

**What changed:**
- `BitbucketPushRequest` model got two new required fields: `email: str` and `api_token: str`
- `push_to_bitbucket` handler now reads credentials from the request body instead of fetching them from the DB

**Before:**
```python
credentials = get_user_atlassian_credentials(user_id)
email = credentials["atlassian_email"]
api_token = credentials["atlassian_api_token"]
```

**After:**
```python
email = req.email.strip()
api_token = req.api_token.strip()
```

**Why:** Consistent with the direct-credential approach used for fetching — the user provides credentials in the Push panel UI, no stored account dependency.

---

## Summary

All three changes work together to enable a fully functional local dev experience:

1. **Auth bypass** → no Azure AD token needed
2. **Direct Bitbucket endpoints** → no stored Atlassian account needed for browsing/fetching repos
3. **Inline Bitbucket push credentials** → no stored Atlassian account needed for pushing

The frontend sends credentials entered by the user in the Terraform → Existing Project panel directly to these endpoints.
