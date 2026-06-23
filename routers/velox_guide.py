"""
FastAPI router for the Velox user-guide vectorless RAG (flat one-shot).

POST /api/velox-guide/ask     {query}          -> full answer envelope (spec §6)
POST /api/velox-guide/expand  {node_id}        -> deep-dive on a known id (spec §5)

Behind the same Azure-AD `get_current_user` dependency as the other routers.
The router (tree + flat index) is built once at import; the LLM is the shared
DLX gateway helper, with the caller's user_id threaded through for token tracking.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.vectorless_rag.llm import GatewayLLM
from services.vectorless_rag.router import VeloxGuideRouter

from .projects import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/velox-guide", tags=["velox-guide"])

# Tree is small (~47 nodes) — load once at import.
_ROUTER = VeloxGuideRouter()

# FAQ retriever is built + indexed ONCE at import (shared, read-only across
# requests) so the per-request router doesn't rebuild the index / re-embed.
# Failure here must not take down the guide endpoints — FAQ is additive.
try:
    from environment import FAQ_ENABLED as _FAQ_ENABLED
except Exception:  # noqa: BLE001
    _FAQ_ENABLED = True

_FAQ_RETRIEVER = None
if _FAQ_ENABLED:
    try:
        from services.vectorless_rag.faq import build_retriever as _build_faq

        _FAQ_RETRIEVER = _build_faq()
        logger.info("[velox-guide] FAQ retriever ready: backend=%s", _FAQ_RETRIEVER.name)
    except Exception:  # noqa: BLE001
        _FAQ_RETRIEVER = None
        logger.exception("[velox-guide] FAQ retriever disabled (build failed)")
else:
    logger.info("[velox-guide] FAQ retriever disabled (FAQ_ENABLED=false)")


class AskRequest(BaseModel):
    query: str


class ExpandRequest(BaseModel):
    node_id: str


def _user_id(current_user: dict) -> str | None:
    return current_user.get("user_id") or current_user.get("oid") or current_user.get("email")


@router.post("/ask")
async def ask(request: AskRequest, current_user: dict = Depends(get_current_user)):
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    # Per-request LLM so token usage is attributed to this caller.
    r = VeloxGuideRouter(
        tree=_ROUTER.tree,
        llm=GatewayLLM(user_id=_user_id(current_user)),
        faq_retriever=_FAQ_RETRIEVER,
    )
    return r.ask(query)


@router.post("/expand")
async def expand(request: ExpandRequest, current_user: dict = Depends(get_current_user)):
    node_id = (request.node_id or "").strip()
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")
    r = VeloxGuideRouter(
        tree=_ROUTER.tree,
        llm=GatewayLLM(user_id=_user_id(current_user)),
        faq_retriever=_FAQ_RETRIEVER,
    )
    return r.expand(node_id)
