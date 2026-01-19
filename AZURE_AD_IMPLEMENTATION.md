# Azure AD Authentication & AgentCore Identity Integration

## Overview
This document describes the Azure AD authentication and AgentCore Identity integration implemented for the BRD application.

## What Was Implemented

### Frontend Changes

1. **Azure AD Authentication Service** (`src/services/authService.ts`)
   - MSAL (Microsoft Authentication Library) integration
   - Login/logout functions
   - Token acquisition for API calls
   - User info retrieval

2. **Updated AuthContext** (`src/contexts/AuthContext.tsx`)
   - Integrated with Azure AD authentication
   - Manages user state and access tokens
   - Handles authentication status

3. **Updated Login Page** (`src/pages/Login.tsx`)
   - Replaced demo login with Azure AD login button
   - Uses Microsoft's login popup

4. **API Service Updates**
   - Created centralized API helper (`src/services/api.ts`)
   - All API calls now include Azure AD access token in Authorization header
   - Updated `chatbotApi.ts` and `projectApi.ts` to use authenticated requests

### Backend Changes

1. **Azure AD Token Verification** (`app.py`)
   - JWT token verification using Azure AD JWKS
   - Token validation with audience and issuer checks
   - FastAPI dependency for authentication

2. **AgentCore Identity Integration** (`app.py`)
   - Store user identities in AgentCore Identity
   - Check BRD access via AgentCore Identity metadata
   - Grant/revoke BRD access functions
   - User identity ARN retrieval

3. **Protected Endpoints**
   - `/generate` - BRD generation
   - `/generate-from-s3` - BRD generation from S3
   - `/upload-transcript` - File upload
   - `/chat` - Chat with BRD
   - `/download-brd/{brd_id}` - Download BRD

4. **New Endpoints**
   - `GET /api/brd/access` - Check if user has BRD access
   - `GET /api/user/info` - Get current user information
   - `POST /api/admin/grant-brd-access` - Grant BRD access (admin)
   - `POST /api/admin/revoke-brd-access` - Revoke BRD access (admin)

## Configuration

### Frontend Environment Variables
Create a `.env` file in the frontend root with:
```
VITE_AZURE_CLIENT_ID=10eda5db-4715-4e7b-bcd9-32dba3533084
VITE_AZURE_TENANT_ID=0575746d-c254-4eea-bfc6-10d0979d1e90
```

### Backend Environment Variables
Add to your `.env` file in the backend root:
```
AZURE_CLIENT_ID=10eda5db-4715-4e7b-bcd9-32dba3533084
AZURE_TENANT_ID=0575746d-c254-4eea-bfc6-10d0979d1e90
AZURE_CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=448049797912
```

## Installation

### Frontend
```bash
cd deluxe-sdlc-frontend/deluxe-sdlc-frontend
npm install
```

### Backend
```bash
cd agentcore-starter
pip install -r requirements.txt
```

## How It Works

1. **User Login Flow:**
   - User clicks "Sign in with Microsoft" on login page
   - MSAL opens Azure AD login popup
   - User authenticates with Azure AD
   - Access token is stored and used for API calls

2. **API Request Flow:**
   - Frontend includes Azure AD token in `Authorization: Bearer <token>` header
   - Backend verifies token using Azure AD JWKS
   - Backend checks user's BRD access via AgentCore Identity
   - If access granted, request proceeds; otherwise returns 403

3. **AgentCore Identity:**
   - User identity is stored in AgentCore Identity on first login
   - Metadata includes: email, name, user_id, has_brd_access flag
   - Access control is managed via `has_brd_access` metadata field

## Access Control

- **Default Behavior:** New users are granted BRD access by default
- **Access Check:** Performed on every protected endpoint
- **Admin Functions:** Grant/revoke access via admin endpoints (TODO: add admin role check)

## Notes

1. **AgentCore Identity API:** The current implementation uses placeholder API calls. You may need to adjust based on the actual AgentCore Identity API methods available.

2. **Admin Role Check:** The admin endpoints (`/api/admin/*`) currently don't check for admin role. Add this check based on your requirements.

3. **Token Refresh:** The frontend automatically handles token refresh when tokens expire.

4. **Error Handling:** Both frontend and backend include comprehensive error handling for authentication failures.

## Testing

1. Start the backend:
   ```bash
   cd agentcore-starter
   python app.py
   ```

2. Start the frontend:
   ```bash
   cd deluxe-sdlc-frontend/deluxe-sdlc-frontend
   npm run dev
   ```

3. Navigate to login page and click "Sign in with Microsoft"

4. After successful login, you should be redirected to the dashboard

## Troubleshooting

1. **"Authorization header missing" error:**
   - Ensure the frontend is sending the token in the Authorization header
   - Check that `api.ts` is being used for all API calls

2. **"Invalid token" error:**
   - Token may have expired - frontend should automatically refresh
   - Check Azure AD app registration configuration

3. **"Access denied" error:**
   - User doesn't have BRD access - use admin endpoint to grant access
   - Check AgentCore Identity metadata

4. **MSAL initialization errors:**
   - Ensure environment variables are set correctly
   - Check browser console for detailed error messages

## Next Steps

1. **Add Admin Role Check:** Implement role-based access control for admin endpoints
2. **Audit Logging:** Log all BRD access and modifications
3. **User Management UI:** Create admin UI for managing user access
4. **Lambda Integration:** Update Lambda functions to check user access if needed

