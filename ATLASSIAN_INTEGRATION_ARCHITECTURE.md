can you # Atlassian Integration Architecture Guide

## 🎯 Overview

This document explains the **3-Layer Architecture** pattern used in the Atlassian integration feature. Understanding this pattern is crucial for building maintainable, testable, and scalable backend systems.

---

## 📊 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                 │
│  (React/TypeScript - Makes HTTP requests to backend APIs)       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ HTTP Request
                             │ POST /api/integrations/atlassian/link
                             │ GET  /api/integrations/jira/projects
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: ROUTER LAYER                         │
│                  (routers/integrations.py)                       │
│                                                                   │
│  Responsibilities:                                                │
│  ✓ Define API endpoints (routes)                                 │
│  ✓ Parse HTTP requests (query params, body, headers)             │
│  ✓ Validate request data (Pydantic models)                       │
│  ✓ Handle authentication (JWT token verification)                │
│  ✓ Orchestrate calls to Service Layer                            │
│  ✓ Format HTTP responses (JSON)                                  │
│  ✓ Handle HTTP errors (404, 401, 500, etc.)                      │
│                                                                   │
│  Example:                                                         │
│  @router.post("/atlassian/link")                                 │
│  async def link_atlassian_account(...)                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Calls
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   LAYER 2: SERVICE LAYER                         │
│         (services/jira_service.py, confluence_service.py)        │
│                                                                   │
│  Responsibilities:                                                │
│  ✓ Business logic for external API interactions                  │
│  ✓ Encapsulate third-party API calls (Jira, Confluence)          │
│  ✓ Handle API authentication (Basic Auth, OAuth)                 │
│  ✓ Transform external data into internal format                  │
│  ✓ Error handling for external services                          │
│  ✓ Retry logic, timeouts, connection pooling                     │
│                                                                   │
│  Example:                                                         │
│  class JiraService:                                               │
│      def get_projects(self) -> List[Dict]                        │
│      def test_connection(self) -> tuple[bool, str]               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Makes HTTP requests to
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  EXTERNAL APIS (Atlassian)                       │
│         https://yourcompany.atlassian.net/rest/api/3/...         │
└─────────────────────────────────────────────────────────────────┘

                             ▲
                             │
                             │ Reads/Writes
                             │
┌─────────────────────────────────────────────────────────────────┐
│                   LAYER 3: DATA LAYER                            │
│                     (db_helper.py)                               │
│                                                                   │
│  Responsibilities:                                                │
│  ✓ Database queries (SELECT, INSERT, UPDATE, DELETE)             │
│  ✓ Transaction management (COMMIT, ROLLBACK)                     │
│  ✓ Data validation and sanitization                              │
│  ✓ Connection pooling                                            │
│  ✓ SQL injection prevention                                      │
│                                                                   │
│  Example:                                                         │
│  def update_user_atlassian_credentials(...)                      │
│  def get_user_atlassian_credentials(...)                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ SQL Queries
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DATABASE (PostgreSQL)                       │
│                      Table: users                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔍 Detailed Layer Breakdown

### **Layer 1: Router Layer** (`routers/integrations.py`)

**Purpose**: HTTP interface between frontend and backend

**Key Concepts**:
- **API Endpoints**: Define URL routes that the frontend can call
- **Request Validation**: Use Pydantic models to validate incoming data
- **Authentication**: Verify JWT tokens and extract user information
- **Orchestration**: Coordinate calls between services and database

**Example Flow**:
```python
@router.post("/atlassian/link")
async def link_atlassian_account(
    request: LinkAtlassianRequest,  # ← Validates incoming JSON
    current_user: dict = Depends(get_current_user)  # ← Authenticates user
):
    # Step 1: Validate credentials using Service Layer
    jira_service = JiraService(request.domain, request.email, request.api_token)
    success, error = jira_service.test_connection()
    
    if not success:
        raise HTTPException(status_code=400, detail=error)
    
    # Step 2: Save to database using Data Layer
    update_user_atlassian_credentials(
        user_id=current_user['id'],
        domain=request.domain,
        email=request.email,
        api_token=request.api_token
    )
    
    # Step 3: Return HTTP response
    return {"status": "success", "message": "Account linked"}
```

