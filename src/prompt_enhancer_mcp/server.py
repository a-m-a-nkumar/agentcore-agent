import os
import httpx
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("enhance-prompt")

@mcp.prompt()
async def enhance(task: str, project_id: str = None) -> str:
    """Enhance a dev task with context from your project docs"""
    project_id = project_id or os.environ.get("PROJECT_ID")
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    api_key = os.environ.get("API_KEY")

    if not project_id or not api_key:
        return "Error: PROJECT_ID and API_KEY must be set (via env var or argument)."

    print(f"[MCP] Enhancing task: {task} (Project: {project_id})")

    result = ""
    try:
        print("[MCP] Connecting to backend...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"{api_url}/api/orchestration/query-internal",
                headers={"X-API-Key": api_key},
                json={"project_id": project_id, "query": task, "max_chunks": 5, "return_prompt": True}
            ) as r:
                if r.status_code != 200:
                    print(f"[MCP] Error: Backend returned {r.status_code}")
                    return f"Error: Backend returned {r.status_code}"

                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if data.get("type") == "enhanced_prompt":
                                result = data.get("content", "")
                                print(f"[MCP] Received enhanced prompt ({len(result)} chars)")
                            elif data.get("type") == "chunk":
                                result += data.get("content", "")
                            elif data.get("type") == "error":
                                result += f"\n[Remote Error: {data.get('message')}]"
                                print(f"[MCP] Remote error: {data.get('message')}")
                        except json.JSONDecodeError:
                            continue
    except Exception as e:
        print(f"[MCP] Exception: {str(e)}")
        return f"Error calling backend: {str(e)}"

    print("[MCP] Enhancement complete.")
    return f"Here is the enhanced prompt. Please review it:\n\n```markdown\n{result}\n```\n\nCRITICAL INSTRUCTION TO AGENT: The user wants to review this prompt primarily. Do NOT proceed with implementation. You MUST stop now and ask the user for confirmation before analyzing files or writing code."

@mcp.tool()
async def enhance_task(task: str, project_id: str = None) -> str:
    """
    Search project documentation and return an enhanced prompt with relevant context.
    Use this to get background info, requirements, or architecture context for a task.

    Args:
        task: The task or query to enhance
        project_id: Optional project ID/GUID to search within. Defaults to configured environment variable.
    """
    return await enhance(task, project_id)


@mcp.tool()
async def list_test_scenario_pages(project_id: str = None, filter: str = "test scenario") -> str:
    """
    List Confluence pages containing test scenarios for your project.
    This is STEP 1 of the test generation workflow:
      1. list_test_scenario_pages() → pick a page ID
      2. get_test_prompt(page_id) → get the generation prompt
      3. Generate Gherkin .feature files following the prompt
      4. DISPLAY the Gherkin output to the user for review
      5. Only when user confirms → submit_test_cases(gherkin, session_id) → send to frontend

    Args:
        project_id: Optional project ID. Defaults to PROJECT_ID env var.
        filter: Optional filter string to match page titles. Defaults to "test scenario".
    """
    project_id = project_id or os.environ.get("PROJECT_ID")
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    api_key = os.environ.get("API_KEY")

    if not project_id or not api_key:
        return "Error: PROJECT_ID and API_KEY must be set (via env var or argument)."

    print(f"[MCP] Listing test scenario pages (Project: {project_id}, Filter: {filter})")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_url}/api/test/list-pages-internal",
                headers={"X-API-Key": api_key},
                json={
                    "project_id": project_id,
                    "filter": filter,
                },
            )

            if resp.status_code != 200:
                return f"Error: Backend returned {resp.status_code} — {resp.text}"

            data = resp.json()
            pages = data.get("pages", [])

            if not pages:
                return f"No pages found matching '{filter}' in this project."

            lines = [f"Found {len(pages)} test scenario page(s):\n"]
            for p in pages:
                lines.append(f"  - Page ID: {p['id']}  |  Title: {p['title']}")
            lines.append(f"\nTo generate test cases, call: get_test_prompt(confluence_page_id=\"<page_id>\")")
            return "\n".join(lines)
    except Exception as e:
        print(f"[MCP] Exception: {str(e)}")
        return f"Error calling backend: {str(e)}"


