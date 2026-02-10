# Backend Architecture Guide - Layered Design & Database Patterns

This guide explains the **architectural layers** and **database transaction patterns** used in your application, so you can apply these principles to future projects.

---

## 🏗️ Architecture Overview

Your application follows a **3-Layer Architecture**:

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND                              │
│  (React Components + API Service Layer)                     │
│  - Handles UI/UX                                            │
│  - Makes HTTP requests                                       │
│  - Manages local state                                       │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP (REST API)
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                    BACKEND (FastAPI)                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  LAYER 1: API Routes (routers/*.py)                 │   │
│  │  - Validates requests                                │   │
│  │  - Handles authentication                            │   │
│  │  - Orchestrates business logic                       │   │
│  │  - Calls multiple DB helpers if needed               │   │
│  │  - Formats responses                                 │   │
│  └──────────────────┬──────────────────────────────────┘   │
│                     │                                        │
│                     ▼                                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  LAYER 2: Database Helpers (db_helper.py)           │   │
│  │  - Single-purpose functions                          │   │
│  │  - Each does ONE database operation                  │   │
│  │  - Reusable across routes                            │   │
│  │  - Handles connection pooling                        │   │
│  │  - Manages transactions (commit/rollback)            │   │
│  └──────────────────┬──────────────────────────────────┘   │
│                     │                                        │
└─────────────────────┼────────────────────────────────────────┘
                      │ SQL Queries
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  DATABASE (PostgreSQL)                       │
│  - Stores data                                              │
│  - Enforces constraints                                      │
│  - Runs triggers                                             │
│  - Manages transactions                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Design Principles

### **Principle 1: Separation of Concerns**

Each layer has a **single responsibility**:

| Layer | Responsibility | Should NOT Do |
|-------|----------------|---------------|
| **Frontend** | UI, user input, display data | Direct database access |
| **API Routes** | Request validation, orchestration, authorization | Write SQL queries |
| **DB Helpers** | Database operations | Business logic, authorization |
| **Database** | Data storage, integrity | Application logic |

---

### **Principle 2: Single Responsibility (DB Helpers)**

Each DB helper function does **ONE thing**:

```python
# ✅ GOOD: Single responsibility
def get_project(project_id: str) -> Optional[Dict]:
    """Get ONE project by ID"""
    # Does ONE SELECT query
    
def get_user_projects(user_id: str) -> List[Dict]:
    """Get ALL projects for a user"""
    # Does ONE SELECT query
    
def delete_project(project_id: str) -> bool:
    """Delete ONE project"""
    # Does ONE DELETE/UPDATE query
```

```python
# ❌ BAD: Multiple responsibilities
def get_project_with_sessions_and_user(project_id: str):
    """Get project AND its sessions AND user info"""
    # Does 3 SELECT queries - TOO MUCH!
    # This should be split into 3 separate functions
```

**Why?**
- **Reusability**: You can call `get_project()` from different routes
- **Testability**: Easy to test one function = one query
- **Maintainability**: Changes to one query don't affect others

---

## 🔄 Transaction Patterns

### **Pattern 1: Single Operation (Most Common)**

**Example**: Get all projects

```
Frontend Request
    ↓
API Route: get_projects()
    ↓
DB Helper: get_user_projects(user_id)
    ↓
Database: SELECT ... FROM projects WHERE user_id = ?
    ↓
Return: List of projects
```

**Code Flow**:
```python
# API Route (routers/projects.py)
@router.get("/")
async def get_projects(current_user):
    projects = get_user_projects(current_user["id"])  # 1 DB call
    return projects

# DB Helper (db_helper.py)
def get_user_projects(user_id):
    conn = get_db_connection()
    try:
        cursor.execute("SELECT ... FROM projects WHERE user_id = %s", (user_id,))
        return cursor.fetchall()
    finally:
        release_db_connection(conn)
```

**Database Operations**: 1 SELECT query

---

### **Pattern 2: Multiple Sequential Operations**

**Example**: Update project (verify ownership → update)

```
Frontend Request
    ↓
API Route: update_project_by_id()
    ├─→ DB Helper: get_project(project_id)        [Operation 1: Verify exists]
    │       ↓
    │   Database: SELECT ... WHERE id = ?
    │       ↓
    ├─→ Check: Does user own this project?        [Business Logic]
    │       ↓
    └─→ DB Helper: update_project(project_id, ...)  [Operation 2: Update]
            ↓
        Database: UPDATE projects SET ... WHERE id = ?
            ↓
Return: Updated project
```

**Code Flow**:
```python
# API Route (routers/projects.py:162)
@router.patch("/{project_id}")
async def update_project_by_id(project_id, project_data, current_user):
    # Operation 1: Verify project exists and user owns it
    existing_project = get_project(project_id)  # DB call #1
    if not existing_project:
        raise HTTPException(status_code=404)
    
    if existing_project["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403)  # Not authorized
    
    # Operation 2: Update the project
    updated_project = update_project(project_id, ...)  # DB call #2
    return updated_project
```

**Database Operations**: 
1. SELECT (verify ownership)
2. UPDATE (modify project)

**Why Two Operations?**
- **Security**: Must verify user owns the project before allowing update
- **Error Handling**: Return 404 if project doesn't exist, 403 if not authorized
- **Separation**: Verification logic in route, update logic in helper

---

### **Pattern 3: Create with Auto-Update (UPSERT)**

**Example**: User login (create user or update last_login)

```
Frontend Login
    ↓
API Route: get_current_user()
    ↓
DB Helper: create_or_update_user(user_id, email, name)
    ↓
Database: INSERT ... ON CONFLICT DO UPDATE
    ↓
Return: User object
```

**Code Flow**:
```python
# API Route (routers/projects.py:67)
async def get_current_user(token_data):
    user_id = token_data["oid"]
    email = token_data["email"]
    name = token_data["name"]
    
    # Single DB call handles both create AND update
    user = create_or_update_user(user_id, email, name)  # 1 DB call
    return user

# DB Helper (db_helper.py:66)
def create_or_update_user(user_id, email, name):
    conn = get_db_connection()
    try:
        cursor.execute("""
            INSERT INTO users (id, email, name, last_login)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE
            SET last_login = CURRENT_TIMESTAMP,
                email = EXCLUDED.email,
                name = COALESCE(EXCLUDED.name, users.name)
            RETURNING *
        """, (user_id, email, name))
        
        user = cursor.fetchone()
        conn.commit()
        return user
    except:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)
```

**Database Operations**: 1 UPSERT query (INSERT or UPDATE)

**Why UPSERT?**
- **Efficiency**: 1 query instead of "SELECT → if exists UPDATE else INSERT"
- **Atomic**: No race condition (two users logging in simultaneously)
- **Simpler**: Less code, less error handling

---

### **Pattern 4: Multiple Operations in Transaction**

**Example**: Create session (verify project exists → create session)

```
Frontend Request
    ↓
API Route: create_new_session()
    ├─→ DB Helper: get_project(project_id)         [Operation 1: Verify project exists]
    │       ↓
    │   Database: SELECT ... WHERE id = ?
    │       ↓
    ├─→ Check: Does user own this project?         [Business Logic]
    │       ↓
    └─→ DB Helper: create_session(...)             [Operation 2: Create session]
            ↓
        Database: INSERT INTO analyst_sessions ...
            ↓
Return: New session
```

**Code Flow**:
```python
# API Route (routers/sessions.py:94)
@router.post("/")
async def create_new_session(session_data, current_user):
    # Operation 1: Verify user owns the project
    project = get_project(session_data.project_id)  # DB call #1
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if project["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403)
    
    # Operation 2: Create session
    session = create_session(...)  # DB call #2
    return session
```

**Database Operations**:
1. SELECT (verify project exists and user owns it)
2. INSERT (create new session)

**Why Separate Transactions?**
- Each DB helper manages its own transaction
- If verification fails, no session is created
- If creation fails, verification is already committed (doesn't matter)

---

## 📊 Real-World Examples from Your App

### **Example 1: Delete Project** (Most Complex)

**API Route**: `DELETE /api/projects/{project_id}`

**Operations**:
```python
@router.delete("/{project_id}")
async def delete_project_by_id(project_id, hard_delete, current_user):
    # ┌─────────────────────────────────────────────────┐
    # │ Operation 1: Verify project exists              │
    # └─────────────────────────────────────────────────┘
    existing_project = get_project(project_id)  # DB call #1: SELECT
    if not existing_project:
        raise HTTPException(status_code=404)
    
    # ┌─────────────────────────────────────────────────┐
    # │ Operation 2: Verify user owns project           │
    # └─────────────────────────────────────────────────┘
    if existing_project["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403)
    
    # ┌─────────────────────────────────────────────────┐
    # │ Operation 3: Delete project                     │
    # └─────────────────────────────────────────────────┘
    result = delete_project(project_id, hard_delete)  # DB call #2: DELETE/UPDATE
    
    return None  # 204 No Content
```

**Total DB Operations**: 2
1. `get_project()` → SELECT
2. `delete_project()` → DELETE or UPDATE

**Why Not Combine?**
- **Reusability**: `get_project()` is used in many routes
- **Clear Errors**: Return specific error codes (404 vs 403)
- **Security**: Verify ownership before deletion

---

### **Example 2: Get Sessions** (Simple)

**API Route**: `GET /api/sessions/?project_id={id}`

**Operations**:
```python
@router.get("/")
async def get_sessions(project_id, current_user):
    # ┌─────────────────────────────────────────────────┐
    # │ Operation 1: Verify project exists              │
    # └─────────────────────────────────────────────────┘
    project = get_project(project_id)  # DB call #1: SELECT
    if not project:
        raise HTTPException(status_code=404)
    
    if project["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403)
    
    # ┌─────────────────────────────────────────────────┐
    # │ Operation 2: Get sessions for project           │
    # └─────────────────────────────────────────────────┘
    sessions = get_project_sessions(project_id, current_user["id"])  # DB call #2: SELECT
    
    return sessions
```

**Total DB Operations**: 2
1. `get_project()` → SELECT (verify ownership)
2. `get_project_sessions()` → SELECT (get sessions)

**Could This Be 1 Query?**
Yes, with a JOIN:
```sql
SELECT s.* FROM analyst_sessions s
JOIN projects p ON s.project_id = p.id
WHERE s.project_id = %s AND p.user_id = %s
```

**Why Keep It Separate?**
- **Clarity**: Easier to understand two simple queries
- **Error Messages**: Can return "Project not found" vs "No sessions"
- **Reusability**: `get_project()` used in many places
- **Performance**: Two simple queries often faster than one complex JOIN

---

## 🎓 Design Guidelines for Future Projects

### **When to Use Multiple DB Operations**

✅ **Use Multiple Operations When:**
1. **Security Checks**: Verify ownership before allowing action
2. **Different Error Messages**: "Not found" vs "Not authorized"
3. **Reusable Functions**: Same verification used in multiple routes
4. **Clear Separation**: Verification logic vs action logic

❌ **Avoid Multiple Operations When:**
1. **Can Use JOIN**: Fetching related data in one query
2. **Can Use UPSERT**: Insert or update in one query
3. **Atomic Requirement**: Must succeed/fail together (use transaction)

---

### **How to Structure Your Code**

#### **1. API Route Layer** (routers/*.py)
**Responsibilities**:
- Validate request data (Pydantic models)
- Authenticate user (`Depends(get_current_user)`)
- **Orchestrate** multiple DB helper calls
- Verify authorization (user owns resource)
- Format response
- Handle errors (404, 403, 500)

**Example**:
```python
@router.patch("/{resource_id}")
async def update_resource(resource_id, data, current_user):
    # 1. Verify resource exists
    resource = get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404)
    
    # 2. Verify user owns it
    if resource["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403)
    
    # 3. Update resource
    updated = update_resource(resource_id, data)
    
    # 4. Format and return
    return format_response(updated)
```

---

#### **2. DB Helper Layer** (db_helper.py)
**Responsibilities**:
- Get connection from pool
- Execute **ONE** SQL query
- Handle transaction (commit/rollback)
- Release connection
- Return data

**Example**:
```python
def get_resource(resource_id: str) -> Optional[Dict]:
    """Get a single resource by ID"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM resources WHERE id = %s", (resource_id,))
            return cursor.fetchone()
    finally:
        release_db_connection(conn)

def update_resource(resource_id: str, name: str) -> Dict:
    """Update a resource"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                UPDATE resources SET name = %s WHERE id = %s
                RETURNING *
            """, (name, resource_id))
            resource = cursor.fetchone()
            conn.commit()
            return resource
    except:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)
```

---

## 📈 Performance Considerations

### **Connection Pooling**
```python
# ✅ GOOD: Reuse connections
_db_pool = pool.ThreadedConnectionPool(1, 20, ...)

def get_db_connection():
    return _db_pool.getconn()  # Borrow from pool

def release_db_connection(conn):
    _db_pool.putconn(conn)  # Return to pool
```

**Why?**
- Creating connection: ~100ms
- Reusing connection: ~1ms
- **100x faster!**

---

### **Query Optimization**

```python
# ❌ BAD: N+1 Query Problem
projects = get_user_projects(user_id)  # 1 query
for project in projects:
    sessions = get_project_sessions(project["id"])  # N queries!

# ✅ GOOD: Batch query
projects = get_user_projects(user_id)  # 1 query
project_ids = [p["id"] for p in projects]
sessions = get_sessions_for_projects(project_ids)  # 1 query with IN clause
```

---

## 🔑 Key Takeaways

1. **3-Layer Architecture**: Frontend → API Routes → DB Helpers → Database
2. **Single Responsibility**: Each DB helper does ONE operation
3. **Orchestration in Routes**: API routes call multiple helpers if needed
4. **Security First**: Always verify ownership before actions
5. **Transactions**: Each helper manages its own transaction
6. **Connection Pooling**: Reuse connections for performance
7. **RETURNING Clause**: Get DB-generated data without second query
8. **Clear Errors**: Return specific HTTP codes (404, 403, 500)

---

## 📚 Further Reading

- **SOLID Principles**: Single Responsibility, Dependency Inversion
- **Repository Pattern**: Similar to your DB helpers
- **Service Layer Pattern**: Your API routes act as services
- **Connection Pooling**: PostgreSQL best practices
- **Transaction Isolation**: ACID properties