**Why separate this layer?**
- ✅ **Testability**: Can test HTTP logic separately from business logic
- ✅ **Reusability**: Same service can be used by multiple endpoints
- ✅ **Security**: Centralized authentication and authorization
- ✅ **API Versioning**: Easy to create `/v1/` and `/v2/` routes

---

### **Layer 2: Service Layer** (`services/jira_service.py`, `services/confluence_service.py`)

**Purpose**: Encapsulate business logic and external API interactions

**Key Concepts**:
- **Separation of Concerns**: External API logic is isolated from HTTP logic
- **Reusability**: Services can be used by multiple routers
- **Testability**: Can mock external APIs in unit tests
- **Error Handling**: Centralized error handling for third-party APIs

**Example**:
```python
class JiraService:
    def __init__(self, domain: str, email: str, api_token: str):
        self.base_url = f"https://{domain}"
        self.auth = HTTPBasicAuth(email, api_token)
    
    def test_connection(self) -> tuple[bool, Optional[str]]:
        """Test if credentials are valid"""
        try:
            url = f"{self.base_url}/rest/api/3/myself"
            response = requests.get(url, auth=self.auth, timeout=10)
            
            if response.status_code == 200:
                return (True, None)
            elif response.status_code == 401:
                return (False, "Invalid credentials")
            else:
                return (False, f"Error: {response.status_code}")
        except Exception as e:
            return (False, str(e))
    
    def get_projects(self) -> List[Dict]:
        """Fetch all Jira projects"""
        url = f"{self.base_url}/rest/api/3/project"
        response = requests.get(url, auth=self.auth)
        response.raise_for_status()
        return response.json()
```

**Why separate this layer?**
- ✅ **Single Responsibility**: Each service handles ONE external system
- ✅ **Testability**: Can mock `requests` library in tests
- ✅ **Maintainability**: Changes to Jira API only affect `JiraService`
- ✅ **Reusability**: Can use `JiraService` in background jobs, CLI tools, etc.

---

### **Layer 3: Data Layer** (`db_helper.py`)

**Purpose**: All database interactions happen here

**Key Concepts**:
- **Data Access Abstraction**: Hide SQL complexity from business logic
- **Transaction Management**: Handle COMMIT/ROLLBACK
- **Connection Pooling**: Reuse database connections efficiently
- **SQL Injection Prevention**: Use parameterized queries

**Example**:
```python
def update_user_atlassian_credentials(
    user_id: str,
    domain: str,
    email: str,
    api_token: str
):
    """Update user's Atlassian credentials in database"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET atlassian_domain = %s,
                atlassian_email = %s,
                atlassian_api_token = %s,
                atlassian_linked_at = NOW()
            WHERE id = %s
        """, (domain, email, api_token, user_id))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
```

**Why separate this layer?**
- ✅ **Database Independence**: Can switch from PostgreSQL to MySQL easily
- ✅ **Testability**: Can mock database calls in tests
- ✅ **Performance**: Centralized query optimization
- ✅ **Security**: Prevents SQL injection attacks

---

## 🔄 Complete Request Flow Example

Let's trace a **real request** through all layers:

### **User Action**: Click "Link Atlassian Account" button

