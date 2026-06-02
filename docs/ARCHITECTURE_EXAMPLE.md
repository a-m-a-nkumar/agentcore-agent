# Real-World Architecture Example: Adding a New Feature

Let's say you need to add a feature: **"Fetch Jira issues assigned to current user"**

This example shows how to implement it following the 3-layer architecture.

---

## Step 1: Define the API Endpoint (Router Layer)

**File**: `routers/integrations.py`

```python
@router.get("/jira/my-issues")
async def get_my_jira_issues(
    status: Optional[str] = None,  # Query param: ?status=In Progress
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch Jira issues assigned to the current user
    
    Query Parameters:
        status: Optional filter by issue status (e.g., "In Progress", "Done")
    
    Returns:
        List of Jira issues assigned to the authenticated user
    """
    
    # Step 1: Get user's Atlassian credentials from database (Data Layer)
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    # Step 2: Use Service Layer to fetch issues from Jira
    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        # Get user's email to filter issues
        user_email = credentials['atlassian_email']
        
        # Call service method
        issues = jira_service.get_issues_assigned_to_user(
            assignee_email=user_email,
            status_filter=status
        )
        
        # Step 3: Transform data for frontend (optional)
        formatted_issues = [
            {
                "id": issue["id"],
                "key": issue["key"],
                "summary": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"],
                "priority": issue["fields"]["priority"]["name"],
                "created": issue["fields"]["created"],
                "project": issue["fields"]["project"]["name"]
            }
            for issue in issues
        ]
        
        # Step 4: Return HTTP response
        return {
            "issues": formatted_issues,
            "total": len(formatted_issues)
        }
    
    except Exception as e:
        logger.error(f"Error fetching user's Jira issues: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

**What this layer does:**
- ✅ Defines the HTTP endpoint: `GET /api/integrations/jira/my-issues`
- ✅ Validates authentication (current_user)
- ✅ Parses query parameters (status)
- ✅ Orchestrates calls to Data Layer and Service Layer
- ✅ Transforms data for frontend
- ✅ Returns HTTP response

---

## Step 2: Implement Business Logic (Service Layer)

**File**: `services/jira_service.py`

```python
class JiraService:
    # ... existing __init__, test_connection, get_projects methods ...
    
    def get_issues_assigned_to_user(
        self, 
        assignee_email: str, 
        status_filter: Optional[str] = None,
        max_results: int = 50
    ) -> List[Dict]:
        """
        Fetch Jira issues assigned to a specific user
        
        Args:
            assignee_email: Email of the assignee
            status_filter: Optional status to filter by (e.g., "In Progress")
            max_results: Maximum number of results to return
            
        Returns:
            List of Jira issues
        """
        
        # Build JQL query
        jql_parts = [f'assignee = "{assignee_email}"']
        
        if status_filter:
            jql_parts.append(f'status = "{status_filter}"')
        
        jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"
        
        # Define fields to fetch
        fields = [
            "summary",
            "description",
            "status",
            "priority",
            "issuetype",
            "created",
            "updated",
            "project"
        ]
        
        # Build request parameters
        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ",".join(fields)
        }
        
        try:
            url = f"{self.base_url}/rest/api/3/search"
            logger.info(f"Fetching issues with JQL: {jql}")
            
            response = requests.get(
                url, 
                headers=self.headers, 
                auth=self.auth, 
                params=params, 
                timeout=15
            )
            
            # Handle errors
            if response.status_code == 400:
                error_data = response.json()
                error_msg = error_data.get('errorMessages', ['Invalid JQL'])[0]
                raise Exception(f"Invalid JQL query: {error_msg}")
            
            response.raise_for_status()
            
            result = response.json()
            issues = result.get('issues', [])
            
            logger.info(f"Fetched {len(issues)} issues for {assignee_email}")
            return issues
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Jira issues: {e}")
            raise Exception(f"Failed to fetch Jira issues: {str(e)}")