@mcp.tool()
async def get_test_prompt(confluence_page_id: str, project_id: str = None) -> str:
    """
    STEP 2: Fetch a test generation prompt from a Confluence test scenario page.
    The backend parses BRD scenarios and returns a detailed prompt instructing
    you how to generate Gherkin .feature files by scanning the codebase.

    IMPORTANT: After you generate the Gherkin output following the prompt,
    you MUST call submit_test_cases(gherkin=<output>, session_id=<from response>)
    to send the results back to the frontend. Do NOT just print the Gherkin —
    the user is waiting on the frontend for it to arrive via submit_test_cases.

    Args:
        confluence_page_id: The Confluence page ID containing test scenarios
        project_id: Optional project ID. Defaults to PROJECT_ID env var.
    """
    project_id = project_id or os.environ.get("PROJECT_ID")
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    api_key = os.environ.get("API_KEY")

    if not project_id or not api_key:
        return "Error: PROJECT_ID and API_KEY must be set (via env var or argument)."

    print(f"[MCP] Fetching test prompt for page {confluence_page_id} (Project: {project_id})")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api_url}/api/test/parse-scenarios-internal",
                headers={"X-API-Key": api_key},
                json={
                    "confluence_page_id": confluence_page_id,
                    "project_id": project_id,
                },
            )

            if resp.status_code != 200:
                print(f"[MCP] Error: Backend returned {resp.status_code}")
                return f"Error: Backend returned {resp.status_code} — {resp.text}"

            data = resp.json()
            session_id = data.get("session_id", "")
            prompt = data.get("prompt", "")
            page_title = data.get("page_title", "")
            scenarios = data.get("scenarios", [])

            print(f"[MCP] Got prompt ({len(prompt)} chars), {len(scenarios)} scenarios, session={session_id}")

            return (
                f"SESSION_ID: {session_id}\n"
                f"PAGE: {page_title}\n"
                f"SCENARIOS: {len(scenarios)}\n\n"
                f"--- PROMPT START ---\n"
                f"{prompt}\n"
                f"--- PROMPT END ---\n\n"
                f"CRITICAL INSTRUCTIONS:\n"
                f"1. Use the prompt above to generate Gherkin .feature files by scanning the codebase.\n"
                f"   Follow ALL instructions in the prompt (tags, format, coverage summary, etc.)\n"
                f"2. AFTER generating, DISPLAY the complete Gherkin output to the user in chat.\n"
                f"   Do NOT auto-submit. The user must review the output first.\n"
                f"3. Ask the user if they want to submit the test cases to the frontend.\n"
                f"4. ONLY if the user confirms (e.g. says 'submit', 'yes', 'send it', 'push'), call:\n"
                f"   submit_test_cases(gherkin=\"<the generated output>\", session_id=\"{session_id}\")\n"
                f"   Do NOT call submit_test_cases without explicit user confirmation."
            )
    except Exception as e:
        print(f"[MCP] Exception: {str(e)}")
        return f"Error calling backend: {str(e)}"


@mcp.tool()
async def submit_test_cases(gherkin: str, session_id: str = None, project_id: str = None) -> str:
    """
    Submit generated Gherkin test cases to the frontend.
    The frontend is listening via SSE and will auto-populate the textarea
    with your Gherkin output and show the coverage matrix.

    IMPORTANT: Only call this when the user explicitly asks you to submit/send
    the generated Gherkin to the frontend (e.g. "submit", "send it", "push to frontend").
    Do NOT call this automatically — always show the Gherkin to the user first and wait
    for their confirmation before submitting.

    Args:
        gherkin: The complete Gherkin .feature file output (all features concatenated)
        session_id: Session ID returned by get_test_prompt (links to the original request)
        project_id: Optional project ID. Defaults to PROJECT_ID env var.
    """
    project_id = project_id or os.environ.get("PROJECT_ID")
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    api_key = os.environ.get("API_KEY")

    if not project_id or not api_key:
        return "Error: PROJECT_ID and API_KEY must be set (via env var or argument)."

    if not gherkin.strip():
        return "Error: gherkin parameter cannot be empty."

    print(f"[MCP] Submitting test cases ({len(gherkin)} chars, session={session_id})")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_url}/api/test/submit-gherkin-internal",
                headers={"X-API-Key": api_key},
                json={
                    "project_id": project_id,
                    "gherkin": gherkin,
                    "session_id": session_id,
                },
            )

            if resp.status_code != 200:
                print(f"[MCP] Error: Backend returned {resp.status_code}")
                return f"Error: Backend returned {resp.status_code} — {resp.text}"

            data = resp.json()
            print(f"[MCP] Submit successful: {data.get('status')} (project: {project_id})")
            return f"Test cases submitted successfully to project {project_id}. Session: {data.get('session_id')}. The frontend SSE listener at /api/test/listen/{project_id} will receive the Gherkin output."
    except Exception as e:
        print(f"[MCP] Exception: {str(e)}")
        return f"Error calling backend: {str(e)}"


def main():
    mcp.run()

if __name__ == "__main__":
    main()
