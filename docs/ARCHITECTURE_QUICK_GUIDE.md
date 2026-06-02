# Backend Architecture - Quick Guide

## The 3-Layer Pattern (TL;DR)

Your Atlassian integration uses a **3-layer architecture**. Each layer has ONE job:

```
Frontend (React)
    ↓ HTTP Request
Router Layer (routers/integrations.py)     ← Handles HTTP, validates input, returns JSON
    ↓ Function call
Service Layer (services/jira_service.py)   ← Talks to external APIs (Jira/Confluence)
    ↓ Function call
Data Layer (db_helper.py)                  ← Reads/writes database
    ↓ SQL query
Database (PostgreSQL)
```

---

## Real Example: Link Atlassian Account

**User clicks "Link Account" button** → Here's what happens:

### 1. Router Layer (`routers/integrations.py`)
```python
@router.post("/atlassian/link")
async def link_atlassian_account(request: LinkAtlassianRequest, current_user: dict):
    # Step 1: Validate credentials using Service
    jira_service = JiraService(request.domain, request.email, request.api_token)
    success, error = jira_service.test_connection()
    
    if not success:
        raise HTTPException(status_code=400, detail=error)
    
    # Step 2: Save to database using Data Layer
    update_user_atlassian_credentials(current_user['id'], request.domain, ...)
    
    # Step 3: Return response
    return {"status": "success"}
```
**Job**: Handle HTTP request → Orchestrate calls → Return HTTP response

---

### 2. Service Layer (`services/jira_service.py`)
```python
class JiraService:
    def test_connection(self):
        response = requests.get(f"{self.base_url}/rest/api/3/myself", auth=self.auth)
        if response.status_code == 200:
            return (True, None)
        else:
            return (False, "Invalid credentials")
```
**Job**: Make external API calls → Handle errors → Return data

---

### 3. Data Layer (`db_helper.py`)
```python
def update_user_atlassian_credentials(user_id, domain, email, api_token):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users 
        SET atlassian_domain = %s, atlassian_email = %s, atlassian_api_token = %s
        WHERE id = %s
    """, (domain, email, api_token, user_id))
    conn.commit()
```
**Job**: Execute SQL queries → Manage transactions → Return data

---

## Why Separate Layers?

| Benefit | Example |
|---------|---------|
| **Testable** | Can test Jira API calls without touching database |
| **Reusable** | Use `JiraService` in API endpoints, background jobs, CLI tools |
| **Maintainable** | If Jira API changes, only update `jira_service.py` |
| **Clear responsibility** | Each file has ONE purpose |

---

## Common Mistakes

❌ **Don't do this:**
```python
# BAD: Database logic in router
@router.post("/atlassian/link")
async def link_account(...):
    conn = psycopg2.connect(...)  # ❌ Database in router
    cursor.execute("UPDATE users...")
```

✅ **Do this:**
```python
# GOOD: Use data layer
@router.post("/atlassian/link")
async def link_account(...):
    update_user_atlassian_credentials(...)  # ✅ Call data layer
```

---

## Quick Rules

1. **Router Layer**: Only HTTP stuff (routes, validation, auth, responses)
2. **Service Layer**: Only business logic (external APIs, transformations)
3. **Data Layer**: Only database stuff (SQL queries, transactions)
4. **Never skip layers**: Router → Service → Data (in that order)

---

## Practice Exercise

**Task**: Add endpoint to get Jira issues assigned to current user

**Solution**:
```python
# 1. Router (routers/integrations.py)
@router.get("/jira/my-issues")
async def get_my_issues(current_user: dict = Depends(get_current_user)):
    credentials = get_user_atlassian_credentials(current_user['id'])  # Data layer
    jira = JiraService(credentials['domain'], credentials['email'], credentials['token'])
    issues = jira.get_my_issues(credentials['email'])  # Service layer
    return {"issues": issues}

# 2. Service (services/jira_service.py)
class JiraService:
    def get_my_issues(self, email):
        jql = f'assignee = "{email}"'
        response = requests.get(f"{self.base_url}/rest/api/3/search", params={"jql": jql})
        return response.json()['issues']

# 3. Data (db_helper.py) - already exists!
def get_user_atlassian_credentials(user_id):
    # ... existing function
```

---

## That's It!

This pattern is used by **every major tech company**. Master this and you're 80% of the way to being a senior backend engineer.

**Next steps:**
1. Trace one request through your code (frontend → router → service → data → database)
2. Add a new endpoint following this pattern
3. Read the other architecture docs for more details
