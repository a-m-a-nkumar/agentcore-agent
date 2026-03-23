import os
import sys
import httpx
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("enhance-prompt")

def log(msg: str):
    """Write logs to stderr so they don't corrupt the MCP stdio protocol."""
    print(msg, file=sys.stderr, flush=True)

@mcp.prompt()
async def enhance(task: str, project_id: str = None) -> str:
    """Enhance a dev task with context from your project docs"""
    project_id = project_id or os.environ.get("PROJECT_ID")
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    api_key = os.environ.get("API_KEY")

    if not project_id or not api_key:
        return "Error: PROJECT_ID and API_KEY must be set (via env var or argument)."

    log(f"[MCP] Enhancing task: {task} (Project: {project_id})")

    result = ""
    try:
        log("[MCP] Connecting to backend...")
        verify_ssl = os.environ.get("VERIFY_SSL", "false").lower() not in ("false", "0", "no")
        async with httpx.AsyncClient(timeout=120.0, verify=verify_ssl) as client:
            async with client.stream(
                "POST",
                f"{api_url}/api/orchestration/query-internal",
                headers={"X-API-Key": api_key},
                json={"project_id": project_id, "query": task, "max_chunks": 5, "return_prompt": True}
            ) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    log(f"[MCP] Error: Backend returned {r.status_code}: {body.decode()}")
                    return f"Error: Backend returned {r.status_code}"

                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if data.get("type") == "enhanced_prompt":
                                result = data.get("content", "")
                                log(f"[MCP] Received enhanced prompt ({len(result)} chars)")
                            elif data.get("type") == "chunk":
                                result += data.get("content", "")
                            elif data.get("type") == "error":
                                result += f"\n[Remote Error: {data.get('message')}]"
                                log(f"[MCP] Remote error: {data.get('message')}")
                        except json.JSONDecodeError:
                            continue
    except Exception as e:
        log(f"[MCP] Exception ({type(e).__name__}): {str(e)}")
        return f"Error calling backend ({type(e).__name__}): {str(e)}"

    if not result:
        log("[MCP] Warning: result is empty after streaming")
        return "Error: Backend returned empty response"

    log(f"[MCP] Enhancement complete ({len(result)} chars).")
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

def main():
    mcp.run()

if __name__ == "__main__":
    main()