```
1. FRONTEND (React)
   ├─ User fills form: domain, email, api_token
   ├─ Clicks "Submit"
   └─ Sends: POST /api/integrations/atlassian/link
      Headers: { Authorization: "Bearer <JWT_TOKEN>" }
      Body: { "domain": "mycompany.atlassian.net", "email": "user@example.com", "api_token": "ATATT..." }

2. ROUTER LAYER (routers/integrations.py)
   ├─ FastAPI receives request
   ├─ Validates JWT token → Extracts user_id
   ├─ Validates request body using Pydantic → LinkAtlassianRequest
   ├─ Calls: get_current_user() → Returns user object from DB
   └─ Enters: link_atlassian_account() function

3. SERVICE LAYER (services/jira_service.py)
   ├─ Router creates: JiraService(domain, email, api_token)
   ├─ Router calls: jira_service.test_connection()
   ├─ Service makes HTTP request to: https://mycompany.atlassian.net/rest/api/3/myself
   ├─ Atlassian API responds: 200 OK (credentials valid)
   └─ Service returns: (True, None)

4. DATA LAYER (db_helper.py)
   ├─ Router calls: update_user_atlassian_credentials(user_id, domain, email, api_token)
   ├─ Opens database connection
   ├─ Executes SQL: UPDATE users SET atlassian_domain = ..., atlassian_email = ..., atlassian_api_token = ...
   ├─ Commits transaction
   └─ Closes connection

5. ROUTER LAYER (Response)
   ├─ Returns HTTP 200 OK
   └─ Body: { "status": "success", "message": "Atlassian account linked successfully" }

6. FRONTEND (React)
   ├─ Receives response
   ├─ Shows success message
   └─ Updates UI to show "Account Linked ✓"
```

---

## 🎓 Why This Architecture Matters

### **1. Separation of Concerns**
Each layer has ONE job:
- **Router**: Handle HTTP
- **Service**: Handle business logic
- **Data**: Handle database

### **2. Testability**
You can test each layer independently:

```python
# Test Router Layer (integration test)
def test_link_atlassian_endpoint():
    response = client.post("/api/integrations/atlassian/link", 
                           json={"domain": "...", "email": "...", "api_token": "..."},
                           headers={"Authorization": "Bearer <token>"})
    assert response.status_code == 200

# Test Service Layer (unit test with mocked requests)
@mock.patch('requests.get')
def test_jira_service_connection(mock_get):
    mock_get.return_value.status_code = 200
    service = JiraService("domain", "email", "token")
    success, error = service.test_connection()
    assert success is True

# Test Data Layer (unit test with mocked database)
@mock.patch('db_helper.get_db_connection')
def test_update_credentials(mock_conn):
    update_user_atlassian_credentials("user123", "domain", "email", "token")
    mock_conn.cursor().execute.assert_called_once()
```

### **3. Maintainability**
- If Jira API changes → Only update `JiraService`
- If database schema changes → Only update `db_helper.py`
- If API response format changes → Only update router

### **4. Reusability**
```python
# Use JiraService in multiple places
# ✓ In API endpoint
# ✓ In background job
# ✓ In CLI tool
# ✓ In admin dashboard

from services.jira_service import JiraService

# Background job
def sync_jira_issues_job():
    credentials = get_user_atlassian_credentials(user_id)
    jira = JiraService(credentials['domain'], credentials['email'], credentials['api_token'])
    projects = jira.get_projects()
    # ... sync logic

# CLI tool
def cli_test_jira_connection(domain, email, token):
    jira = JiraService(domain, email, token)
    success, error = jira.test_connection()
    print(f"Connection: {'✓' if success else '✗'}")
```

### **5. Scalability**
- Can add caching layer between router and service
- Can add message queue for async processing
- Can add API gateway for rate limiting
- Can horizontally scale each layer independently

---

## 📚 Common Patterns You'll See

### **1. Dependency Injection**
```python
# Router doesn't create database connection itself
# It receives it from FastAPI's dependency system
async def link_atlassian_account(
    request: LinkAtlassianRequest,
    current_user: dict = Depends(get_current_user)  # ← Injected
):
    ...
```

