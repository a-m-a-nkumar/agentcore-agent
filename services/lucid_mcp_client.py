"""
Lucidchart MCP client.

Thin wrapper over the official MCP SDK that calls the
`lucid_create_diagram_from_description` tool hosted at mcp.lucid.app.
The caller passes a plain-English architecture description; Lucid AI
returns a Lucidchart edit URL we surface to the user.

Auth model:
  • Bearer token from the user's OAuth flow (preferred — see
    routers/design.py /lucid-auth-url + /lucid-callback).
  • LUCID_OAUTH_TOKEN env var (fallback — useful for dev / single-user).

A missing token raises ValueError so the FastAPI layer can return 401
with a "Click 'Connect to Lucid' first" message.
"""

import os
import re
import logging

logger = logging.getLogger(__name__)

LUCID_MCP_URL = "https://mcp.lucid.app/mcp"
LUCID_MCP_TOOL = "lucid_create_diagram_from_description"


def _parse_result(result) -> dict:
    raw = "".join(item.text for item in result.content if hasattr(item, "text"))
    url_match = re.search(r"https://lucid\.app/lucidchart/[^\s\"'>\]]+", raw)
    edit_url = url_match.group(0).rstrip(".,)") if url_match else ""
    doc_match = re.search(r"lucidchart/([a-zA-Z0-9_-]{8,})/", raw)
    doc_id = doc_match.group(1) if doc_match else ""
    return {"edit_url": edit_url, "document_id": doc_id, "raw": raw}


async def create_diagram_from_description(description: str, title: str, token: str = None) -> dict:
    if not token:
        token = os.getenv("LUCID_OAUTH_TOKEN", "").strip()
    if not token:
        raise ValueError("Not connected to Lucid. Click 'Connect to Lucid' first.")

    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {token}"}
    logger.info(f"[LUCID_MCP] Connecting → {LUCID_MCP_URL}")

    async with streamablehttp_client(LUCID_MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            logger.info(f"[LUCID_MCP] Calling {LUCID_MCP_TOOL}")
            result = await session.call_tool(
                LUCID_MCP_TOOL,
                arguments={"description": description, "title": title},
            )

    parsed = _parse_result(result)

    if parsed.get("edit_url"):
        logger.info(f"[LUCID_MCP] Diagram ready: {parsed['edit_url']}")
    else:
        logger.warning(f"[LUCID_MCP] No URL in response. Raw: {parsed.get('raw', '')[:300]}")

    return parsed
