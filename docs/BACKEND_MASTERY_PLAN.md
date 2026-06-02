# 🚀 2-Month Backend & Cloud Mastery Plan
*Targeting: Senior Backend Engineer / AI Engineer Roles*

This plan is tailored to your current stack (**FastAPI, PostgreSQL, AWS, AgentCore**) using your own codebase as the primary learning material.

---

## 📅 Month 1: The Foundation (Backend & Database)

### Week 1: Professional API Development (FastAPI)
**Goal:** Move from "it runs" to "it scales".
*   **Concepts to Learn (Basics you use):**
    *   **HTTP Methods:** GET, POST, DELETE. (See `projects_api.py` decorators like `@router.get`).
    *   **Decorators:** How `@app.get` works.
    *   **JSON Structures:** Parsing dictionaries (currently done manually).
*   **Concepts to Master (Improvements):**
    *   **Pydantic Models:** Stop parsing JSON manually! Define schemas/shapes for your data.
    *   **Dependency Injection:** How `Depends()` works under the hood.
    *   **Middleware:** Creating interceptors for logging or auth (you have `log_requests` in `app.py`, learn how it works).
*   **Project Task (Hands-on):**
    *   Create a file `schemas.py`.
    *   Refactor `projects_api.py` to use Pydantic models instead of reading raw JSON dictionaries.
    *   *Why?* This prevents 90% of bugs where a frontend sends missing fields.

#### 🗓️ Week 1 Daily Schedule
- **Day 1: HTTP & REST Principles (The "What" and "Why")**
  - **Study:** HTTP Verbs (GET vs POST), Status Codes (200, 400, 500), and Path vs Query Params.
  - **Action:** Audit `projects_api.py` to see if your status codes define success/failure correctly.
- **Day 2: Data Validation with Pydantic (The "How" of Safety)**
  - **Study:** Pydantic `BaseModel`, type hinting, and auto-validation.
  - **Action:** Create `schemas.py` and define `ProjectCreate` and `ProjectResponse` models.
- **Day 3: The Code Refactor (Hands-on Implementation)**
  - **Study:** Request bodies in FastAPI.
  - **Action:** **(CRITICAL TASK)** Replace `data = await request.json()` in `projects_api.py` with your new Pydantic models.
- **Day 4: Dependency Injection & Clean Code**
  - **Study:** Why Dependency Injection (`Depends()`) is better than global variables.
  - **Action:** Ensure your DB connection logic uses `Depends()` if it doesn't already.
- **Day 5: Middleware & Observability**
  - **Study:** How Middleware "wraps" a request.
  - **Action:** Enhance `log_requests` in `app.py` to print the *execution time* of each request.

### Week 2: Database Engineering (PostgreSQL)
**Goal:** Solve the connection bottleneck in `db_helper.py`.
*   **Concepts to Learn (Basics you use):**
    *   **SQL Syntax:** `SELECT`, `INSERT`, `UPDATE`. (See `db_helper.py` SQL strings).
    *   **Drivers:** `psycopg2` is the driver. Understand what a "cursor" is.
*   **Concepts to Master (Improvements):**
    *   **Connection Lifecycle:** Why opening a connection is slow (TCP handshake + Auth).
    *   **Pooling:** Keeping 5-10 connections open and "borrowing" them.
    *   **ACID Transactions:** `commit()` vs `rollback()` (You use this in `create_project`, deeply understand *why*).
*   **Project Task (Hands-on):**
    *   Refactor `db_helper.py` to use `psycopg2.pool.SimpleConnectionPool`.
    *   Create a global `DB_POOL` on startup in `app.py`.
    *   Update `get_db_connection()` to `get_db_from_pool()`.

### Week 3: Modern ORMs & Migrations
**Goal:** Stop writing string-based SQL queries.
*   **Concepts to Learn (Basics you use):**
    *   **Schema Design:** Tables, Columns, Primary Keys (See `create_tables.py`).
*   **Concepts to Master (Improvements):**
    *   **ORM (Object Relational Mapper):** Mapping a Python Class `User` to a SQL Table `users`.
    *   **Migrations (Alembic):** Tracking changes to your DB schema (e.g., adding a column) via code, not manual SQL commands.
*   **Project Task (Hands-on):**
    *   Install `SQLAlchemy`.
    *   Create a `models.py` file attempting to define your `projects` table as a python class.
    *   *Note:* You don't have to rewrite the whole app yet, just map one table to understand the power.

