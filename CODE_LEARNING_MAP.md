# 🔍 Learning from Your Own Codebase
You asked: *"Can I understand these concepts from my current code?"*

**YES.** Your code is the perfect textbook because it contains the "Before" examples that we need to fix.

Here is a map of the Learning Plan concepts to your specific files.

---

## 🏗️ Month 1: Backend & Database

### 1. Pydantic Models (The "Correct" way to handle data)
*   **See Implementation:** `projects_api.py` (Lines 34-46)
*   **What you did right:** You defined `class ProjectCreate(BaseModel)`. This is Pydantic!
*   **The Lesson:** Compare this to `app.py`, where you might be using `json.loads(request.body)`. Use Pydantic classes (like in `projects_api.py`) everywhere to validate data automatically.

### 2. Database Connection Bottleneck (The Problem)
*   **See The Problem:** `db_helper.py` (Line 18)
    ```python
    def get_db_connection():
        conn = psycopg2.connect(...)  # Opens a NEW connection every time!
        return conn
    ```
*   **The Lesson:** Every time a function like `create_project` (Line 95) is called, it calls `get_db_connection()`, which performs a slow handshake with AWS.
*   **The Fix:** You will learn to replace Line 18 with a `ConnectionPool` so you just "borrow" an already-open connection.

### 3. Authentication & Dependency Injection
*   **See Implementation:** `app.py` (Line 198 `get_current_user`) & `projects_api.py` (Line 65)
    ```python
    async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    ```
*   **The Lesson:** You are already using FastAPI's `Depends`. This is "Dependency Injection". Understanding *how* `token_data` magically appears there is key to being a Senior Engineer.

---

## ☁️ Month 2: The Cloud & AI

### 4. Serverless Architecture (AWS Lambda)
*   **See Implementation:** `lambda_brd_from_history.py` (Line 382)
    ```python
    def lambda_handler(event, context):
    ```
*   **The Lesson:** This is the entry point. AWS calls this function when an event happens. No server is running until this line is hit.
*   **Key Concept:** Notice `_get_bedrock_runtime()` (Line 38). It uses `global` variables to cache the client. This is a "Cold Start optimization" trick you already have!

### 5. Agent Logic (ReAct Pattern)
*   **See Implementation:** `my_agent.py` (Line 406 `invoke`)
*   **The Lesson:** This file proves you are building an *Agent*, not just a chatbot.
    *   Line 511 (`enhanced_message`): This is where you inject "Memory" into the prompt.
    *   Line 576 (`agent(enhanced_message)`): This is where the LLM "thinks" and decides which tool (`generate_brd`, `fetch_brd`) to call.

### 6. Infrastructure & Deployment
*   **See Implementation:** `create_lambda_zip.py`
*   **The Lesson:** This script manually zips your code and sends it to AWS.
*   **The Future:** In the "Production" week, you will learn to replace this python script with **Terraform** or **GitHub Actions** so you don't have to run it manually.

---

## ✅ Summary
You have all the "Pieces of the Puzzle" in your folder right now.
*   **Month 1** is about optimizing `db_helper.py` and `app.py`.
*   **Month 2** is about understanding how `lambda_brd_from_history.py` and `my_agent.py` run in the cloud.

You don't need a new project. You just need to **refactor** this one. That is the best way to learn.
