# SDLC Orchestrator Platform - SSO Integration Vendor Document

**Application:** Deluxe SDLC Orchestrator
**Version:** 1.0
**Date:** March 2026
**Classification:** Internal / Vendor

---

## 1. Overview

The Deluxe SDLC Orchestrator is a web application that assists software development teams with Business Requirements Document (BRD) generation, Jira/Confluence integration, and AI-powered project analysis. The application requires Single Sign-On (SSO) integration with **Microsoft Entra ID** (formerly Azure Active Directory) for user authentication and authorization.

### Architecture

| Component | Technology | Hosting |
|---|---|---|
| Frontend (SPA) | React + TypeScript + Vite | AWS (ECS / CloudFront) |
| Backend API | Python FastAPI | AWS ECS |
| Database | PostgreSQL (RDS) | AWS RDS |
| AI Engine | AWS Bedrock AgentCore | AWS |

---

## 2. SSO Protocol

| Parameter | Value |
|---|---|
| **Protocol** | OpenID Connect (OIDC) 1.0 over OAuth 2.0 |
| **Grant Type** | Authorization Code with PKCE (Proof Key for Code Exchange) |
| **Client Type** | Public Client (Single Page Application) |
| **Token Type Used** | ID Token (JWT) |
| **Signing Algorithm** | RS256 (RSA Signature with SHA-256) |
| **MSAL Library** | `@azure/msal-browser` v4.27.0, `@azure/msal-react` v3.0.23 |

---

## 3. Entra ID App Registration Requirements

### 3.1 Application Registration

A single Entra ID **App Registration** is required with the following configuration:

| Setting | Value |
|---|---|
| **Application (Client) ID** | `10eda5db-4715-4e7b-bcd9-32dba3533084` |
| **Directory (Tenant) ID** | `0575746d-c254-4eea-bfc6-10d0979d1e90` |
| **Supported Account Types** | Single tenant (this organization only) |
| **Application Type** | Single Page Application (SPA) |
| **Client Secret** | Not required (public client / SPA flow with PKCE) |

### 3.2 Redirect URIs

Configure the following **SPA** redirect URIs under **Authentication > Platform configurations > Single-page application**:

| Environment | Redirect URI |
|---|---|
| Production | `https://sdlc-dev.deluxe.com` |
| Development | `http://localhost:8080` |
| Development (alt) | `http://localhost:5173` |

> **Note:** Do NOT configure these as "Web" redirect URIs. They must be registered under the **SPA** platform to enable PKCE and avoid CORS issues.

### 3.3 API Permissions

The application requires the following **Microsoft Graph** delegated permissions:

| Permission | Type | Description | Admin Consent Required |
|---|---|---|---|
| `User.Read` | Delegated | Sign in and read user profile | No |

No additional API permissions or application-level permissions are required. The application does not call the Microsoft Graph API directly -- the `User.Read` scope is used solely to obtain an ID token containing the user's identity claims.

### 3.4 Token Configuration

Under **Token configuration**, ensure the following **optional claims** are included in the **ID Token**:

| Claim | Purpose |
|---|---|
| `email` | User's email address |
| `preferred_username` | User's UPN / login name |
| `upn` | User Principal Name (fallback) |

> These claims are used by the backend to identify and provision the user in the application database.

---

## 4. Authentication Flow

### 4.1 End-to-End Process Flow