### Week 4: Asynchronous Programming (AsyncIO)
**Goal:** Fix the 5-minute timeout in `app.py`.
*   **Concepts to Learn (Basics you use):**
    *   **Synchronous Code:** Line 1 runs, then Line 2 runs.
    *   **Timeouts:** `read_timeout=300` in `app.py`.
*   **Concepts to Master (Improvements):**
    *   **Event Loop:** How Python handles 1000 requests with 1 thread.
    *   **Blocking vs Non-Blocking:** Why `time.sleep()` kills a server but `await asyncio.sleep()` doesn't.
    *   **Background Tasks:** delegating heavy work (like AI generation) to the background.
*   **Project Task (Hands-on):**
    *   Use FastAPI's `BackgroundTasks` to trigger a dummy log function after returning a response.

---

## 📅 Month 2: The Cloud & AI (AWS & Agents)

### Week 5: Serverless Architecture (AWS Lambda)
**Goal:** Master the "Compute" behind your agents.
*   **Concepts to Learn (Basics you use):**
    *   **Functions:** `def lambda_handler(event, context):`.
    *   **Zip Deployments:** `create_lambda_zip.py`.
*   **Concepts to Master (Improvements):**
    *   **Cold Starts:** Why the first request is slow and how to fix it (SnapStart or Provisioned Concurrency).
    *   **Event Objects:** Understanding the JSON event that AWS sends to your function (look at `lambda_brd_generator.py`).
    *   **Layers:** Managing dependencies (like `pandas` or `numpy`) in Lambda without uploading huge zip files.
*   **Project Task (Hands-on):**
    *   Create a simple "Hello World" Lambda in the AWS Console.
    *   Trigger it via a localized test event.
    *   Read your `create_lambda_zip.py` script to understand how your code gets to the cloud.

### Week 6: Storage & Events (AWS S3)
**Goal:** Beyond just "file storage".
*   **Concepts to Learn (Basics you use):**
    *   **Buckets & Keys:** Simple file storage.
    *   **Boto3:** The AWS SDK you use in `app.py`.
*   **Concepts to Master (Improvements):**
    *   **Presigned URLs:** Letting the frontend upload directly to S3 (bypassing your backend server entirely—huge performance win).
    *   **S3 Event Notifications:** Triggering a Lambda immediately when a file is uploaded (e.g., "New Transcript Uploaded" -> "Trigger Analysis Agent").
*   **Project Task (Hands-on):**
    *   Update your `upload_transcript` flow. Instead of user -> backend -> S3, try generating a Presigned URL.

### Week 7: Agentic AI Systems (AgentCore & Bedrock)
**Goal:** Deep dive into how your `my_agent.py` actually thinks.
*   **Concepts to Learn (Basics you use):**
    *   **Prompts:** Instructions to the AI.
    *   **Tools:** Functions the AI can call (`generate_brd`).
*   **Concepts to Master (Improvements):**
    *   **The ReAct Pattern:** Reasoning + Acting. How the LLM decides *which* tool to call.
    *   **OpenAPI Schemas:** How Bedrock understands your tools (look at `lambda_function` definitions).
    *   **Prompt Engineering for Agents:** Giving the agent a "Persona" and "Principles" (like you have in `bmad_agent_config.json`).
*   **Project Task (Hands-on):**
    *   Modify `my_agent.py` (or your Bedrock configuration) to add a simple new tool, like "get_current_time", and see if the agent can use it.

### Week 8: Production Quality (Security & CI/CD)
**Goal:** Preparing for the real world.
*   **Concepts to Learn (Basics you use):**
    *   **Environment Variables:** `.env` files.
    *   **Logging:** `print()` statements (currently used).
*   **Concepts to Master (Improvements):**
    *   **IAM (Identity Access Management):** The most critical AWS skill. Why does your Lambda need `s3:GetObject`? Least Privilege Principle.
    *   **CloudWatch:** Reading logs (you do this manually now with `fetch_logs.py`). Learn to set up Alarms (e.g., "If errors > 5, email me").
    *   **CI/CD:** Using GitHub Actions or Azure DevOps (since you use Azure Repos) to deploy automatically when you push code.
*   **Project Task (Hands-on):**
    *   Review the IAM role attached to your `Analyst_agent`. Does it have too many permissions? Try to restrict it.

---

## 📚 Recommended Resources
1.  **FastAPI Documentation:** Their tutorial is world-class.
2.  **AWS Skill Builder (Free):** "Serverless Learning Plan".
3.  **"Cosmic Python" (Book):** For architecture patterns.
4.  **Hussein Nasser (YouTube):** For Backend Engineering database concepts.
