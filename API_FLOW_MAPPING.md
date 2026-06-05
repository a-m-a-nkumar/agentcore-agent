# Full Stack API Mapping - Frontend → Backend → Database

This document maps the complete flow from Frontend API calls → Backend Routes → Database Helper Functions.

---

## 📋 Table of Contents
1. [Projects Flow](#projects-flow)
2. [Sessions Flow](#sessions-flow)
3. [Integrations Flow](#integrations-flow)
4. [Authentication Flow](#authentication-flow)

---

## 🗂️ Projects Flow

### 1. **Get All Projects**
**User Action**: User logs in / Opens app

**Frontend** (`projectApi.ts:90`)
```typescript
fetchProjects() → GET /api/projects/
```

**Backend** (`routers/projects.py:92`)
```python
@router.get("/")
async def get_projects(current_user)
```

**DB Helper** (`db_helper.py:167`)
```python
get_user_projects(user_id, include_deleted=False)
```

**SQL Query**:
```sql
SELECT id, user_id, project_name, description,
       jira_project_key, confluence_space_key,
       created_at, updated_at, is_deleted
FROM projects
WHERE user_id = %s AND is_deleted = FALSE
ORDER BY updated_at DESC
```

**Why**: Fetches all active projects for the logged-in user, sorted by most recently updated.

**Returns**: List of projects with timestamps converted to milliseconds for frontend.

---

### 2. **Create Project**
**User Action**: User clicks "New Project" button

**Frontend** (`projectApi.ts:73`)
```typescript
createProject(data) → POST /api/projects/
Body: { project_id: uuid(), project_name, description, jira_project_key, confluence_space_key }
```

**Backend** (`routers/projects.py:106`)
```python
@router.post("/", status_code=201)
async def create_new_project(project_data, current_user)
```

**DB Helper** (`db_helper.py:120`)
```python
create_project(project_id, user_id, project_name, description, 
               jira_project_key, confluence_space_key)
```

**SQL Query**:
```sql
INSERT INTO projects (
    id, user_id, project_name, description,
    jira_project_key, confluence_space_key
) VALUES (%s, %s, %s, %s, %s, %s)
RETURNING *
```

**Why**: 
- Frontend generates UUID for optimistic UI (instant navigation)
- `RETURNING *` gets back `created_at` and `updated_at` timestamps from DB
- Backend sends complete project object back to frontend

**Returns**: Newly created project with DB-generated timestamps.

---

### 3. **Get Single Project**
**User Action**: User selects a project from dropdown

**Frontend** (`projectApi.ts:102`)
```typescript
getProjectById(projectId) → GET /api/projects/{project_id}
```

**Backend** (`routers/projects.py:136`)
```python
@router.get("/{project_id}")
async def get_project_by_id(project_id, current_user)
```

**DB Helper** (`db_helper.py:207`)
```python
get_project(project_id)
```

**SQL Query**:
```sql
SELECT * FROM projects 
WHERE id = %s AND is_deleted = FALSE
```

**Why**: 
- Fetches specific project details
- Backend verifies user owns the project (security check)
- Used when user switches projects

**Returns**: Single project object or 404 if not found.

---

### 4. **Update Project**
**User Action**: User edits project name/description/Jira key

**Frontend** (`projectApi.ts:114`)
```typescript
updateProject(projectId, updates) → PATCH /api/projects/{project_id}
Body: { project_name?, description?, jira_project_key?, confluence_space_key? }
```

**Backend** (`routers/projects.py:162`)
```python
@router.patch("/{project_id}")
async def update_project_by_id(project_id, project_data, current_user)
```

**DB Helper** (`db_helper.py:228`)
```python
update_project(project_id, project_name, description, 
               jira_project_key, confluence_space_key)
```

**SQL Query** (Dynamic):
```sql
UPDATE projects
SET project_name = %s, description = %s  -- Only fields provided
WHERE id = %s
RETURNING *
```

**Why**: 
- **Dynamic query building**: Only updates fields that are provided
- `RETURNING *` gets the updated `updated_at` timestamp (set by DB trigger)
- Frontend needs new timestamp to re-sort project list

**Returns**: Updated project with new `updated_at` timestamp.

---

### 5. **Delete Project**
**User Action**: User clicks delete button on project

**Frontend** (`projectApi.ts:130`)
```typescript
deleteProject(projectId) → DELETE /api/projects/{project_id}
```

**Backend** (`routers/projects.py:205`)
```python
@router.delete("/{project_id}", status_code=204)
async def delete_project_by_id(project_id, hard_delete=True, current_user)
```

**DB Helper** (`db_helper.py:287`)
```python
delete_project(project_id, hard_delete=False)
```

**SQL Query** (Hard Delete):
```sql
DELETE FROM projects WHERE id = %s
```

**SQL Query** (Soft Delete):
```sql
UPDATE projects 
SET is_deleted = TRUE 
WHERE id = %s
```

**Why**: 
- Hard delete permanently removes from DB
- Soft delete marks as deleted but keeps data (default in your app)
- Uses `cursor.rowcount` to verify deletion succeeded

**Returns**: 204 No Content (success) or 404 if not found.

---

## 💬 Sessions Flow

### 1. **Get Project Sessions**
**User Action**: User selects a project, sidebar shows chat history

**Frontend** (`sessionsApi.ts:34`)
```typescript
getProjectSessions(projectId) → GET /api/sessions/?project_id={projectId}
```

**Backend** (`routers/sessions.py:64`)
```python
@router.get("/")
async def get_sessions(project_id: Query, current_user)
```

**DB Helper** (`db_helper.py:381`)
```python
get_project_sessions(project_id, user_id, include_deleted=False)
```

**SQL Query**:
```sql
SELECT id, project_id, user_id, title, brd_id, 
       message_count, created_at, last_updated, is_deleted
FROM analyst_sessions
WHERE project_id = %s AND user_id = %s AND is_deleted = FALSE
ORDER BY last_updated DESC
```

**Why**: 
- Shows all chat sessions for the selected project
- Sorted by most recent activity
- **NO JOIN needed** - sessions table has `project_id` directly

**Returns**: List of sessions sorted by `last_updated`.

---

### 2. **Create Session**
**User Action**: User clicks "New Chat"

**Frontend** (`sessionsApi.ts:62`)
```typescript
createSession(data) → POST /api/sessions/
Body: { session_id: uuid(), project_id, title: "New Chat" }
```

**Backend** (`routers/sessions.py:94`)
```python
@router.post("/", status_code=201)
async def create_new_session(session_data, current_user)
```

**DB Helper** (`db_helper.py:337`)
```python
create_session(session_id, project_id, user_id, title)
```

**SQL Query**:
```sql
INSERT INTO analyst_sessions (id, project_id, user_id, title)
VALUES (%s, %s, %s, %s)
RETURNING *
```

**Why**: 
- Frontend generates session ID for instant navigation
- `RETURNING *` gets `created_at` and `last_updated` timestamps
- Backend verifies user owns the project before creating session

**Returns**: New session with DB timestamps.

---

### 3. **Get Single Session**
**User Action**: User clicks on a chat in sidebar

**Frontend** (`sessionsApi.ts:48`)
```typescript
getSession(sessionId) → GET /api/sessions/{session_id}
```

**Backend** (`routers/sessions.py:129`)
```python
@router.get("/{session_id}")
async def get_session_by_id(session_id, current_user)
```

**DB Helper** (`db_helper.py:430`)
```python
get_session(session_id)
```

**SQL Query**:
```sql
SELECT * FROM analyst_sessions 
WHERE id = %s AND is_deleted = FALSE
```

**Why**: 
- Loads specific chat session
- Backend verifies user owns the session

**Returns**: Session object with all details.

---

### 4. **Update Session**
**User Action**: User renames chat or BRD is generated

**Frontend** (`sessionsApi.ts:76`)
```typescript
updateSession(sessionId, updates) → PATCH /api/sessions/{session_id}
Body: { title?, brd_id?, message_count? }
```

**Backend** (`routers/sessions.py:155`)
```python
@router.patch("/{session_id}")
async def update_session_by_id(session_id, session_data, current_user)
```

**DB Helper** (`db_helper.py:451`)
```python
update_session(session_id, title, brd_id, message_count)
```

**SQL Query** (Dynamic):
```sql
UPDATE analyst_sessions
SET title = %s, brd_id = %s  -- Only provided fields
WHERE id = %s
RETURNING *
```

**Why**: 
- Dynamic query: only updates fields that are provided
- `RETURNING *` gets new `last_updated` timestamp (DB trigger)
- Frontend uses new timestamp to re-sort session list

**Returns**: Updated session with new `last_updated`.

---

### 5. **Increment Message Count**
**User Action**: User sends a message in chat

**Frontend** (`sessionsApi.ts:94`)
```typescript
incrementMessageCount(sessionId) → POST /api/sessions/{session_id}/increment-messages
```

**Backend** (`routers/sessions.py:189`)
```python
@router.post("/{session_id}/increment-messages")
async def increment_session_messages(session_id, current_user)
```

**DB Helper** (`db_helper.py:515`)
```python
increment_message_count(session_id)
```

**SQL Query**:
```sql
UPDATE analyst_sessions
SET message_count = message_count + 1
WHERE id = %s
RETURNING message_count
```

**Why**: 
- Atomic increment (prevents race conditions)
- `RETURNING message_count` gets the new count
- Shows user how many messages in the chat

**Returns**: `{ session_id, message_count: 5 }`

---

### 6. **Delete Session**
**User Action**: User deletes a chat

**Frontend** (`sessionsApi.ts:108`)
```typescript
deleteSession(sessionId) → DELETE /api/sessions/{session_id}
```

**Backend** (`routers/sessions.py:217`)
```python
@router.delete("/{session_id}", status_code=204)
async def delete_session_by_id(session_id, hard_delete=True, current_user)
```

**DB Helper** (`db_helper.py:538`)
```python
delete_session(session_id, hard_delete=False)
```

**SQL Query** (Hard Delete):
```sql
DELETE FROM analyst_sessions WHERE id = %s
```

**SQL Query** (Soft Delete):
```sql
UPDATE analyst_sessions 
SET is_deleted = TRUE 
WHERE id = %s
```

**Why**: Similar to project deletion - soft delete by default.

**Returns**: 204 No Content.

---

## 🔗 Integrations Flow

### 1. **Link Atlassian Account**
**User Action**: User enters Jira credentials

**Frontend** (`integrationsApi.ts:27`)
```typescript
linkAtlassianAccount(data) → POST /api/integrations/atlassian/link
Body: { domain, email, api_token }
```

**Backend** (`routers/integrations.py:79`)
```python
@router.post("/atlassian/link")
async def link_atlassian_account(credentials, current_user)
```

**DB Helper** (`db_helper.py:576`)
```python
update_user_atlassian_credentials(user_id, domain, email, api_token)
```

**SQL Query**:
```sql
UPDATE users 
SET atlassian_domain = %s,
    atlassian_email = %s,
    atlassian_api_token = %s,
    atlassian_linked_at = NOW()
WHERE id = %s
```

**Why**: 
- Stores encrypted credentials in users table
- Tests connection before saving (calls Jira API)
- Sets `atlassian_linked_at` timestamp

**Returns**: `{ success: true, message: "Linked successfully" }`

---

### 2. **Get Atlassian Status**
**User Action**: App checks if user has linked Atlassian

**Frontend** (`integrationsApi.ts:50`)
```typescript
getAtlassianStatus() → GET /api/integrations/atlassian/status
```

**Backend** (`routers/integrations.py:113`)
```python
@router.get("/atlassian/status")
async def get_atlassian_status(current_user)
```

**DB Helper** (`db_helper.py:605`)
```python
get_user_atlassian_credentials(user_id)
```

**SQL Query**:
```sql
SELECT atlassian_domain, atlassian_email, 
       atlassian_api_token, atlassian_linked_at
FROM users
WHERE id = %s
```

**Why**: 
- Checks if user has linked credentials
- Used to show/hide "Link Atlassian" banner
- Cached in frontend (React Query, 5 min staleTime)

**Returns**: `{ linked: true/false, domain?, email?, linked_at? }`

---

### 3. **Get Jira Projects**
**User Action**: User opens "Create Project" modal

**Frontend** (`integrationsApi.ts:67`)
```typescript
getJiraProjects() → GET /api/integrations/jira/projects
```

**Backend** (`routers/integrations.py:129`)
```python
@router.get("/jira/projects")
async def list_jira_projects(current_user)
```

**External Service** (`services/jira_service.py:54`)
```python
JiraService.get_projects()
```

**Jira API Call**:
```
GET https://{domain}.atlassian.net/rest/api/3/project
```

**Why**: 
- Fetches all Jira projects user has access to
- Used in dropdown when creating/editing project
- No DB query - calls Jira API directly

**Returns**: `{ projects: [{ key: "PROJ", name: "My Project" }] }`

---

### 4. **Get Jira Issues**
**User Action**: User navigates to Jira page

**Frontend** (`integrationsApi.ts:113`)
```typescript
getJiraIssues(projectKey) → GET /api/integrations/jira/issues/{project_key}
```

**Backend** (`routers/integrations.py:181`)
```python
@router.get("/jira/issues/{project_key}")
async def get_jira_issues(project_key, current_user)
```

**External Service** (`services/jira_service.py:82`)
```python
JiraService.get_project_issues(project_key, max_results=100)
```

**Jira API Call**:
```
GET https://{domain}.atlassian.net/rest/api/3/search/jql
?jql=project = {project_key} ORDER BY created DESC
&maxResults=100
&fields=summary,description,status,assignee,reporter,priority,issuetype,created,updated,labels
```

**Why**: 
- Fetches issues from Jira project linked to selected project
- Uses new `/search/jql` endpoint (Atlassian migration)
- No DB query - calls Jira API directly

**Returns**: `{ issues: [...], total: 10 }`

---

### 5. **Get Confluence Spaces**
**User Action**: User opens "Create Project" modal

**Frontend** (`integrationsApi.ts:90`)
```typescript
getConfluenceSpaces() → GET /api/integrations/confluence/spaces
```

**Backend** (`routers/integrations.py:150`)
```python
@router.get("/confluence/spaces")
async def list_confluence_spaces(current_user)
```

**External Service** (`services/confluence_service.py`)
```python
ConfluenceService.get_spaces()
```

**Confluence API Call**:
```
GET https://{domain}.atlassian.net/wiki/rest/api/space
```

**Why**: 
- Fetches all Confluence spaces user has access to
- Used in dropdown when creating/editing project
- No DB query - calls Confluence API directly

**Returns**: `{ spaces: [{ key: "SPACE", name: "My Space" }] }`

---

## 🔐 Authentication Flow

### **Every Protected Route**
**User Action**: Any API call

**Frontend** (`api.ts`)
```typescript
// All API calls include Authorization header
headers: { Authorization: `Bearer ${accessToken}` }
```

**Backend** (`routers/projects.py:67`)
```python
async def get_current_user(token_data = Depends(verify_azure_token))
```

**Auth Service** (`auth.py`)
```python
verify_azure_token(token) → Validates with Azure AD
```

**DB Helper** (`db_helper.py:66`)
```python
create_or_update_user(user_id, email, name)
```

**SQL Query**:
```sql
INSERT INTO users (id, email, name, last_login)
VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
ON CONFLICT (id) DO UPDATE
SET last_login = CURRENT_TIMESTAMP,
    email = EXCLUDED.email,
    name = COALESCE(EXCLUDED.name, users.name)
RETURNING *
```

**Why**: 
- **UPSERT**: Creates user on first login, updates `last_login` on subsequent logins
- `COALESCE`: Only updates name if new value provided
- Every protected route depends on this

**Returns**: User object with `id`, `email`, `name`.

---

## 📊 Summary Table

| Frontend File | Backend Route | DB Helper | Purpose |
|---------------|---------------|-----------|---------|
| `projectApi.ts` | `routers/projects.py` | `db_helper.py` (Projects) | Manage projects |
| `sessionsApi.ts` | `routers/sessions.py` | `db_helper.py` (Sessions) | Manage chat sessions |
| `integrationsApi.ts` | `routers/integrations.py` | `db_helper.py` (Atlassian) + External APIs | Jira/Confluence integration |
| All API files | All routes | `create_or_update_user()` | Authentication |

---

## 🎯 Key Patterns

### 1. **Optimistic UI**
- Frontend generates UUIDs
- User sees instant feedback
- Backend confirms with DB timestamps

### 2. **RETURNING Clause**
- Gets DB-generated data (timestamps, defaults)
- Avoids second SELECT query
- Ensures frontend matches DB state

### 3. **Dynamic Queries**
- Only update fields that are provided
- Prevents overwriting with NULL
- More flexible API

### 4. **Security Checks**
- Every route verifies user ownership
- Prevents unauthorized access
- Returns 403 if user doesn't own resource

### 5. **Soft Deletes**
- Default behavior marks `is_deleted = TRUE`
- Data preserved for recovery
- Hard delete available as option

### 6. **Connection Pooling**
- Reuses DB connections
- Faster than creating new connections
- Handles concurrent requests efficiently
