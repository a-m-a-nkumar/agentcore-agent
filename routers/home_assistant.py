"""
Home-assistant router endpoint.

POST /api/home-assistant/route  {query, project_id?}  -> {route, confidence, reason}

A thin LLM classifier (one cheap Haiku call) that decides which knowledge source
should answer a query: the Velox user guide ("guide") or the user's synced project
knowledge base ("project"). The FRONTEND then calls the matching endpoint
(/api/velox-guide/ask  or  /api/orchestration/query) — this keeps each path's native
response shape (structured envelope vs SSE stream) and reuses both unchanged.

Classification-only by design: the dispatch contract stays the same if this is later
upgraded to a tool-agent. Reuses services.vectorless_rag.llm.GatewayLLM.json_call for
the Haiku call + JSON repair/retry. Behind the same auth as the other routers.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from prompts.home_assistant_router import HOME_ROUTER_SYSTEM, ROUTES
from services.vectorless_rag.llm import GatewayLLM

from .projects import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/home-assistant", tags=["home-assistant"])


class RouteRequest(BaseModel):
    query: str
    project_id: str | None = None


def _user_id(current_user: dict) -> str | None:
    return current_user.get("user_id") or current_user.get("oid") or current_user.get("email")


@router.post("/route")
async def route(request: RouteRequest, current_user: dict = Depends(get_current_user)):
    query = (request.query or "").strip()
    if not query:
        return {"route": "guide", "confidence": 0.0, "reason": "empty query"}

    llm = GatewayLLM(user_id=_user_id(current_user))
    try:
        resp = llm.json_call(HOME_ROUTER_SYSTEM, f"QUESTION:\n{query}")
    except Exception as e:  # noqa: BLE001 — never block the assistant on a routing hiccup
        logger.warning("home-assistant route classification failed: %s", e)
        resp = {}

    route_val = resp.get("route")
    if route_val not in ROUTES:
        route_val = "guide"  # safe default — the guide path needs no project + can abstain
    try:
        confidence = float(resp.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(resp.get("reason") or "").strip()

    # No-project guard: the project path needs a selected, synced project.
    project_unavailable = route_val == "project" and not (request.project_id or "").strip()
    if project_unavailable:
        route_val = "guide"

    return {
        "route": route_val,
        "confidence": confidence,
        "reason": reason,
        "project_unavailable": project_unavailable,
    }