```
                                    SDLC Orchestrator SSO Flow
                                    ==========================

  User Browser                   Frontend (SPA)               Entra ID                 Backend API
  ============                   ==============               =========                ===========
       |                              |                           |                         |
  1.   |--- Navigate to app --------->|                           |                         |
       |                              |                           |                         |
  2.   |                              |-- Check sessionStorage -->|                         |
       |                              |   (existing MSAL cache?)  |                         |
       |                              |                           |                         |
  3.   |<-- Redirect to /login -------|  (if no cached session)   |                         |
       |                              |                           |                         |
  4.   |--- Click "Sign in with       |                           |                         |
       |    Microsoft" -------------->|                           |                         |
       |                              |                           |                         |
  5.   |                              |-- loginPopup() ---------->|                         |
       |                              |   scopes: [User.Read]     |                         |
       |                              |                           |                         |
  6.   |<---------- Popup window opens for Entra ID login --------|                         |
       |                              |                           |                         |
  7.   |--- Enter credentials --------|-------------------------->|                         |
       |                              |                           |                         |
  8.   |                              |<-- AuthenticationResult --|                         |
       |                              |    (idToken, account)     |                         |
       |                              |                           |                         |
  9.   |                              |-- Store in sessionStorage |                         |
       |                              |-- Set AuthContext state   |                         |
       |                              |   (user, token, isAuth)   |                         |
       |                              |                           |                         |
  10.  |<-- Redirect to Dashboard ----|                           |                         |
       |                              |                           |                         |
  11.  |                              |-- API Request ------------|------------------------>|
       |                              |   Authorization:          |                         |
       |                              |   Bearer {idToken}        |                         |
       |                              |                           |                         |
  12.  |                              |                           |                   Extract JWT
       |                              |                           |                   Decode header
       |                              |                           |                   Get kid (key ID)
       |                              |                           |                         |
  13.  |                              |                           |<-- Fetch JWKS keys -----|
       |                              |                           |    /discovery/v2.0/keys |
       |                              |                           |-- Return public keys -->|
       |                              |                           |                         |
  14.  |                              |                           |                   Verify signature
       |                              |                           |                   Validate issuer
       |                              |                           |                   Extract claims
       |                              |                           |                         |
  15.  |                              |                           |                   Create/update user
       |                              |                           |                   in app database
       |                              |                           |                         |
  16.  |                              |<-- API Response --------- |-------------------------|
       |                              |                           |                         |
```

### 4.2 Silent Token Renewal

After the initial login, subsequent API calls acquire tokens silently:

1. Frontend calls `acquireTokenSilent()` using the cached MSAL account
2. MSAL checks if the cached ID token is still valid
3. If expired, MSAL silently refreshes using the cached refresh token
4. If silent renewal fails, the user is prompted to re-authenticate via popup

---

## 5. Backend Token Verification

### 5.1 JWKS Endpoints

The backend validates the ID token JWT signature by fetching public signing keys from Entra ID's JWKS endpoints:

| Priority | Endpoint |
|---|---|
| Primary (v2.0) | `https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys` |
| Fallback (v1.0) | `https://login.microsoftonline.com/{tenant_id}/discovery/keys` |
| Last resort | `https://login.microsoftonline.com/common/discovery/keys` |

### 5.2 Token Validation Steps

The backend performs the following validation on every API request:

1. **Extract** the `Authorization: Bearer {token}` header
2. **Decode** the JWT header to obtain the `kid` (Key ID)
3. **Fetch** the matching public key from the JWKS endpoint
4. **Verify** the JWT signature using RS256 algorithm
5. **Validate** the issuer claim matches:
   - v2.0: `https://login.microsoftonline.com/{tenant_id}/v2.0`
   - v1.0: `https://sts.windows.net/{tenant_id}/`
6. **Extract** user identity claims from the token payload

### 5.3 Claims Used

| Claim | Priority | Purpose |
|---|---|---|
| `oid` | Primary | Azure AD Object ID — unique user identifier |
| `sub` | Fallback | Subject identifier (used if `oid` absent) |
| `preferred_username` | Primary | User's email / login name |
| `email` | Fallback | Email address |
| `upn` | Fallback | User Principal Name |
| `name` | Optional | Display name |

### 5.4 User Provisioning

On first successful authentication, the backend automatically provisions the user:

1. Extracts `user_id` (from `oid`/`sub`) and `email` (from `preferred_username`/`email`/`upn`)
2. Calls `create_or_update_user(user_id, email, name)` to upsert in the application database
3. Returns the user context to the API handler for authorization checks

---

## 6. CORS Configuration

The backend must allow cross-origin requests from the frontend SPA:

| Setting | Value |
|---|---|
| **Allowed Origins** | `https://sdlc-dev.deluxe.com`, `http://localhost:8080`, `http://localhost:5173` |
| **Allowed Methods** | `*` (all HTTP methods) |
| **Allowed Headers** | `*`, `Authorization`, `Content-Type` |
| **Allow Credentials** | `true` |
| **Expose Headers** | `*` |

