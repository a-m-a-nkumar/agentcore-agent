"""
Thin wrapper around the Lucid REST API (https://api.lucid.co).

Used by routers/integrations.py and routers/design.py for the personal
API-key-based Lucid flow:

  1. test_connection()        — validate the user's key by hitting /users/me
  2. list_documents(...)      — enumerate the user's recent Lucid docs so they
                                 can pick the one they just generated
  3. export_document(...)     — pull a document's contents back as SVG / PNG
                                 so we can save it to S3 against the session's
                                 diagram slot

The Lucid REST API uses Bearer auth with a personal API key that starts with
"key-..." (see lucid.app -> Account Settings -> API Tokens). Each key is
region-pinned at issuance (the trailing -Lucid-US / -Lucid-EU suffix); for
v1 we hardcode the US base URL. EU support can branch on key suffix later.

Error mapping:
  401              -> InvalidLucidKeyError  ("re-link your Lucid API key")
  403 / 404        -> LucidNotAccessibleError ("doc not found or no access")
  5xx              -> LucidUpstreamError ("Lucid is unavailable, try again")
  network / other  -> LucidUpstreamError wrapping the underlying exception

Callers should catch these specific exceptions and turn them into
HTTPException with appropriate status codes in the FastAPI layer.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

LUCID_BASE_URL = "https://api.lucid.co"
LUCID_API_VERSION = "1"  # Lucid REST API version header value
LUCID_DEFAULT_TIMEOUT = 15  # seconds


class LucidError(Exception):
    """Base error for Lucid API issues. Catch this to log + bail."""


class InvalidLucidKeyError(LucidError):
    """The API key was rejected (401). User needs to re-link in profile."""


class LucidNotAccessibleError(LucidError):
    """403 / 404 — document doesn't exist or this key can't see it."""


class LucidUpstreamError(LucidError):
    """5xx / network — Lucid is having a bad day. Try again later."""


class LucidAPIService:
    """Per-user wrapper. Instantiate with a decrypted user API key."""

    def __init__(self, api_key: str, base_url: str = LUCID_BASE_URL,
                 timeout: int = LUCID_DEFAULT_TIMEOUT):
        if not api_key:
            raise ValueError("LucidAPIService requires a non-empty api_key")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Lucid-Api-Version": LUCID_API_VERSION,
            "Accept": "application/json",
        }

    # ---------------------------------------------------------------------
    # internal helpers
    # ---------------------------------------------------------------------
    def _raise_for_status(self, resp: requests.Response, context: str) -> None:
        """Translate Lucid HTTP errors to typed LucidError subclasses."""
        if resp.status_code == 401:
            raise InvalidLucidKeyError(
                f"Lucid rejected the API key ({context}). Re-link in profile."
            )
        if resp.status_code in (403, 404):
            raise LucidNotAccessibleError(
                f"Lucid said {resp.status_code} for {context}. "
                "Document not found or not accessible with this key."
            )
        if resp.status_code >= 500:
            raise LucidUpstreamError(
                f"Lucid returned {resp.status_code} for {context}. Try again."
            )
        if resp.status_code >= 400:
            # 4xx that we didn't special-case — surface body briefly for debugging
            preview = resp.text[:200] if resp.text else "<empty body>"
            raise LucidError(f"Lucid {resp.status_code} for {context}: {preview!r}")

    # ---------------------------------------------------------------------
    # public API
    # ---------------------------------------------------------------------
    def test_connection(self) -> Dict:
        """Verify the API key is accepted by Lucid.

        Earlier attempts at `/users/me` (400 — Lucid parses "me" as a
        numeric ID) and `/profile` (404 — endpoint not present in this
        API version) both failed. The most reliable validity check that
        works across Lucid's REST API surface is the same endpoint we
        use for listing: POST /documents/search with pageSize:1. A valid
        key returns 200 (possibly empty list); an invalid key returns
        401 regardless of whether the user has any documents.

        Returns a small success marker. The caller treats any 200 as
        success and discards identity info.
        """
        url = f"{self.base_url}/documents/search"
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        # Lucid expects `product` as an array of strings, not a single value.
        body: Dict = {"product": ["lucidchart"], "pageSize": 1}
        try:
            resp = requests.post(
                url, headers=headers, json=body, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LucidUpstreamError(f"network error reaching {url}: {e}") from e
        self._raise_for_status(resp, "test_connection")
        return {"ok": True}

    def list_documents(self, search: Optional[str] = None,
                       page_size: int = 50,
                       product: str = "lucidchart") -> List[Dict]:
        """POST /documents/search — list the authenticated user's docs.

        Lucid's REST API doesn't expose a paginated GET /documents
        endpoint; document listing is done through the search resource.
        Empty `keywords` returns the user's most recently-edited docs.

        Args:
          search: optional keyword. Server-side filters titles and
                  content. If empty, returns the most recent docs.
          page_size: cap on returned items (single page).
          product: defaults to lucidchart. Lucidspark whiteboards live
                   under the same API key but are filtered out for v1 to
                   keep the picker focused on architecture diagrams.

        Returns a list of dicts shaped roughly like:
            { "documentId": "...", "title": "...",
              "lastModified": "ISO-8601", "product": "lucidchart", ... }
        Pre-sorted by lastModified DESC, capped at page_size.
        """
        url = f"{self.base_url}/documents/search"
        # The search resource takes a JSON body, not query params. `product`
        # must be an array of strings; `keywords` is a single string.
        body: Dict = {
            "product": [product],
            "pageSize": int(page_size),
            "excludeTrashed": True,
        }
        if search:
            body["keywords"] = search

        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"

        try:
            resp = requests.post(
                url, headers=headers, json=body, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LucidUpstreamError(f"network error reaching {url}: {e}") from e
        self._raise_for_status(resp, "list_documents")

        try:
            payload = resp.json() or {}
        except ValueError:
            return []

        # Lucid returns the results either as a top-level list or wrapped
        # under `data` depending on API version. Accept both.
        if isinstance(payload, list):
            docs = payload
        elif isinstance(payload, dict):
            docs = payload.get("data") or payload.get("documents") or []
        else:
            docs = []

        if not isinstance(docs, list):
            return []

        # Newest-first; Lucid usually returns this order already but we
        # don't rely on it.
        def _key(d: Dict) -> str:
            return d.get("lastModified") or d.get("modified") or ""
        return sorted(docs, key=_key, reverse=True)[:page_size]

    def export_document(self, document_id: str, fmt: str = "png",
                        page_id: Optional[str] = None) -> bytes:
        """Fetch a rendered image of a Lucid document page.

        Earlier this method called `GET /documents/{id}/contents?format=svg`
        and trusted the format query param. In practice Lucid's `/contents`
        endpoint always returns the *structured JSON* representation of the
        document (pages, shapes, text areas) and silently ignores `?format`.
        We were saving that JSON to S3 with `Content-Type: image/svg+xml`
        and browsers refused to render it — broken-image icon in the preview
        pane, broken SAD §4/6/7 embeds.

        The correct sync endpoint is:
            GET /documents/{id}/contents/image/{pageId}
        which returns PNG bytes for that page. SVG export is only available
        via Lucid's async exports API (`POST /documents/{id}/exports` + poll)
        which we deliberately skip for v1 — PNG renders identically in the
        browser, embeds cleanly in DOCX, and avoids the polling complexity.

        Two-step flow:
          1. GET /contents → JSON → pick first page's id (unless caller
             passed `page_id`).
          2. GET /contents/image/{page_id} → raw PNG bytes.

        Args:
          document_id: Lucid documentId.
          fmt: only "png" is supported; the param is kept for back-compat with
               the original signature. Pass "svg" and you'll get a clear error
               rather than corrupted output.
          page_id: optional Lucid page id; defaults to pages[0].id from the
                   document's /contents response.

        Returns raw PNG bytes for one page. Caller writes them to S3 at
        sessions/{session_id}/diagram/{type}.png.
        """
        if fmt == "svg":
            raise ValueError(
                "SVG export is not supported via the sync API; use fmt='png'. "
                "If SVG fidelity is required, switch to Lucid's async /exports "
                "endpoint with polling (deferred to a later release)."
            )
        if fmt != "png":
            raise ValueError(f"unsupported fmt {fmt!r}; want 'png'")

        # Step 1 — discover the page id from /contents if the caller didn't
        # provide one. Lucid's /contents returns JSON regardless of headers
        # so we lean into that.
        if not page_id:
            contents_url = f"{self.base_url}/documents/{document_id}/contents"
            try:
                resp = requests.get(
                    contents_url, headers=self.headers, timeout=self.timeout,
                )
            except requests.RequestException as e:
                raise LucidUpstreamError(
                    f"network error reaching {contents_url}: {e}"
                ) from e
            self._raise_for_status(resp, f"export_document({document_id}) - /contents")
            try:
                doc = resp.json()
            except ValueError as e:
                raise LucidUpstreamError(
                    f"Lucid /contents returned non-JSON for {document_id}: {e}"
                ) from e
            pages = doc.get("pages") if isinstance(doc, dict) else None
            if not isinstance(pages, list) or not pages:
                raise LucidUpstreamError(
                    f"Lucid document {document_id} has no pages — nothing to export"
                )
            page_id = pages[0].get("id") if isinstance(pages[0], dict) else None
            if not page_id:
                raise LucidUpstreamError(
                    f"Lucid document {document_id} first page is missing an id"
                )

        # Step 2 — fetch the PNG bytes for that page.
        image_url = (
            f"{self.base_url}/documents/{document_id}/contents/image/{page_id}"
        )
        headers = dict(self.headers)
        headers["Accept"] = "image/png"

        try:
            resp = requests.get(
                image_url, headers=headers, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LucidUpstreamError(
                f"network error reaching {image_url}: {e}"
            ) from e
        self._raise_for_status(
            resp, f"export_document({document_id}, page={page_id}, png)"
        )

        if not resp.content:
            raise LucidUpstreamError(
                f"Lucid returned empty body for PNG export of {document_id} "
                f"page {page_id}"
            )
        # Sanity check: PNGs start with the 8-byte magic header.
        if not resp.content.startswith(b"\x89PNG\r\n\x1a\n"):
            head = resp.content[:16]
            raise LucidUpstreamError(
                f"Lucid returned non-PNG bytes for {document_id} page {page_id}; "
                f"first 16 bytes: {head!r}. Check Lucid endpoint changes."
            )
        return resp.content