### **2. Error Handling Hierarchy**
```python
# Service Layer: Raises specific exceptions
class JiraService:
    def get_projects(self):
        if response.status_code == 401:
            raise JiraAuthenticationError("Invalid credentials")
        if response.status_code == 404:
            raise JiraNotFoundError("Project not found")

# Router Layer: Converts to HTTP errors
@router.get("/jira/projects")
async def list_projects(...):
    try:
        projects = jira_service.get_projects()
        return {"projects": projects}
    except JiraAuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except JiraNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

### **3. Data Transformation**
```python
# Service Layer: Returns raw API data
def get_projects(self) -> List[Dict]:
    response = requests.get(...)
    return response.json()  # Raw Jira format

# Router Layer: Transforms for frontend
@router.get("/jira/projects")
async def list_projects(...):
    raw_projects = jira_service.get_projects()
    
    # Transform to frontend-friendly format
    return {
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "key": p["key"],
                "icon": p.get("avatarUrls", {}).get("48x48")
            }
            for p in raw_projects
        ]
    }
```

---

## 🚀 Next Steps to Become a Better Backend Engineer

### **1. Practice This Pattern**
Every new feature should follow this structure:
```
routers/
  ├─ feature_name.py      # HTTP endpoints
services/
  ├─ feature_service.py   # Business logic
db_helper.py              # Database queries
```

### **2. Learn Design Patterns**
- **Repository Pattern**: Abstract data access
- **Factory Pattern**: Create service instances
- **Strategy Pattern**: Swap implementations (e.g., different payment providers)
- **Decorator Pattern**: Add logging, caching, retry logic

### **3. Study Transaction Management**
```python
# Good: Atomic operation
def transfer_money(from_user, to_user, amount):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET balance = balance - %s WHERE user_id = %s", (amount, from_user))
        cursor.execute("UPDATE accounts SET balance = balance + %s WHERE user_id = %s", (amount, to_user))
        conn.commit()  # Both succeed or both fail
    except:
        conn.rollback()
        raise
```

### **4. Learn Async/Await**
```python
# Synchronous (blocks)
def get_user_data(user_id):
    user = db.query("SELECT * FROM users WHERE id = %s", user_id)
    projects = jira_service.get_projects()  # Waits for Jira
    return {"user": user, "projects": projects}

# Asynchronous (concurrent)
async def get_user_data(user_id):
    user_task = asyncio.create_task(db.query_async("SELECT * FROM users WHERE id = %s", user_id))
    projects_task = asyncio.create_task(jira_service.get_projects_async())
    
    user, projects = await asyncio.gather(user_task, projects_task)  # Runs in parallel
    return {"user": user, "projects": projects}
```

### **5. Add Observability**
```python
import logging
import time

logger = logging.getLogger(__name__)

class JiraService:
    def get_projects(self):
        start_time = time.time()
        logger.info("Fetching Jira projects")
        
        try:
            response = requests.get(...)
            projects = response.json()
            
            duration = time.time() - start_time
            logger.info(f"Fetched {len(projects)} projects in {duration:.2f}s")
            
            return projects
        except Exception as e:
            logger.error(f"Failed to fetch projects: {e}")
            raise
```

---

## 📖 Recommended Reading

1. **Clean Architecture** by Robert C. Martin
2. **Domain-Driven Design** by Eric Evans
3. **Designing Data-Intensive Applications** by Martin Kleppmann
4. **FastAPI Documentation**: https://fastapi.tiangolo.com/
5. **PostgreSQL Performance**: https://www.postgresql.org/docs/current/performance-tips.html

---

## 💡 Key Takeaways

✅ **Router Layer** = HTTP interface (routes, validation, auth)  
✅ **Service Layer** = Business logic (external APIs, transformations)  
✅ **Data Layer** = Database operations (queries, transactions)  

✅ **Each layer has ONE responsibility**  
✅ **Layers communicate through well-defined interfaces**  
✅ **Changes in one layer don't affect others**  

This is the foundation of professional backend development! 🚀
