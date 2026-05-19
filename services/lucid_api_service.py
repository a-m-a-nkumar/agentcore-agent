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
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

LUCID_BASE_URL = "https://api.lucid.co"
LUCID_API_VERSION = "1"  # Lucid REST API version header value
LUCID_DEFAULT_TIMEOUT = 15      # seconds — metadata/search calls are fast
LUCID_EXPORT_TIMEOUT = 60       # seconds — image render can take 5-20s on Lucid's side
LUCID_EXPORT_MAX_RETRIES = 2    # one initial + 2 retries for transient failures


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
                       page_size: int = 200,
                       products: Optional[List[str]] = None,
                       max_total: int = 500) -> List[Dict]:
        """POST /documents/search — list the authenticated user's docs.

        Lucid's REST API doesn't expose a paginated GET /documents
        endpoint; document listing is done through this search resource.
        Empty `keywords` returns the user's most recently-edited docs.

        Pagination: Lucid returns a `cursor` field when more results are
        available. We loop calling search with that cursor in subsequent
        request bodies until exhausted (or until we hit `max_total`).

        Args:
          search: optional keyword. Server-side filters titles and content.
                  If empty, returns recently-edited docs (subject to
                  Lucid's search-index indexing delay of ~30s-several
                  minutes for newly-created documents).
          page_size: per-page cap. Lucid's documented max for this
                     endpoint is 200; values higher are silently clamped.
          products: list of product types to include. Default includes BOTH
                    Lucidchart and Lucidspark because architecture
                    diagrams created via Lucid AI sometimes land as Spark
                    whiteboards. Pass ["lucidchart"] to restrict.
          max_total: hard cap on returned documents to avoid huge payloads
                     for accounts with thousands of docs.

        Returns a list of dicts shaped roughly like:
            { "documentId": "...", "title": "...",
              "lastModified": "ISO-8601", "product": "lucidchart", ... }
        Pre-sorted by lastModified DESC, capped at max_total.
        """
        url = f"{self.base_url}/documents/search"
        if not products:
            products = ["lucidchart", "lucidspark"]

        # The search resource takes a JSON body, not query params. `product`
        # must be an array of strings; `keywords` is a single string;
        # `cursor` (when present in a response) carries us to the next page.
        body: Dict = {
            "product": list(products),
            "pageSize": int(min(page_size, 200)),
            "excludeTrashed": True,
        }
        if search:
            body["keywords"] = search

        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"

        all_docs: List[Dict] = []
        cursor: Optional[str] = None
        pages_fetched = 0
        # Absolute sanity cap on page hops — 20 * 200 = 4000 docs upper bound.
        # max_total breaks out earlier in normal use.
        for _ in range(20):
            req_body = dict(body)
            if cursor:
                req_body["cursor"] = cursor
            try:
                resp = requests.post(
                    url, headers=headers, json=req_body, timeout=self.timeout,
                )
            except requests.RequestException as e:
                raise LucidUpstreamError(
                    f"network error reaching {url}: {e}"
                ) from e
            self._raise_for_status(resp, f"list_documents (page {pages_fetched + 1})")

            try:
                payload = resp.json() or {}
            except ValueError:
                break

            # Lucid returns results either as a top-level list or wrapped under
            # `data` (or sometimes `documents`) depending on API version.
            if isinstance(payload, list):
                page_docs = payload
                cursor = None
            elif isinstance(payload, dict):
                page_docs = (
                    payload.get("data")
                    or payload.get("documents")
                    or payload.get("results")
                    or []
                )
                cursor = (
                    payload.get("cursor")
                    or payload.get("nextCursor")
                    or payload.get("nextPageToken")
                )
            else:
                page_docs = []
                cursor = None

            if not isinstance(page_docs, list):
                break

            all_docs.extend(page_docs)
            pages_fetched += 1

            if not cursor or len(all_docs) >= max_total:
                break

        logger.info(
            "[LucidAPI] list_documents: fetched %d docs across %d page(s) "
            "(products=%s search=%r)",
            len(all_docs), pages_fetched, products, search,
        )

        def _sort_key(d: Dict) -> str:
            return d.get("lastModified") or d.get("modified") or ""
        return sorted(all_docs, key=_sort_key, reverse=True)[:max_total]

    def get_document_contents(self, document_id: str) -> Dict:
        """Fetch the document's structured content (shapes, text, connectors).

        Returns the JSON returned by `GET /documents/{id}/contents` — the same
        endpoint that powers our page-id discovery, but consumed here as
        structured data for downstream LLM-context formatting.

        Schema (abbreviated): {
          id, title, product,
          pages: [
            { id, title, index, items: { shapes: [...], lines: [...] } }
          ]
        }

        Used by the SAD orchestrator (via _format_lucid_for_llm) so Claude
        can reason about the Lucid diagram's contents during section
        generation — same way it reads drawio's mxGraph XML.
        """
        url = f"{self.base_url}/documents/{document_id}/contents"
        try:
            resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise LucidUpstreamError(f"network error reaching {url}: {e}") from e
        self._raise_for_status(resp, f"get_document_contents({document_id})")
        try:
            return resp.json() or {}
        except ValueError as e:
            raise LucidUpstreamError(
                f"Lucid /contents returned non-JSON for {document_id}: {e}"
            ) from e

    def export_document(self, document_id: str, fmt: str = "png",
                        page_id: Optional[str] = None,
                        dpi: int = 200,
                        crop_to_content: bool = True) -> bytes:
        """Fetch a rendered image of a Lucid document.

        Uses Lucid's overloaded `GET /documents/{id}` endpoint — the same URL
        as metadata retrieval, but the `Accept` header decides the response:

            Accept: application/json     -> document metadata JSON
            Accept: image/png            -> raw PNG bytes (96 dpi default)
            Accept: image/png;dpi=200    -> high-DPI PNG (recommended for DOCX)
            Accept: image/jpeg           -> raw JPEG bytes

        Verified end-to-end via .scratch/probe_lucid_png_export.py:
        with `Document Read` grant + valid API key + Lucid-Api-Version: 1
        header, the endpoint returns real PNG/JPEG bytes that render in any
        browser, `python-docx` add_picture, etc.

        Args:
          document_id: Lucid documentId (UUID).
          fmt: 'png' (default, lossless) or 'jpeg' (smaller files). SVG and
               PDF are NOT exposed via the public REST API — UI export only.
          page_id: optional Lucid page id. If omitted, Lucid returns the
               document's first page. Multi-page architecture diagrams should
               iterate pages explicitly (use list_documents -> /contents to
               get the page id list).
          dpi: PNG resolution. 96 is screen-default; 200 is recommended for
               DOCX print clarity at A4/letter. Ignored for JPEG. Very large
               diagrams are auto-downscaled server-side.
          crop_to_content: when True (default), Lucid trims empty canvas
               margins around the actual diagram content. Usually wanted
               for SAD embeds so the diagram fills the available width.

        Returns: raw image bytes (PNG by default).
        Raises:
          InvalidLucidKeyError on 401.
          LucidNotAccessibleError on 403/404 — most often means the API
            key lacks `Document Read` grant, OR the caller is not a direct
            collaborator on this specific document (admin-by-org-membership
            is NOT sufficient for this endpoint).
          LucidUpstreamError on 5xx / network / bad-bytes response.
        """
        if fmt == "svg":
            raise ValueError(
                "SVG export is not exposed via Lucid's public REST API "
                "(UI-only). Use fmt='png' instead."
            )
        if fmt not in ("png", "jpeg"):
            raise ValueError(f"unsupported fmt {fmt!r}; valid: 'png', 'jpeg'")

        accept = f"image/{fmt}"
        if fmt == "png" and dpi and dpi != 96:
            accept = f"image/png;dpi={dpi}"

        url = f"{self.base_url}/documents/{document_id}"
        headers = dict(self.headers)
        headers["Accept"] = accept

        params: Dict[str, str] = {}
        if page_id:
            params["pageId"] = page_id
        if crop_to_content:
            params["crop"] = "content"

        # Image exports go through Lucid's server-side renderer which can
        # take 5-20s for non-trivial diagrams; the metadata-call default
        # timeout (15s) is risky here. Use the export-specific timeout +
        # retry once or twice on transient failures (network timeout,
        # 5xx, empty body). This catches the "user clicks Fetch & Save
        # right after Lucid AI finishes generating, Lucid's first render
        # request is slow, we time out, user sees 502" pattern.
        last_error: Optional[Exception] = None
        for attempt in range(1, LUCID_EXPORT_MAX_RETRIES + 2):  # 1 + max_retries
            try:
                resp = requests.get(
                    url, headers=headers, params=params,
                    timeout=LUCID_EXPORT_TIMEOUT,
                )
            except requests.Timeout as e:
                last_error = e
                logger.warning(
                    f"[LucidAPI] export_document timeout on attempt {attempt} for "
                    f"{document_id} (fmt={fmt}); will retry up to "
                    f"{LUCID_EXPORT_MAX_RETRIES} time(s)"
                )
                if attempt <= LUCID_EXPORT_MAX_RETRIES:
                    time.sleep(2 ** (attempt - 1))  # 1s, 2s
                    continue
                raise LucidUpstreamError(
                    f"Lucid export timed out after {LUCID_EXPORT_TIMEOUT}s "
                    f"on {LUCID_EXPORT_MAX_RETRIES + 1} attempts for {document_id}. "
                    "Try again in a moment — Lucid's renderer may be under load."
                ) from e
            except requests.RequestException as e:
                last_error = e
                logger.warning(
                    f"[LucidAPI] export_document network error on attempt {attempt} "
                    f"for {document_id}: {e}"
                )
                if attempt <= LUCID_EXPORT_MAX_RETRIES:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise LucidUpstreamError(
                    f"network error reaching {url} after retries: {e}"
                ) from e

            # 5xx → retryable transient failure (Lucid having a bad moment)
            if resp.status_code >= 500:
                last_error = LucidUpstreamError(
                    f"Lucid returned {resp.status_code} (attempt {attempt}/{LUCID_EXPORT_MAX_RETRIES + 1})"
                )
                logger.warning(
                    f"[LucidAPI] export_document got {resp.status_code} from Lucid on "
                    f"attempt {attempt} for {document_id}; retrying"
                )
                if attempt <= LUCID_EXPORT_MAX_RETRIES:
                    time.sleep(2 ** (attempt - 1))
                    continue
                # exhausted retries — fall through to _raise_for_status
                break

            # 4xx (auth, permission, not-found) → don't retry, raise immediately
            # 200 with non-empty body → done, exit retry loop
            break

        # Final status check (catches 4xx, any non-200 we didn't already handle)
        self._raise_for_status(
            resp, f"export_document({document_id}, fmt={fmt}, page={page_id})"
        )

        if not resp.content:
            raise LucidUpstreamError(
                f"Lucid returned empty body when exporting {document_id} as {fmt}. "
                "The diagram may still be rendering — try Fetch & Save again in a few seconds."
            )

        # Sanity check magic bytes so a misconfigured response (JSON body
        # returned despite our Accept header, content-type drift, etc.)
        # fails loud rather than writing garbage to S3.
        if fmt == "png" and not resp.content.startswith(b"\x89PNG\r\n\x1a\n"):
            head = resp.content[:32]
            raise LucidUpstreamError(
                f"Lucid returned non-PNG bytes for {document_id} (got {head!r}). "
                "Check that the API key has 'Document Read' grant and that "
                "the caller is a direct collaborator on this document."
            )
        if fmt == "jpeg" and not resp.content.startswith(b"\xff\xd8\xff"):
            head = resp.content[:32]
            raise LucidUpstreamError(
                f"Lucid returned non-JPEG bytes for {document_id} (got {head!r})."
            )
        logger.info(
            f"[LucidAPI] export_document OK for {document_id} ({len(resp.content)} bytes, "
            f"fmt={fmt}, attempts={attempt})"
        )
        return resp.content