---

## 7. Session & Token Storage

| Item | Storage Location | Lifetime |
|---|---|---|
| MSAL Cache (tokens, accounts) | Browser `sessionStorage` | Cleared on tab/browser close |
| ID Token | In-memory (AuthContext state) | Until page refresh or expiry |
| Refresh Token | Managed by MSAL internally | Per Entra ID policy (default 24h) |
| Backend session | No server-side sessions | Stateless — each request validated independently |

> **Security Note:** The application does NOT use `localStorage` for token storage, mitigating XSS token exfiltration risks. `sessionStorage` is isolated per-tab and cleared on close.

---

## 8. Logout Flow

1. Frontend calls `msalInstance.logoutPopup()` with the current account
2. MSAL clears the `sessionStorage` cache
3. Entra ID session is terminated via the popup
4. Frontend `AuthContext` clears `user`, `accessToken`, and `isAuthenticated` state
5. User is redirected to `/login`

---

## 9. Network & Firewall Requirements

The following outbound network access is required from the user's browser:

| Destination | Port | Purpose |
|---|---|---|
| `login.microsoftonline.com` | 443 (HTTPS) | Entra ID authentication & token endpoints |
| `sdlc-dev.deluxe.com` | 443 (HTTPS) | Application frontend |
| Backend API endpoint | 443 (HTTPS) | Application API |

The backend requires outbound access to:

| Destination | Port | Purpose |
|---|---|---|
| `login.microsoftonline.com` | 443 (HTTPS) | JWKS key retrieval for token verification |
| AWS services (Bedrock, S3, RDS) | 443 | Application infrastructure |

---

## 10. Entra ID Configuration Checklist

- [ ] **App Registration** created as Single Page Application (SPA)
- [ ] **Client ID** configured: `10eda5db-4715-4e7b-bcd9-32dba3533084`
- [ ] **Tenant ID** configured: `0575746d-c254-4eea-bfc6-10d0979d1e90`
- [ ] **Redirect URIs** added under SPA platform (not Web):
  - [ ] `https://sdlc-dev.deluxe.com`
  - [ ] `http://localhost:8080` (dev)
- [ ] **API Permission** granted: `User.Read` (delegated)
- [ ] **Admin consent** granted for `User.Read` (if tenant requires it)
- [ ] **ID Token** enabled under Authentication > Implicit grant (if needed for fallback)
- [ ] **Optional claims** configured: `email`, `preferred_username`, `upn`
- [ ] **User assignment** configured (if restricting access to specific users/groups)
- [ ] **Token lifetime policy** reviewed (default: 1 hour ID token, 24 hour refresh)

---

## 11. Troubleshooting

| Issue | Cause | Resolution |
|---|---|---|
| AADSTS50011: Reply URL mismatch | Redirect URI not registered or registered under wrong platform | Ensure URI is under **SPA** platform, not **Web** |
| AADSTS700054: response_type 'id_token' not enabled | Implicit grant not enabled | Enable ID tokens under Authentication settings, or ensure PKCE flow is used |
| CORS error on token request | Redirect URI registered as Web instead of SPA | Move URI to SPA platform configuration |
| 401 Unauthorized on API calls | Token expired or JWKS key rotation | Frontend auto-retries with fresh token; backend fetches latest JWKS keys |
| User not provisioned | Claims missing from token | Add optional claims (`email`, `upn`) in Token Configuration |

---

## 12. Security Considerations

1. **No client secret** — SPA uses PKCE (public client), eliminating secret management in browser code
2. **Single-tenant** — Only users from the configured Entra ID tenant can authenticate
3. **Minimal permissions** — Only `User.Read` delegated scope; no application-level permissions
4. **Stateless backend** — No server-side sessions; each request independently validated via JWT verification
5. **Session storage** — Tokens stored in `sessionStorage` (not `localStorage`), cleared on browser close
6. **JWKS verification** — Backend verifies JWT signatures against Entra ID's published public keys (RS256)
7. **Automatic key rotation** — Backend fetches fresh JWKS keys on cache miss, supporting Entra ID key rotation

---

## 13. Contact

For questions regarding this integration, contact the SDLC Orchestrator development team.