```

**What this layer does:**
- ✅ Encapsulates Jira API logic
- ✅ Builds JQL queries
- ✅ Makes HTTP requests to Jira
- ✅ Handles Jira-specific errors
- ✅ Returns raw Jira data

**Why it's separate:**
- Can be reused in background jobs, CLI tools, etc.
- Can be tested independently by mocking `requests`
- Changes to Jira API only affect this file

---

## Step 3: Database Operations (Data Layer)

**File**: `db_helper.py`

```python
def get_user_atlassian_credentials(user_id: str) -> Optional[Dict]:
    """
    Fetch user's Atlassian credentials from database
    
    Args:
        user_id: User's unique identifier
        
    Returns:
        Dictionary with atlassian_domain, atlassian_email, atlassian_api_token
        or None if not found
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT 
                atlassian_domain,
                atlassian_email,
                atlassian_api_token,
                atlassian_linked_at
            FROM users
            WHERE id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        
        if result:
            return dict(result)
        return None
        
    except Exception as e:
        logger.error(f"Error fetching Atlassian credentials: {e}")
        raise
    finally:
        cursor.close()
        conn.close()
```

**What this layer does:**
- ✅ Executes SQL queries
- ✅ Manages database connections
- ✅ Handles database errors
- ✅ Returns data as Python dictionaries

**Why it's separate:**
- Can switch databases (PostgreSQL → MySQL) easily
- Can add caching layer
- Can optimize queries in one place
- Prevents SQL injection

---

## Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ USER ACTION: Opens "My Jira Issues" page                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (React)                                                 │
│                                                                   │
│ useEffect(() => {                                                 │
│   fetch('/api/integrations/jira/my-issues?status=In Progress', { │
│     headers: { Authorization: `Bearer ${token}` }                │
│   })                                                              │
│ }, [])                                                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ HTTP GET Request
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ ROUTER LAYER (routers/integrations.py)                          │
│                                                                   │
│ @router.get("/jira/my-issues")                                   │
│ async def get_my_jira_issues(                                    │
│     status: Optional[str] = None,                                │
│     current_user: dict = Depends(get_current_user)               │
│ ):                                                                │
│     # 1. Verify JWT token → Extract user_id                      │
│     # 2. Parse query params → status = "In Progress"             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Call: get_user_atlassian_credentials(user_id)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ DATA LAYER (db_helper.py)                                        │
│                                                                   │
│ def get_user_atlassian_credentials(user_id):                     │
│     cursor.execute("""                                            │
│         SELECT atlassian_domain, atlassian_email, ...            │
│         FROM users WHERE id = %s                                 │
│     """, (user_id,))                                              │
│     return cursor.fetchone()                                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Returns: { domain: "...", email: "...", api_token: "..." }
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ ROUTER LAYER (continued)                                         │
│                                                                   │
│     credentials = get_user_atlassian_credentials(user_id)        │
│     jira_service = JiraService(credentials['domain'], ...)       │
│     issues = jira_service.get_issues_assigned_to_user(           │
│         assignee_email=credentials['email'],                     │
│         status_filter="In Progress"                              │
│     )                                                             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Call: jira_service.get_issues_assigned_to_user(...)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ SERVICE LAYER (services/jira_service.py)                         │
│                                                                   │
│ def get_issues_assigned_to_user(self, assignee_email, status):   │
│     jql = f'assignee = "{assignee_email}" AND status = "{status}"'│
│     response = requests.get(                                      │
│         f"{self.base_url}/rest/api/3/search",                    │
│         params={"jql": jql},                                      │
│         auth=self.auth                                            │
│     )                                                             │
│     return response.json()['issues']                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ HTTP Request to Jira API
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ EXTERNAL API (Jira Cloud)                                        │
│                                                                   │
│ GET https://mycompany.atlassian.net/rest/api/3/search            │
│ ?jql=assignee="user@example.com" AND status="In Progress"        │
│                                                                   │
│ Response: {                                                       │
│   "issues": [                                                     │
│     {                                                             │
│       "id": "10001",                                              │
│       "key": "PROJ-123",                                          │
│       "fields": {                                                 │
│         "summary": "Fix login bug",                               │
│         "status": {"name": "In Progress"},                        │
│         "priority": {"name": "High"}                              │
│       }                                                            │
│     }                                                             │
│   ]                                                               │
│ }                                                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Returns: List of issues
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ ROUTER LAYER (final transformation)                             │
│                                                                   │
│     formatted_issues = [                                          │
│         {                                                         │
│             "id": issue["id"],                                    │
│             "key": issue["key"],                                  │
│             "summary": issue["fields"]["summary"],                │
│             "status": issue["fields"]["status"]["name"]           │
│         }                                                         │
│         for issue in issues                                       │
│     ]                                                             │
│                                                                   │
│     return {"issues": formatted_issues, "total": 1}               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ HTTP 200 OK Response
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND (React)                                                 │
│                                                                   │
│ const { data } = await response.json()                           │
│ // data = { issues: [...], total: 1 }                            │
│                                                                   │
│ setIssues(data.issues)  // Update UI                             │
│                                                                   │
│ // Display:                                                       │
│ // ┌─────────────────────────────────┐                           │
│ // │ My Jira Issues                  │                           │
│ // ├─────────────────────────────────┤                           │
│ // │ PROJ-123: Fix login bug         │                           │
│ // │ Status: In Progress             │                           │
│ // │ Priority: High                  │                           │
│ // └─────────────────────────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Principles Demonstrated

### 1. **Single Responsibility Principle**
Each layer has ONE job:
- Router: HTTP handling
- Service: Jira API logic
- Data: Database queries

### 2. **Dependency Inversion**
Router depends on abstractions (Service interface), not concrete implementations

### 3. **Open/Closed Principle**
Can add new features without modifying existing code:
```python
# Add new service without changing router
class ConfluenceService:
    def get_pages_by_user(self, user_email):
        ...

# Use it in router
@router.get("/confluence/my-pages")
async def get_my_confluence_pages(...):
    confluence_service = ConfluenceService(...)
    pages = confluence_service.get_pages_by_user(...)
```

### 4. **Testability**
Each layer can be tested independently:

```python
# Test Router (integration test)
def test_get_my_issues_endpoint():
    response = client.get(
        "/api/integrations/jira/my-issues?status=In Progress",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert "issues" in response.json()

# Test Service (unit test with mocked requests)
@mock.patch('requests.get')
def test_get_issues_assigned_to_user(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "issues": [{"id": "1", "key": "PROJ-123"}]
    }
    
    service = JiraService("domain", "email", "token")
    issues = service.get_issues_assigned_to_user("user@example.com")
    
    assert len(issues) == 1
    assert issues[0]["key"] == "PROJ-123"

# Test Data Layer (unit test with mocked database)
@mock.patch('db_helper.get_db_connection')
def test_get_user_atlassian_credentials(mock_conn):
    mock_cursor = mock_conn.return_value.cursor.return_value
    mock_cursor.fetchone.return_value = {
        "atlassian_domain": "test.atlassian.net",
        "atlassian_email": "test@example.com"
    }
    
    result = get_user_atlassian_credentials("user123")
    
    assert result["atlassian_domain"] == "test.atlassian.net"
```

---

## Common Mistakes to Avoid

### ❌ **Mistake 1: Mixing Layers**
```python
# BAD: Database logic in router
@router.get("/jira/my-issues")
async def get_my_issues(...):
    conn = psycopg2.connect(...)  # ❌ Database logic in router
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    ...
```

### ✅ **Correct: Separate Layers**
```python
# GOOD: Use data layer
@router.get("/jira/my-issues")
async def get_my_issues(...):
    credentials = get_user_atlassian_credentials(user_id)  # ✅ Data layer
    ...
```

---

### ❌ **Mistake 2: Business Logic in Router**
```python
# BAD: Jira API logic in router
@router.get("/jira/my-issues")
async def get_my_issues(...):
    response = requests.get(  # ❌ External API logic in router
        "https://mycompany.atlassian.net/rest/api/3/search",
        auth=HTTPBasicAuth(email, token)
    )
    ...
```

### ✅ **Correct: Use Service Layer**
```python
# GOOD: Use service layer
@router.get("/jira/my-issues")
async def get_my_issues(...):
    jira_service = JiraService(domain, email, token)
    issues = jira_service.get_issues_assigned_to_user(email)  # ✅ Service layer
    ...
```

---

### ❌ **Mistake 3: Returning Raw External Data**
```python
# BAD: Return raw Jira response to frontend
@router.get("/jira/my-issues")
async def get_my_issues(...):
    issues = jira_service.get_issues_assigned_to_user(email)
    return issues  # ❌ Raw Jira format (frontend depends on Jira structure)
```

### ✅ **Correct: Transform Data**
```python
# GOOD: Transform to frontend-friendly format
@router.get("/jira/my-issues")
async def get_my_issues(...):
    issues = jira_service.get_issues_assigned_to_user(email)
    
    # ✅ Transform to consistent format
    return {
        "issues": [
            {
                "id": issue["id"],
                "title": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"]
            }
            for issue in issues
        ]
    }
```

---

## Practice Exercise

Try implementing this feature yourself:

**Feature**: "Get Confluence pages created by current user"

**Requirements**:
1. Create endpoint: `GET /api/integrations/confluence/my-pages`
2. Add method to `ConfluenceService`: `get_pages_by_author(author_email)`
3. Use existing `get_user_atlassian_credentials()` from data layer
4. Return formatted response: `{ "pages": [...], "total": N }`

**Hint**: Follow the same pattern as the Jira example above!

---

This is how professional backend systems are built! 🚀
