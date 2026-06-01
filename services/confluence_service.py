import time
import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional, Tuple
import logging
import html

logger = logging.getLogger(__name__)

# Module-level cache of the FULL space list, keyed by user email so a request
# from one user can't see another user's spaces. TTL is short enough that
# brand-new spaces appear quickly (5 min) but long enough that typing every
# character in the search box doesn't re-enumerate Atlassian.
#
# Why a cache: without it, each keystroke ("sd", "sdl", "sdlc", ...) triggers
# a fresh full pagination across (in some instances) thousands of pages,
# which both consumes Atlassian's WAF burst budget — surfacing as
# "SSL: UNEXPECTED_EOF_WHILE_READING" errors when too many parallel
# connections handshake at once — and gives a poor UX (each keystroke
# pays the full cost). The cache means: pay it ONCE per 5-min window per
# user, then every keystroke is in-memory.
_SPACE_LIST_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_SPACE_LIST_CACHE_TTL_SECS = 300  # 5 minutes


class ConfluenceService:
    """Service for interacting with Confluence Cloud API"""

    def __init__(self, domain: str, email: str, api_token: str):
        """
        Initialize Confluence service

        Args:
            domain: Atlassian domain (e.g., 'mycompany.atlassian.net')
            email: User's email address
            api_token: Atlassian API token
        """
        self.domain = domain                       # bare host, for building v2 URLs
        self.base_url = f"https://{domain}/wiki"   # legacy v1 root
        self.v2_base  = f"https://{domain}/wiki/api/v2"  # v2 API root
        self.email = email                          # used as cache key for the spaces list
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        # requests.Session pools TCP+TLS connections across calls. Without it,
        # every parallel page fetch did a fresh SSL handshake to Atlassian and
        # the burst tore connections (UNEXPECTED_EOF). With Session, the
        # ThreadPoolExecutor reuses a small set of warm connections.
        self._session = requests.Session()
        # Cache for space_key -> numeric space ID (required by v2 endpoints).
        # Populated lazily; one entry per space encountered per ConfluenceService
        # instance, so a sync touches the v2 /spaces lookup at most once.
        self._space_id_cache: Dict[str, str] = {}

    def _get_with_retry(self, url: str, **kwargs):
        """GET with one auto-retry on transient TLS / connection errors.

        Atlassian's edge occasionally drops a TLS handshake mid-flight
        (SSLEOFError "UNEXPECTED_EOF_WHILE_READING") or resets the
        connection — particularly after bursts of parallel requests. A
        single retry after ~400ms is almost always sufficient; if the
        second attempt also fails, the network is genuinely degraded and
        we let the caller see the error. We deliberately do NOT retry
        non-network errors (4xx/5xx response codes) — those are handled
        by raise_for_status() in the call site.
        """
        last_exc: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                return requests.get(url, **kwargs)
            except (requests.exceptions.SSLError,
                    requests.exceptions.ConnectionError) as e:
                last_exc = e
                if attempt == 1:
                    logger.warning(
                        f"[Confluence] transient network error on {url} "
                        f"(attempt {attempt}): {e}. Retrying once."
                    )
                    time.sleep(0.4)
                    continue
                raise
        # Unreachable, but mypy-quiet.
        raise last_exc if last_exc else RuntimeError("retry loop exited unexpectedly")

    def test_connection(self) -> bool:
        """Test if credentials are valid by fetching current user info"""
        try:
            url = f"{self.base_url}/rest/api/user/current"
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=30)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Confluence connection test failed: {e}")
            return False
    
    def get_spaces(self) -> List[Dict]:
        """
        Fetch all accessible Confluence spaces

        Returns:
            List of spaces with key, name, id, and type
        """
        try:
            url = f"{self.base_url}/rest/api/space"
            all_spaces = []
            start = 0
            limit = 100

            # Paginate through all results
            while True:
                params = {"limit": limit, "start": start}
                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
                response.raise_for_status()

                data = response.json()
                batch = data.get("results", [])
                all_spaces.extend(batch)

                # Stop if we've received fewer results than the limit (last page)
                if len(batch) < limit:
                    break
                start += limit

            # Filter out personal spaces (keys starting with ~) — keep all real team spaces
            return [
                {
                    "key": space["key"],
                    "name": space["name"],
                    "id": space["id"],
                    "type": space.get("type", "global")
                }
                for space in all_spaces
                if not space["key"].startswith("~")
            ]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence spaces: {e}")
            raise Exception(f"Failed to fetch Confluence spaces: {str(e)}")
    
    def _fetch_spaces_batch(self, page_start: int, page_limit: int) -> List[Dict]:
        """One paginated HTTP call to /rest/api/space with up to 3 retries on
        transient errors. Uses the instance-level Session for connection
        pooling so a burst of concurrent fetches does NOT trigger fresh SSL
        handshakes on every call — the previous "SSLError: UNEXPECTED_EOF"
        storm came from doing exactly that against Atlassian's WAF."""
        url = f"{self.base_url}/rest/api/space"
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self._session.get(
                    url,
                    headers=self.headers,
                    auth=self.auth,
                    params={"limit": page_limit, "start": page_start},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json().get("results", [])
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_err = e
                logger.warning(
                    f"Confluence spaces attempt {attempt + 1}/3 failed (start={page_start}): {e}"
                )
                if attempt < 2:
                    # Exponential backoff — Atlassian's edge needs a moment
                    # after a SSL EOF storm to release sockets.
                    time.sleep(1 + attempt)
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching Confluence spaces (start={page_start}): {e}")
                raise Exception(f"Failed to fetch Confluence spaces: {str(e)}")
        logger.error(
            f"Confluence spaces failed after 3 retries (start={page_start}): {last_err}"
        )
        raise Exception(f"Failed to fetch Confluence spaces after 3 retries: {str(last_err)}")

    def _fetch_all_spaces(self) -> List[Dict]:
        """Enumerate every space in the Confluence instance (one full pass).

        Used to (re)populate the module-level cache. Bounded to 3 concurrent
        in-flight fetches per wave: anything higher kept tripping Atlassian's
        WAF and surfacing as `UNEXPECTED_EOF_WHILE_READING`. The Session above
        keeps connections warm so 3 parallel calls reuse 1–2 TCP sockets
        rather than handshaking 8 fresh ones.

        Filters personal spaces (keys starting with `~`) at ingestion time
        so the cache holds only what's actually pickable.
        """
        import concurrent.futures
        all_spaces: List[Dict] = []
        page_size = 100   # Confluence Cloud per-page cap
        wave_size = 3     # Empirically safe under Atlassian's burst limit
        wave_start = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=wave_size) as executor:
            while True:
                offsets = [wave_start + i * page_size for i in range(wave_size)]
                futures = [executor.submit(self._fetch_spaces_batch, off, page_size) for off in offsets]
                batches = [f.result() for f in futures]
                for batch in batches:
                    for s in batch:
                        if s["key"].startswith("~"):
                            continue
                        all_spaces.append({
                            "key": s["key"],
                            "name": s["name"],
                            "id": s["id"],
                            "type": s.get("type", "global"),
                        })
                if any(len(b) < page_size for b in batches):
                    break
                wave_start += wave_size * page_size
        return all_spaces

    def _get_cached_all_spaces(self) -> List[Dict]:
        """Return the cached full space list, refreshing if stale or missing.

        Cache is per-user (keyed by email) so credentials from different
        accounts don't see each other's spaces.
        """
        now = time.time()
        cached = _SPACE_LIST_CACHE.get(self.email)
        if cached and (now - cached[0]) < _SPACE_LIST_CACHE_TTL_SECS:
            return cached[1]
        logger.info(
            f"[Confluence] cache miss/expired for {self.email}; fetching full space list…"
        )
        spaces = self._fetch_all_spaces()
        _SPACE_LIST_CACHE[self.email] = (now, spaces)
        logger.info(
            f"[Confluence] cached {len(spaces)} spaces for {self.email} (TTL {_SPACE_LIST_CACHE_TTL_SECS}s)"
        )
        return spaces

    def get_spaces_page(self, start: int = 0, limit: int = 100, search: str = "") -> Dict:
        """
        Page through (or search) Confluence spaces, backed by an in-memory
        cache of the full list.

        Behaviour:
          - First call (any path) fills the cache by enumerating every page
            of /rest/api/space with bounded concurrency. ~1 round trip per
            wave of 3, so a 2,000-space instance completes in ~7 waves.
          - All subsequent calls within `_SPACE_LIST_CACHE_TTL_SECS` (5 min)
            serve from memory — instant, no Atlassian network cost.
          - search != "": filter the cached list by key/name substring,
            return all matches with hasMore=False.
          - search == "": slice the cached list at [start:start+limit],
            return hasMore=True iff more items remain. The scroll-to-load
            sentinel in the UI advances `start` to fetch the next slice
            (still from memory, also instant).

        Returns:
            dict with keys: spaces, hasMore
        """
        spaces = self._get_cached_all_spaces()

        if search:
            q = search.lower()
            matches = [
                s for s in spaces
                if q in s["key"].lower() or q in s["name"].lower()
            ]
            logger.info(
                f"[Confluence] search='{search}' matched {len(matches)}/{len(spaces)} space(s) "
                f"(cached list)"
            )
            return {"spaces": matches, "hasMore": False}

        # No-search: slice the cached list. The frontend's lazy-load sentinel
        # advances `start` by `limit` on each scroll hit; we serve the next
        # slice from memory until exhausted.
        page = spaces[start : start + limit]
        has_more = (start + limit) < len(spaces)
        return {"spaces": page, "hasMore": has_more}

    def _resolve_space_id(self, space_key: str) -> str:
        """
        Resolve a space key to its numeric space ID (required by v2 endpoints).

        Cached per ConfluenceService instance so a sync makes at most ONE extra
        HTTP call per space, regardless of how many times get_space_pages is invoked.

        Also caches the space's homepageId on `self._space_homepage_cache` so
        callers that want to exclude the auto-generated space-overview page
        (the one Confluence creates with macros like "Recently updated content"
        + "Contributors") can do so without a second HTTP call.
        """
        if space_key in self._space_id_cache:
            return self._space_id_cache[space_key]

        url = f"{self.v2_base}/spaces"
        # _get_with_retry survives Atlassian's occasional TLS handshake drops
        # that surface as SSL: UNEXPECTED_EOF_WHILE_READING. Without it, a
        # single edge flake would 500 the entire /confluence/pages call and
        # the user would see an empty page-picker on the BRD comparison flow.
        response = self._get_with_retry(
            url,
            params={"keys": space_key, "limit": 1},
            headers={"Accept": "application/json"},
            auth=self.auth, timeout=30,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            raise Exception(f"Confluence v2 API: space key '{space_key}' not found or not accessible")
        space_id = results[0]["id"]
        homepage_id = results[0].get("homepageId")
        self._space_id_cache[space_key] = space_id
        # Lazily init the homepage cache the first time this method runs.
        if not hasattr(self, "_space_homepage_cache"):
            self._space_homepage_cache = {}
        self._space_homepage_cache[space_key] = homepage_id
        logger.info(
            f"[v2] resolved space_key='{space_key}' -> space_id={space_id} "
            f"homepageId={homepage_id}"
        )
        return space_id

    def get_space_pages(
        self,
        space_key: str,
        max_pages: Optional[int] = None,
        with_body: bool = False,
    ) -> List[Dict]:
        """
        Fetch ALL pages from a Confluence space via the Confluence Cloud v2 API.

        Why v2 instead of v1:
          - The v1 endpoint /rest/api/space/{key}/content/page does NOT return pages
            that live inside Confluence Folders (the content type Atlassian introduced
            in 2024). For real customer spaces this can hide ~10-15% of content.
          - The v2 endpoint /api/v2/spaces/{id}/pages returns every page in the space
            regardless of folder nesting, with proper cursor-based pagination.
          - On the Digital Payments space, v1 returned 2,299 pages, v2 returned 2,601
            (302 / 11.6% folder-nested and previously invisible to RAG).

        Args:
            space_key:  Confluence space key (e.g. 'APP'). Internally resolved to
                        a numeric space ID via the v2 /spaces lookup.
            max_pages:  Optional ceiling on total pages returned. None = fetch all.
            with_body:  When True, request body inline via `?body-format=storage`
                        so the caller does NOT need a per-page detail fetch later.
                        Confluence caps body-inclusive batches at 25 (vs 250
                        without body), so list-call count grows but total HTTP
                        traffic drops drastically. For the 2,601-page Digital
                        Payments sync: 11 list + 2,601 detail = 2,612 calls
                        becomes 105 list + 0 detail = 105 calls (~25x fewer
                        round trips). The body lives at returned dict path
                        `page['body']['storage']['value']` to match the v1
                        shape downstream callers already consume.

        Returns:
            List of normalised page dicts. Shape:
                id, title, status, parentId, parentType, spaceId,
                version.{number, when}, _links.webui,
                body.storage.value   (only when with_body=True)
        """
        try:
            space_id = self._resolve_space_id(space_key)

            if with_body:
                # body-format=storage caps per-request limit to 25 in v2.
                page_size = 25
                next_url = f"{self.v2_base}/spaces/{space_id}/pages?limit={page_size}&body-format=storage"
            else:
                # Metadata-only — bigger batches are fine, fewer round trips.
                page_size = 250
                next_url = f"{self.v2_base}/spaces/{space_id}/pages?limit={page_size}"

            all_pages: List[Dict] = []
            batch_num = 0

            while next_url:
                batch_num += 1
                response = requests.get(
                    next_url,
                    headers={"Accept": "application/json"},
                    auth=self.auth, timeout=60 if with_body else 30,
                )
                response.raise_for_status()
                payload = response.json()
                results = payload.get("results", [])

                for p in results:
                    # Normalise v2 -> v1-like dict that downstream code already consumes.
                    version = p.get("version") or {}
                    links = p.get("_links") or {}
                    normalised: Dict = {
                        "id": p.get("id"),
                        "title": p.get("title"),
                        "status": p.get("status"),
                        "parentId": p.get("parentId"),
                        "parentType": p.get("parentType"),
                        "spaceId": p.get("spaceId"),
                        "version": {
                            "number": version.get("number"),
                            "when": version.get("createdAt"),   # v2 'createdAt' -> v1 'when'
                        },
                        "_links": {
                            "webui": links.get("webui", ""),
                        },
                    }
                    if with_body:
                        # v2 body shape: body.storage.value. Mirror v1 exactly so
                        # callers can use page['body']['storage']['value'] either way.
                        body_obj = p.get("body") or {}
                        storage = body_obj.get("storage") or {}
                        normalised["body"] = {
                            "storage": {
                                "value": storage.get("value", "") or "",
                                "representation": storage.get("representation", "storage"),
                            }
                        }
                    all_pages.append(normalised)

                logger.info(
                    f"[get_space_pages v2{' +body' if with_body else ''}] "
                    f"space={space_key} batch {batch_num}: {len(results)} pages "
                    f"(running total={len(all_pages)})"
                )

                if max_pages is not None and len(all_pages) >= max_pages:
                    all_pages = all_pages[:max_pages]
                    break

                # Cursor pagination: _links.next is /wiki/api/v2/.../pages?cursor=...
                next_rel = (payload.get("_links") or {}).get("next")
                if next_rel:
                    next_url = f"https://{self.domain}{next_rel}" if next_rel.startswith("/") else next_rel
                else:
                    next_url = None

            logger.info(
                f"[get_space_pages v2] space={space_key} TOTAL pages fetched: "
                f"{len(all_pages)}{' (with body)' if with_body else ''}"
            )
            return all_pages

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence pages (v2): {e}")
            raise Exception(f"Failed to fetch Confluence pages: {str(e)}")

    def get_space_tree(self, space_key: str) -> Dict:
        """Fetch every page AND every folder in a Confluence space and build a
        tree that mirrors the live Confluence hierarchy.

        Returns:
            {
                "tree":   [<top-level items>],   # nested children populated
                "flat":   [<every page>],         # full list for search/index
                "stats":  {
                    "pages": int,
                    "folders": int,
                    "top_level": int,
                    "max_depth": int,
                    "orphaned": int,             # parent ID we never saw
                    "list_calls": int,           # HTTP round-trips made
                },
            }

        Every node is a dict shaped like:
            {
                "type":       "page" | "folder",
                "id":         "<numeric id as str>",
                "title":      "...",
                "parentId":   "..." | None,
                "parentType": "space" | "page" | "folder" | None,
                "spaceId":    "...",
                "version":    {"number": int, "when": iso8601 str | None},
                "_links":     {"webui": "..."},
                "children":   [<nested nodes in title order>],
            }

        Implementation notes:
          - Uses v2 endpoints so folder-nested pages are included (v1 hides
            them since Atlassian added Folders as a content type in 2024).
          - Paginates exhaustively via _links.next cursor — NO 200-page cap,
            NO max_results clamp. Every batch is fully drained.
          - Pages come from /spaces/{id}/pages, folders from /folders?
            space-id=... (v2's folder listing endpoint).
          - Hierarchy is built in-memory once both lists are in hand. Items
            whose parent isn't in the by_id dict are still surfaced under
            "tree" at the top level AND counted in stats.orphaned so the
            caller can see if the API returned an inconsistent view.
          - Per-step logging gives the caller (and CloudWatch) proof that
            every batch was drained.
        """
        space_id = self._resolve_space_id(space_key)
        homepage_id = getattr(self, "_space_homepage_cache", {}).get(space_key)
        list_calls = 0

        # ── Step 1: fetch ALL pages ─────────────────────────────────────
        # No body, metadata only — that lets us use the 250/batch limit so
        # we drain large spaces in the fewest possible HTTP calls.
        # We explicitly skip the space's homepage (the auto-generated
        # space-overview page with "Recently updated content" + "Contributors"
        # macros). It's not user-authored content; surfacing it just clutters
        # the picker.
        pages_by_id: Dict[str, Dict] = {}
        homepage_dropped = False
        next_url = f"{self.v2_base}/spaces/{space_id}/pages?limit=250"
        batch_num = 0
        while next_url:
            batch_num += 1
            list_calls += 1
            # _get_with_retry survives the occasional Atlassian TLS-edge drop
            # mid-pagination. Without it a flake on batch N abandons every
            # batch < N already fetched.
            resp = self._get_with_retry(
                next_url,
                headers={"Accept": "application/json"},
                auth=self.auth, timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("results", []) or []
            for p in batch:
                pid = p.get("id")
                if not pid:
                    continue
                if homepage_id and str(pid) == str(homepage_id):
                    homepage_dropped = True
                    continue
                pages_by_id[pid] = self._normalise_v2_item(p, item_type="page")
            logger.info(
                f"[get_space_tree] space={space_key} pages batch {batch_num}: "
                f"{len(batch)} (running total={len(pages_by_id)})"
            )
            next_rel = (payload.get("_links") or {}).get("next")
            next_url = (
                f"https://{self.domain}{next_rel}"
                if next_rel and next_rel.startswith("/") else next_rel
            )
        logger.info(
            f"[get_space_tree] space={space_key} PAGES PHASE DONE — "
            f"{len(pages_by_id)} pages across {batch_num} batches "
            f"(homepage_excluded={homepage_dropped})"
        )

        # ── Step 2: fetch ALL folders ───────────────────────────────────
        # Folders are a separate v2 content type. The endpoint is
        # /api/v2/folders?space-id=<id>. Atlassian started exposing this in
        # 2024; older Confluence tenants may 404 here — treat that as
        # "no folders" rather than a hard error.
        folders_by_id: Dict[str, Dict] = {}
        next_url = f"{self.v2_base}/folders?space-id={space_id}&limit=250"
        batch_num = 0
        folders_unsupported = False
        while next_url:
            batch_num += 1
            list_calls += 1
            try:
                resp = self._get_with_retry(
                    next_url,
                    headers={"Accept": "application/json"},
                    auth=self.auth, timeout=30,
                )
                if resp.status_code == 404:
                    folders_unsupported = True
                    logger.info(
                        f"[get_space_tree] space={space_key} folders endpoint 404 — "
                        f"tenant doesn't support v2 folders; treating as 0 folders"
                    )
                    break
                resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.warning(
                    f"[get_space_tree] space={space_key} folders fetch failed "
                    f"(continuing with pages only): {e}"
                )
                break
            payload = resp.json()
            batch = payload.get("results", []) or []
            for f in batch:
                fid = f.get("id")
                if not fid:
                    continue
                folders_by_id[fid] = self._normalise_v2_item(f, item_type="folder")
            logger.info(
                f"[get_space_tree] space={space_key} folders batch {batch_num}: "
                f"{len(batch)} (running total={len(folders_by_id)})"
            )
            next_rel = (payload.get("_links") or {}).get("next")
            next_url = (
                f"https://{self.domain}{next_rel}"
                if next_rel and next_rel.startswith("/") else next_rel
            )
        if not folders_unsupported:
            logger.info(
                f"[get_space_tree] space={space_key} FOLDERS PHASE DONE — "
                f"{len(folders_by_id)} folders across {batch_num} batches"
            )

        # ── Step 3: build the tree ──────────────────────────────────────
        # Walk all items, attach each as a child of its parent. Items whose
        # parent isn't in the by_id dict (either parentType=space or a
        # genuinely orphaned record) become top-level.
        by_id: Dict[str, Dict] = {}
        by_id.update(folders_by_id)
        by_id.update(pages_by_id)

        for item in by_id.values():
            item["children"] = []

        tree: List[Dict] = []
        orphaned = 0
        for item in by_id.values():
            parent_id = item.get("parentId")
            parent_type = item.get("parentType")
            if parent_id and parent_id in by_id and parent_type in ("page", "folder"):
                by_id[parent_id]["children"].append(item)
            elif parent_type == "space" or not parent_id:
                tree.append(item)
            else:
                # parentId references something we don't have (e.g., a
                # restricted page the user can't read). Surface it at top
                # level so it's not hidden, and count it for the caller.
                tree.append(item)
                orphaned += 1

        # ── Step 4: sort each level by title (case-insensitive) ─────────
        def _sort_level(nodes: List[Dict]) -> None:
            nodes.sort(key=lambda n: (n.get("title") or "").lower())
            for n in nodes:
                if n.get("children"):
                    _sort_level(n["children"])
        _sort_level(tree)

        # ── Step 5: compute depth for the stats line ────────────────────
        def _max_depth(nodes: List[Dict], depth: int = 1) -> int:
            if not nodes:
                return depth - 1
            return max(
                (_max_depth(n.get("children") or [], depth + 1) for n in nodes),
                default=depth,
            )
        max_depth = _max_depth(tree)

        stats = {
            "pages":     len(pages_by_id),
            "folders":   len(folders_by_id),
            "top_level": len(tree),
            "max_depth": max_depth,
            "orphaned":  orphaned,
            "list_calls": list_calls,
        }
        logger.info(
            f"[get_space_tree] space={space_key} TREE BUILT — "
            f"pages={stats['pages']} folders={stats['folders']} "
            f"top_level={stats['top_level']} max_depth={max_depth} "
            f"orphaned={orphaned} http_calls={list_calls}"
        )

        return {
            "tree":  tree,
            "flat":  list(pages_by_id.values()),
            "stats": stats,
        }

    def _normalise_v2_item(self, item: Dict, item_type: str) -> Dict:
        """Shape a raw v2 page/folder dict into the common node shape used
        by get_space_tree. Folders don't have version/body so version is
        populated only for pages."""
        version = item.get("version") or {}
        links = item.get("_links") or {}
        return {
            "type":       item_type,
            "id":         item.get("id"),
            "title":      item.get("title") or "",
            "parentId":   item.get("parentId"),
            "parentType": item.get("parentType"),
            "spaceId":    item.get("spaceId"),
            "status":     item.get("status"),
            "version": {
                "number": version.get("number"),
                "when":   version.get("createdAt"),
            } if item_type == "page" else None,
            "_links": {
                "webui": links.get("webui", ""),
            },
        }

    def get_all_pages_in_space(self, space_key: str) -> List[Dict]:
        """Exhaustively fetch every page in a Confluence space, newest first.

        Replaces the legacy `get_space_pages(space_key, 1000)` sync path which:
          - made a single non-paginated REST call (effective ceiling ~100 pages)
          - returned oldest-first (Confluence API default)
          - did NOT traverse child pages nested under a parent

        Two-phase fetch so nothing slips through:
          1. CQL search over the space ordered by lastmodified DESC. This is
             flat across the space and includes nested pages, but we treat
             it as the *primary* enumeration only.
          2. For every page surfaced in phase 1, list its child pages via
             /content/{id}/child/page. Deduplicated by id. This belt-and-
             braces step catches any descendants the CQL index might have
             missed (eventual-consistency lag on freshly-published pages).

        No upper bound on total — paginates until each endpoint reports
        an empty batch. Caller is responsible for handling space-level scale
        (sync_service streams pages, doesn't materialise everything at once).
        """
        # ---- Phase 1: CQL enumeration, newest first ----
        by_id: Dict[str, Dict] = {}
        try:
            search_url = f"{self.base_url}/rest/api/content/search"
            start = 0
            page_size = 50  # Confluence Cloud hard cap on /search
            cql = f'space = "{space_key}" AND type = page ORDER BY lastmodified DESC'
            while True:
                params = {
                    "cql": cql,
                    "limit": page_size,
                    "start": start,
                    "expand": "version,ancestors",
                }
                response = requests.get(
                    search_url, headers=self.headers, auth=self.auth, params=params, timeout=30
                )
                response.raise_for_status()
                data = response.json()
                batch = data.get("results", [])
                for p in batch:
                    pid = p.get("id")
                    if pid and pid not in by_id:
                        by_id[pid] = p
                if len(batch) < page_size:
                    break
                start += page_size
            phase1_count = len(by_id)
            logger.info(
                f"[Confluence] space={space_key} phase1 cql_pages={phase1_count} "
                f"(newest-first, fully paginated)"
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"[Confluence] CQL enumeration failed for space {space_key}: {e}")
            raise Exception(f"Failed to enumerate Confluence space {space_key}: {str(e)}")

        # ---- Phase 2: recursive child traversal, dedup by id ----
        # Walk every page found in phase 1 and pull its children. New pages
        # discovered are themselves walked. Capped only by what actually
        # exists (no artificial ceiling).
        descendants_added = 0
        try:
            queue = list(by_id.keys())
            visited: set = set()
            while queue:
                parent_id = queue.pop()
                if parent_id in visited:
                    continue
                visited.add(parent_id)
                child_url = f"{self.base_url}/rest/api/content/{parent_id}/child/page"
                start = 0
                page_size = 50
                while True:
                    params = {
                        "limit": page_size,
                        "start": start,
                        "expand": "version,ancestors",
                    }
                    try:
                        resp = requests.get(
                            child_url, headers=self.headers, auth=self.auth, params=params, timeout=30
                        )
                        resp.raise_for_status()
                    except requests.exceptions.RequestException as e:
                        # One child-fetch failing must not abort the whole sync —
                        # log it and continue with what we have.
                        logger.warning(
                            f"[Confluence] child fetch failed for parent {parent_id}: {e}"
                        )
                        break
                    data = resp.json()
                    batch = data.get("results", [])
                    for p in batch:
                        pid = p.get("id")
                        if pid and pid not in by_id:
                            by_id[pid] = p
                            queue.append(pid)
                            descendants_added += 1
                    if len(batch) < page_size:
                        break
                    start += page_size
        except Exception as e:
            # Defensive — phase 1 result is still usable. Don't lose work.
            logger.warning(f"[Confluence] descendant traversal aborted early: {e}")

        # Final ordering: newest-first by version.when (already implied by CQL
        # but the descendant traversal re-orders by walk). Sort once at the end.
        all_pages = list(by_id.values())
        all_pages.sort(
            key=lambda p: (
                (p.get("version", {}) or {}).get("when")
                or (p.get("history", {}) or {}).get("createdDate")
                or ""
            ),
            reverse=True,
        )
        logger.info(
            f"[Confluence] space={space_key} sync_complete "
            f"top_level_or_cql={phase1_count} descendants_added={descendants_added} "
            f"total={len(all_pages)}"
        )
        return all_pages

    def get_content_pages(self, space_key: str, limit: int = 50, max_pages: int = 200) -> List[Dict]:
        """
        Fetch pages from a space using Content API, paginating automatically up to max_pages.
        Uses minimal expand fields to keep responses fast.
        """
        try:
            url = f"{self.base_url}/rest/api/content"
            all_pages = []
            start = 0
            batch_size = min(limit, 50)  # Confluence Cloud hard cap is 50
            while len(all_pages) < max_pages:
                params = {
                    "spaceKey": space_key,
                    "type": "page",
                    "limit": batch_size,
                    "start": start,
                    "expand": "version,ancestors"  # ancestors gives parent-child hierarchy
                }
                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                batch = data.get("results", [])
                all_pages.extend(batch)
                if len(batch) < batch_size:
                    break
                start += batch_size
            logger.info(f"Fetched {len(all_pages)} pages from space {space_key}")
            return all_pages
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence content pages: {e}")
            raise Exception(f"Failed to fetch Confluence pages: {str(e)}")

    def search_pages_by_title_prefix(self, space_key: str, title_prefix: str) -> List[Dict]:
        """
        Find all pages in a space whose title contains title_prefix.
        Uses two separate word tokens in CQL for maximum compatibility,
        then filters in Python to ensure the prefix is actually present.
        """
        try:
            url = f"{self.base_url}/rest/api/content/search"
            all_results = []
            start = 0
            limit = 50
            # Split prefix into individual words so CQL tokenisation can't miss it
            words = title_prefix.split()
            word_clauses = " AND ".join(f'title ~ "{w}"' for w in words)
            cql = f'space = "{space_key}" AND ({word_clauses}) AND type = page'
            while True:
                params = {
                    "cql": cql,
                    "limit": limit,
                    "start": start,
                    "expand": "version,_links"
                }
                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                batch = data.get("results", [])
                # Keep only pages whose title actually contains the full prefix string
                filtered = [p for p in batch if title_prefix.lower() in p["title"].lower()]
                all_results.extend(filtered)
                if len(batch) < limit:
                    break
                start += limit
            logger.info(f"CQL search found {len(all_results)} pages containing '{title_prefix}' in space {space_key}")
            return all_results
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching Confluence pages by title prefix: {e}")
            raise Exception(f"Failed to search Confluence pages: {str(e)}")

    def get_content_page_by_id(self, page_id: str, expand: str = "body.storage,version,ancestors") -> Dict:
        """
        Get a single page by ID with optional expand (same shape as Confluence REST API).
        """
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            params = {"expand": expand}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence page {page_id}: {e}")
            raise Exception(f"Failed to fetch Confluence page: {str(e)}")

    def convert_brd_to_confluence_storage(self, brd_data: Dict) -> str:
        """
        Convert BRD JSON structure to Confluence storage format (HTML-like)
        
        Args:
            brd_data: BRD data with sections structure
            
        Returns:
            Confluence storage format HTML string
        """
        html_parts = []
        
        sections = brd_data.get("sections", [])
        
        for section in sections:
            title = html.escape(section.get("title", ""))
            # Add section title as h2
            html_parts.append(f"<h2>{title}</h2>")
            
            # Process content blocks
            for block in section.get("content", []):
                block_type = block.get("type")
                
                if block_type == "paragraph":
                    text = html.escape(block.get("text", ""))
                    # Replace newlines with <br/> for proper formatting
                    text = text.replace("\n", "<br/>")
                    html_parts.append(f"<p>{text}</p>")
                
                elif block_type == "bullet":
                    items = block.get("items", [])
                    if items:
                        html_parts.append("<ul>")
                        for item in items:
                            escaped_item = html.escape(str(item))
                            html_parts.append(f"<li>{escaped_item}</li>")
                        html_parts.append("</ul>")
                
                elif block_type == "table":
                    rows = block.get("rows", [])
                    if rows:
                        html_parts.append("<table><tbody>")
                        for row_idx, row in enumerate(rows):
                            html_parts.append("<tr>")
                            # First row is header
                            tag = "th" if row_idx == 0 else "td"
                            for cell in row:
                                escaped_cell = html.escape(str(cell))
                                html_parts.append(f"<{tag}>{escaped_cell}</{tag}>")
                            html_parts.append("</tr>")
                        html_parts.append("</tbody></table>")
        
        return "".join(html_parts)
    
    def convert_sad_to_confluence_storage(
        self,
        sad_data: Dict,
        diagram_filenames: Optional[Dict[str, str]] = None,
    ) -> str:
        """Convert SAD `sad_structure.json` into Confluence storage XHTML.

        Shape of `sad_data` mirrors the section list rendered by routers/sad.py:
        each section has `number`, `title`, and a `content` list of typed
        blocks (paragraph, heading, ordered_list, bullet_list, table, diagram).

        `diagram_filenames` maps a block's `s3_key` to the attachment filename
        the caller will upload to the Confluence page (via `create_attachment`).
        Diagram blocks whose s3_key is missing from the map are emitted as a
        placeholder paragraph so the page never silently drops a section.
        """
        diagram_filenames = diagram_filenames or {}
        out: List[str] = []

        for section in sad_data.get("sections", []):
            number = section.get("number")
            title = html.escape(section.get("title", ""))
            heading_text = f"{number}. {title}" if number is not None else title
            out.append(f"<h2>{heading_text}</h2>")

            for block in section.get("content", []) or []:
                btype = block.get("type")

                if btype == "paragraph":
                    text = html.escape(block.get("text", "")).replace("\n", "<br/>")
                    out.append(f"<p>{text}</p>")

                elif btype == "heading":
                    level = max(3, min(int(block.get("level", 3)), 6))
                    text = html.escape(block.get("text", ""))
                    out.append(f"<h{level}>{text}</h{level}>")

                elif btype == "ordered_list":
                    items = block.get("items", []) or []
                    if items:
                        out.append("<ol>")
                        for it in items:
                            out.append(f"<li>{html.escape(str(it))}</li>")
                        out.append("</ol>")

                elif btype == "bullet_list":
                    items = block.get("items", []) or []
                    if items:
                        out.append("<ul>")
                        for it in items:
                            out.append(f"<li>{html.escape(str(it))}</li>")
                        out.append("</ul>")

                elif btype == "table":
                    headers = block.get("headers", []) or []
                    rows = block.get("rows", []) or []
                    if headers or rows:
                        out.append("<table><tbody>")
                        if headers:
                            out.append("<tr>")
                            for h in headers:
                                out.append(f"<th>{html.escape(str(h))}</th>")
                            out.append("</tr>")
                        col_count = len(headers) if headers else (len(rows[0]) if rows else 0)
                        for row in rows:
                            out.append("<tr>")
                            for cell in (row[:col_count] if col_count else row):
                                out.append(f"<td>{html.escape(str(cell))}</td>")
                            out.append("</tr>")
                        out.append("</tbody></table>")

                elif btype == "diagram":
                    s3_key = block.get("s3_key", "")
                    filename = diagram_filenames.get(s3_key)
                    if filename:
                        alt = html.escape(block.get("alt", "Architecture diagram"))
                        out.append(
                            f'<ac:image ac:alt="{alt}"><ri:attachment ri:filename="{html.escape(filename)}"/></ac:image>'
                        )
                    else:
                        out.append(
                            "<p><em>[Diagram unavailable — re-export the SAD to refresh.]</em></p>"
                        )

        return "".join(out)

    def create_attachment(
        self,
        page_id: str,
        filename: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> Dict:
        """Attach a file to an existing Confluence page.

        Uses the v1 /rest/api/content/{id}/child/attachment endpoint with the
        `X-Atlassian-Token: no-check` header Confluence requires for multipart
        uploads. Returns the first result entry (Confluence returns a list).
        """
        url = f"{self.base_url}/rest/api/content/{page_id}/child/attachment"
        files = {"file": (filename, file_bytes, content_type)}
        headers = {
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
        }
        try:
            response = requests.post(
                url, files=files, headers=headers, auth=self.auth, timeout=60
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [data]) if isinstance(data, dict) else data
            attachment = results[0] if isinstance(results, list) and results else {}
            logger.info(
                f"Attached {filename} ({len(file_bytes)} bytes) to page {page_id}"
            )
            return attachment
        except requests.exceptions.RequestException as e:
            logger.error(f"Error attaching {filename} to page {page_id}: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to attach file to Confluence page: {str(e)}")

    def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: Optional[str] = None
    ) -> Dict:
        """
        Create a new Confluence page
        
        Args:
            space_key: Confluence space key
            title: Page title
            content: Page content in Confluence storage format (HTML)
            parent_id: Optional parent page ID
            
        Returns:
            Created page data with id, title, and web URL
        """
        try:
            url = f"{self.base_url}/rest/api/content"
            
            payload = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {
                    "storage": {
                        "value": content,
                        "representation": "storage"
                    }
                }
            }
            
            # Add parent if specified
            if parent_id:
                payload["ancestors"] = [{"id": parent_id}]
            
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                auth=self.auth,
                timeout=30
            )
            response.raise_for_status()
            
            page_data = response.json()
            
            # Extract useful information
            result = {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "type": page_data.get("type"),
                "status": page_data.get("status"),
                "web_url": f"{self.base_url}{page_data.get('_links', {}).get('webui', '')}"
            }
            
            logger.info(f"Created Confluence page: {result['title']} (ID: {result['id']})")
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Confluence page: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to create Confluence page: {str(e)}")
    
    def find_page_by_title(self, space_key: str, title: str) -> Optional[Dict]:
        """Find a page in a space by exact title. Returns None if not found."""
        try:
            url = f"{self.base_url}/rest/api/content"
            params = {"spaceKey": space_key, "title": title, "type": "page", "expand": "version"}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
            response.raise_for_status()
            results = response.json().get("results", [])
            return results[0] if results else None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching Confluence page by title: {e}")
            return None

    def update_page(self, page_id: str, title: str, content: str, current_version: int) -> Dict:
        """Update an existing Confluence page — saves as a new version."""
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            payload = {
                "type": "page",
                "title": title,
                "version": {"number": current_version + 1},
                "body": {
                    "storage": {
                        "value": content,
                        "representation": "storage"
                    }
                }
            }
            response = requests.put(url, json=payload, headers=self.headers, auth=self.auth, timeout=30)
            response.raise_for_status()
            page_data = response.json()
            return {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "web_url": f"{self.base_url}{page_data.get('_links', {}).get('webui', '')}"
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Error updating Confluence page: {e}")
            raise Exception(f"Failed to update Confluence page: {str(e)}")

    def get_page_version(self, page_id: str, version_number: int) -> Dict:
        """
        Fetch a specific historical version of a Confluence page.
        Used by Jira Sync to retrieve the "before" body of a BRD page so
        the Diff agent can compare it against the current body.

        Returns:
            {id, title, content, version} — same shape as get_page_content,
            but content reflects the requested historical version.
        """
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            params = {
                "expand": "body.storage,version",
                "version": str(version_number),
            }
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                auth=self.auth,
                timeout=30,
            )
            response.raise_for_status()
            page_data = response.json()
            return {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "type": page_data.get("type"),
                "content": page_data.get("body", {}).get("storage", {}).get("value", ""),
                "version": page_data.get("version", {}).get("number", version_number),
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence page {page_id} version {version_number}: {e}")
            raise Exception(f"Failed to fetch Confluence page version: {str(e)}")

    def get_page_content(self, page_id: str) -> Dict:
        """
        Get full content of a Confluence page by ID

        Args:
            page_id: Confluence page ID

        Returns:
            Page data with title, content, and metadata
        """
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            params = {
                "expand": "body.storage,version"
            }
            
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                auth=self.auth,
                timeout=30
            )
            response.raise_for_status()
            
            page_data = response.json()
            
            # Extract useful information
            result = {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "type": page_data.get("type"),
                "content": page_data.get("body", {}).get("storage", {}).get("value", ""),
                "version": page_data.get("version", {}).get("number", 1)
            }
            
            logger.info(f"Fetched Confluence page: {result['title']} (ID: {result['id']})")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence page content: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to fetch Confluence page: {str(e)}")

    # ------------------------------------------------------------------
    # Code Documentation helpers
    # ------------------------------------------------------------------

    def apply_label(self, page_id: str, label: str) -> bool:
        """
        Attach a label to a Confluence page. Returns True on success.
        Cloud REST uses POST /rest/api/content/{id}/label with a list payload.
        """
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}/label"
            payload = [{"prefix": "global", "name": label}]
            response = requests.post(url, json=payload, headers=self.headers, auth=self.auth, timeout=30)
            response.raise_for_status()
            logger.info(f"Applied label '{label}' to page {page_id}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error applying label '{label}' to page {page_id}: {e}")
            return False

    def search_pages_by_label(self, space_key: str, label: str, limit: int = 50) -> List[Dict]:
        """
        Return pages in a space tagged with a label, newest first.
        """
        try:
            url = f"{self.base_url}/rest/api/content/search"
            cql = f'space = "{space_key}" AND label = "{label}" AND type = page ORDER BY created DESC'
            all_results: List[Dict] = []
            start = 0
            page_size = min(limit, 50)
            while len(all_results) < limit:
                params = {
                    "cql": cql,
                    "limit": page_size,
                    "start": start,
                    "expand": "version,metadata.labels,history"
                }
                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                batch = data.get("results", [])
                all_results.extend(batch)
                if len(batch) < page_size:
                    break
                start += page_size
            return all_results[:limit]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching pages by label '{label}' in space {space_key}: {e}")
            raise Exception(f"Failed to search Confluence pages by label: {str(e)}")

    def list_children_with_label(self, parent_id: str, label: str, limit: int = 1000) -> List[Dict]:
        """
        List child pages of `parent_id` that carry `label`, newest first.
        Uses the content tree (instant), not CQL search (eventually consistent),
        so freshly-published pages appear without indexing delay.

        The default `limit` is now 1000 (previously 50). The function already
        paginates the underlying API call correctly; the limit only caps the
        final filtered+sorted output. Folders with >50 labelled children were
        being silently truncated to the first 50 by the old default.
        """
        try:
            url = f"{self.base_url}/rest/api/content/{parent_id}/child/page"
            all_children: List[Dict] = []
            start = 0
            page_size = 50
            while True:
                params = {
                    "limit": page_size,
                    "start": start,
                    "expand": "version,metadata.labels,history",
                }
                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                batch = data.get("results", [])
                all_children.extend(batch)
                if len(batch) < page_size:
                    break
                start += page_size

            filtered = [
                p for p in all_children
                if any(
                    (lbl.get("name") == label)
                    for lbl in (p.get("metadata", {}).get("labels", {}) or {}).get("results", [])
                )
            ]
            filtered.sort(
                key=lambda p: (p.get("history", {}) or {}).get("createdDate")
                or (p.get("version", {}) or {}).get("when")
                or "",
                reverse=True,
            )
            return filtered[:limit]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error listing children of {parent_id} with label '{label}': {e}")
            raise Exception(f"Failed to list child pages by label: {str(e)}")

    def find_or_create_page(self, space_key: str, title: str, default_content: str = "") -> Dict:
        """
        Find a page by exact title in a space; if missing, create at space root.
        Used to ensure a 'Code Documentation' parent page exists.
        """
        existing = self.find_page_by_title(space_key, title)
        if existing:
            return {
                "id": existing.get("id"),
                "title": existing.get("title"),
                "web_url": f"{self.base_url}{existing.get('_links', {}).get('webui', '')}",
                "created": False,
            }
        body = default_content or (
            "<p>This page collects Code Documentation pages published from the IDE via the "
            "<strong>code-documentation</strong> MCP. Each child page is one document describing "
            "the current state of the codebase at a given commit.</p>"
        )
        created = self.create_page(space_key, title, body)
        created["created"] = True
        return created

    def markdown_to_storage(self, markdown: str) -> str:
        """
        Convert a markdown subset to Confluence storage format.

        Supported: ATX headings (# / ## / ###), unordered lists (- / *),
        **bold**, inline `code`, ```fenced code blocks```, blank-line paragraphs.
        Anything else is escaped and emitted as paragraph text — good enough for
        the locked-shape Code Documentation template.
        """
        import re as _re

        lines = markdown.split("\n")
        out: List[str] = []
        i = 0
        in_list = False

        def close_list():
            nonlocal in_list
            if in_list:
                out.append("</ul>")
                in_list = False

        def inline(text: str) -> str:
            escaped = html.escape(text)
            escaped = _re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
            escaped = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
            return escaped

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("```"):
                lang = stripped[3:].strip()
                close_list()
                code_lines: List[str] = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                code_text = "\n".join(code_lines)
                lang_param = (
                    f'<ac:parameter ac:name="language">{html.escape(lang)}</ac:parameter>'
                    if lang else ""
                )
                out.append(
                    '<ac:structured-macro ac:name="code">'
                    f'{lang_param}'
                    f'<ac:plain-text-body><![CDATA[{code_text}]]></ac:plain-text-body>'
                    '</ac:structured-macro>'
                )
                i += 1
                continue

            m = _re.match(r"^(#{1,3})\s+(.*)$", stripped)
            if m:
                close_list()
                level = len(m.group(1))
                out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
                i += 1
                continue

            if _re.match(r"^[-*]\s+", stripped):
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                content = _re.sub(r"^[-*]\s+", "", stripped)
                out.append(f"<li>{inline(content)}</li>")
                i += 1
                continue

            if not stripped:
                close_list()
                i += 1
                continue

            close_list()
            para_lines = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    break
                if (_re.match(r"^(#{1,3})\s+", nxt) or _re.match(r"^[-*]\s+", nxt)
                        or nxt.startswith("```")):
                    break
                para_lines.append(lines[i])
                i += 1
            paragraph = inline("\n".join(para_lines).strip()).replace("\n", "<br/>")
            out.append(f"<p>{paragraph}</p>")

        close_list()
        return "".join(out)

    def build_code_summary_info_panel(self, project_id: str, commit_sha: str, scope: str) -> str:
        """
        Return a Confluence 'info' panel macro to prepend to a code documentation page,
        so anyone landing on the page out of context understands what it is.
        """
        body = (
            f"<p><strong>Auto-generated code documentation.</strong> "
            f"Project: <code>{html.escape(project_id)}</code> &middot; "
            f"Scope: <code>{html.escape(scope)}</code> &middot; "
            f"Commit: <code>{html.escape(commit_sha)}</code></p>"
            "<p>Published from the IDE via the <strong>code-documentation</strong> MCP. "
            "Source of truth for what the code currently does; do not edit by hand.</p>"
        )
        return (
            '<ac:structured-macro ac:name="info">'
            f'<ac:rich-text-body>{body}</ac:rich-text-body>'
            '</ac:structured-macro>'
        )
