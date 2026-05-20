"""
Sync Service - Synchronize Confluence and Jira data with vector database
Handles initial sync and incremental updates
"""
 
import asyncio
import hashlib
from typing import List, Dict, Optional
from datetime import datetime
from services.embedding_service import embedding_service
from services.rag_service import _strip_html
from services.confluence_service import ConfluenceService
from services.jira_service import JiraService
from db_helper import get_project, get_user_atlassian_credentials
from db_helper_vector import (
    upsert_confluence_page,
    upsert_jira_issue,
    get_confluence_page,
    get_jira_issue,
    get_all_confluence_pages_metadata,
    get_all_jira_issues_metadata,
    find_existing_embeddings_bulk,
    replace_document_embeddings_bulk,
)
import logging
 
logger = logging.getLogger(__name__)


def _extract_text_from_adf(adf: dict) -> str:
    """Recursively extract plain text from Jira's Atlassian Document Format (ADF)."""
    parts = []
    if adf.get('type') == 'text':
        parts.append(adf.get('text', ''))
    for child in adf.get('content', []):
        parts.append(_extract_text_from_adf(child))
    return ' '.join(parts).strip()
 
# ============================================
# IN-MEMORY SYNC STATUS TRACKING
# ============================================
_sync_status: Dict[str, Dict] = {}
 
 
def get_sync_progress(project_id: str) -> Optional[Dict]:
    """Get the current sync progress for a project"""
    return _sync_status.get(project_id)
 
 
def _update_sync_status(project_id: str, **kwargs):
    """Update sync status for a project"""
    if project_id not in _sync_status:
        _sync_status[project_id] = {}
    _sync_status[project_id].update(kwargs)
 
 
def _clear_sync_status(project_id: str):
    """Remove sync status for a project"""
    _sync_status.pop(project_id, None)
 
 
async def sync_project(project_id: str, user_id: str, sync_type: str = 'incremental'):
    """
    Sync Confluence and Jira data for a project.
    All blocking I/O (DB, HTTP, embeddings) is offloaded to threads
    so the FastAPI event loop stays responsive for other requests.
 
    Args:
        project_id: Project ID
        user_id: User ID
        sync_type: 'initial' or 'incremental'
    """
    try:
        logger.info(f"Starting {sync_type} sync for project {project_id}")
        _update_sync_status(project_id, is_syncing=True, started_at=datetime.utcnow().isoformat(), message="Starting sync...")
 
        # Get project details (offload blocking DB call)
        project = await asyncio.to_thread(get_project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
 
        # Get user's Atlassian credentials (offload blocking DB call)
        creds = await asyncio.to_thread(get_user_atlassian_credentials, user_id)
        if not creds:
            raise ValueError(f"No Atlassian credentials found for user {user_id}")
 
        # Sync Confluence if configured
        if project.get('confluence_space_key'):
            logger.info(f"Syncing Confluence space: {project['confluence_space_key']}")
            _update_sync_status(project_id, message=f"Syncing Confluence space: {project['confluence_space_key']}...")
            await sync_confluence_space(
                project_id=project_id,
                user_id=user_id,
                space_key=project['confluence_space_key'],
                credentials=creds,
                sync_type=sync_type
            )
 
        # Sync Jira if configured
        if project.get('jira_project_key'):
            logger.info(f"Syncing Jira project: {project['jira_project_key']}")
            _update_sync_status(project_id, message=f"Syncing Jira project: {project['jira_project_key']}...")
            await sync_jira_project(
                project_id=project_id,
                user_id=user_id,
                project_key=project['jira_project_key'],
                credentials=creds,
                sync_type=sync_type
            )
 
        logger.info(f"Sync completed for project {project_id}")
        _update_sync_status(project_id, is_syncing=False, message="Sync completed")
 
    except Exception as e:
        logger.error(f"Sync failed for project {project_id}: {e}")
        _update_sync_status(project_id, is_syncing=False, message=f"Sync failed: {str(e)}")
        raise
 
 
async def sync_confluence_space(
    project_id: str,
    user_id: str,
    space_key: str,
    credentials: Dict,
    sync_type: str = 'incremental'
):
    """Sync all pages from a Confluence space"""
   
    # Initialize Confluence service with extracted credentials
    confluence = ConfluenceService(
        domain=credentials['atlassian_domain'].replace('https://', '').replace('http://', ''),
        email=credentials['atlassian_email'],
        api_token=credentials['atlassian_api_token']
    )
   
    # Fetch all pages WITH BODY in a single paginated call.
    # See get_space_pages docstring for why this is dramatically cheaper than
    # the previous per-page detail-fetch pattern.
    logger.info(f"Fetching pages from Confluence space {space_key} (body inline)...")
    pages = await asyncio.to_thread(confluence.get_space_pages, space_key, None, True)
    logger.info(f"Found {len(pages)} pages in space {space_key}")

    # Bulk fetch existing metadata in one DB call.
    logger.info(f"[OPTIMIZATION] Bulk-fetching all Confluence page metadata for project {project_id}...")
    local_pages_map = await asyncio.to_thread(get_all_confluence_pages_metadata, project_id)
    logger.info(f"[OPTIMIZATION] Loaded {len(local_pages_map)} existing page records in 1 DB call")

    # ── Filter pages that need updating BEFORE fanning out. Unchanged pages
    # cost zero work in the concurrent loop below.
    pages_to_sync: List[tuple] = []
    skipped_count = 0
    for idx, page in enumerate(pages):
        page_id = page['id']
        title = page.get('title', '')
        version_number = page.get('version', {}).get('number')

        local_page = local_pages_map.get(page_id)
        if not local_page:
            logger.info(f"  New page: {title}")
            pages_to_sync.append((idx, page))
        elif local_page['version_number'] < version_number:
            logger.info(f"  Updated page: {title} (v{local_page['version_number']} -> v{version_number})")
            pages_to_sync.append((idx, page))
        else:
            skipped_count += 1

    logger.info(
        f"[SYNC_PLAN] {len(pages_to_sync)} pages need sync, {skipped_count} unchanged "
        f"(total {len(pages)})"
    )

    # ── Concurrent page processor.
    # The bounded semaphore caps in-flight pages so we don't:
    #   (a) saturate the DB connection pool (50 connections max),
    #   (b) hammer the gateway with so many parallel batch-embed calls that the
    #       provider's throttling kicks in — generate_embedding/batch already
    #       has retry/backoff but it's better not to provoke 429s in the first place.
    sync_concurrency = 8
    semaphore = asyncio.Semaphore(sync_concurrency)
    completed = [0]   # mutable counter shared between coroutines (single-threaded asyncio = safe)

    async def process_one(idx: int, page: Dict):
        async with semaphore:
            try:
                page_id = page['id']
                title = page.get('title', '')
                version_number = page.get('version', {}).get('number')
                page_web_url = f"{confluence.base_url}{page.get('_links', {}).get('webui', '')}"

                raw_html = page.get('body', {}).get('storage', {}).get('value', '') or ''
                content = embedding_service._preprocess_confluence_content(raw_html)

                # 1. Generate + store embeddings (delete-old + bulk-insert-new in 1 txn).
                await generate_and_store_embeddings(
                    project_id=project_id,
                    user_id=user_id,
                    source_type='confluence',
                    source_id=page_id,
                    title=title,
                    content=content,
                    url=page_web_url,
                    source_updated_at=page.get('version', {}).get('when'),
                )

                # 2. Mark the page synced ONLY after embeddings are safely in the vector DB.
                await asyncio.to_thread(
                    upsert_confluence_page,
                    project_id=project_id,
                    user_id=user_id,
                    page_id=page_id,
                    space_key=space_key,
                    title=title,
                    url=page_web_url,
                    version_number=version_number,
                    last_modified_at=page.get('version', {}).get('when'),
                )

                completed[0] += 1
                # Update progress occasionally — every 10 pages avoids hammering
                # the in-memory status dict from 8 concurrent writers.
                if completed[0] % 10 == 0 or completed[0] == len(pages_to_sync):
                    _update_sync_status(
                        project_id,
                        message=f"Syncing Confluence: {completed[0]}/{len(pages_to_sync)} (last: {title})",
                    )
                return True
            except Exception as e:
                logger.error(f"Error syncing page {page.get('title', page.get('id', 'unknown'))}: {e}")
                return False

    # Kick off all pages — semaphore caps concurrency.
    if pages_to_sync:
        results = await asyncio.gather(
            *(process_one(idx, page) for idx, page in pages_to_sync),
            return_exceptions=False,
        )
        synced_count = sum(1 for r in results if r)
    else:
        synced_count = 0

    logger.info(
        f"Confluence sync complete: {synced_count} synced, {skipped_count} skipped, "
        f"concurrency={sync_concurrency}"
    )
 
 
async def sync_jira_project(
    project_id: str,
    user_id: str,
    project_key: str,
    credentials: Dict,
    sync_type: str = 'incremental'
):
    """Sync all issues from a Jira project"""
   
    # Initialize Jira service with extracted credentials
    jira = JiraService(
        domain=credentials['atlassian_domain'].replace('https://', '').replace('http://', ''),
        email=credentials['atlassian_email'],
        api_token=credentials['atlassian_api_token']
    )
   
    # Fetch all issues (offload blocking HTTP call)
    logger.info(f"Fetching issues from Jira project {project_key}...")
    issues = await asyncio.to_thread(jira.get_project_issues, project_key)
    logger.info(f"Found {len(issues)} issues in project {project_key}")
 
    synced_count = 0
    skipped_count = 0

    def parse_atlassian_date(date_str: str) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            # Handle formats like "2024-02-09T10:00:00.000+0000" or "...+00:00" or "...Z"
            normalized = date_str.replace('Z', '+00:00')
            if '+' in normalized and ':' not in normalized.split('+')[-1]:
                # Convert +0000 to +00:00
                tz_part = normalized.split('+')[-1]
                if len(tz_part) == 4:
                    normalized = normalized[:-4] + tz_part[:2] + ':' + tz_part[2:]
            return datetime.fromisoformat(normalized)
        except Exception as e:
            logger.error(f"Error parsing date {date_str}: {e}")
            return None

    # OPTIMIZATION: Bulk fetch all issue metadata in 1 DB call instead of N individual calls
    logger.info(f"[OPTIMIZATION] Bulk-fetching all Jira issue metadata for project {project_id}...")
    local_issues_map = await asyncio.to_thread(get_all_jira_issues_metadata, project_id)
    logger.info(f"[OPTIMIZATION] Loaded {len(local_issues_map)} existing issue records in 1 DB call (saved {len(local_issues_map)} individual DB connections)")

    for idx, issue in enumerate(issues):
        try:
            issue_key = issue['key']
            fields = issue['fields']
            updated_date_str = fields['updated']
            updated_date = parse_atlassian_date(updated_date_str)

            # Check if issue needs update (in-memory lookup, NO DB call)
            local_issue = local_issues_map.get(issue_key)
 
            needs_update = False
            if not local_issue:
                # New issue
                logger.info(f"  New issue: {issue_key}")
                needs_update = True
            else:
                local_updated = local_issue['updated_date']
                # Ensure comparison between timezone-aware datetimes
                if local_updated and updated_date:
                    # If one is naive and other is aware, make both naive for comparison
                    if (local_updated.tzinfo is None) != (updated_date.tzinfo is None):
                        local_comp = local_updated.replace(tzinfo=None)
                        updated_comp = updated_date.replace(tzinfo=None)
                    else:
                        local_comp = local_updated
                        updated_comp = updated_date
 
                    if local_comp < updated_comp:
                        logger.info(f"  Updated issue: {issue_key}")
                        needs_update = True
                else:
                    needs_update = True
 
            if not needs_update:
                # Unchanged issue
                logger.debug(f"  Skipping unchanged issue: {issue_key}")
                skipped_count += 1
                continue
 
            if needs_update:
                _update_sync_status(project_id, message=f"Syncing Jira issue {idx+1}/{len(issues)}: {issue_key}")

                # Calculate actual duration if resolved
                actual_duration_days = None
                if fields.get('resolutiondate') and fields.get('created'):
                    created = parse_atlassian_date(fields['created'])
                    resolved = parse_atlassian_date(fields['resolutiondate'])
                    if created and resolved:
                        actual_duration_days = (resolved - created).days

                clean_domain = credentials['atlassian_domain'].replace('https://', '').replace('http://', '')
                issue_url = f"https://{clean_domain}/browse/{issue_key}"

                # 1. Generate new embeddings FIRST. delete-old + insert-new is done atomically
                #    inside generate_and_store_embeddings, AFTER all chunks have been embedded.
                #    A throttled Bedrock call leaves the existing embeddings in place and
                #    raises here, skipping the upsert below so the next sync retries cleanly.
                raw_desc = fields.get('description', '') or ''
                if isinstance(raw_desc, dict):
                    raw_desc = _extract_text_from_adf(raw_desc)
                content = f"{fields['summary']}\n\n{_strip_html(raw_desc)}"
                await generate_and_store_embeddings(
                    project_id=project_id,
                    user_id=user_id,
                    source_type='jira',
                    source_id=issue_key,
                    title=f"{issue_key}: {fields['summary']}",
                    content=content,
                    url=issue_url,
                    # updated_date_str is Jira's "updated" field — an ISO-8601
                    # string from the Jira API; Postgres converts to TIMESTAMPTZ.
                    source_updated_at=updated_date_str,
                )

                # 2. Only AFTER embeddings are safely in the vector DB do we mark the issue synced.
                await asyncio.to_thread(
                    upsert_jira_issue,
                    project_id=project_id,
                    user_id=user_id,
                    issue_key=issue_key,
                    issue_id=issue['id'],
                    project_key=project_key,
                    summary=fields['summary'],
                    url=issue_url,
                    issue_type=fields.get('issuetype', {}).get('name'),
                    status=fields.get('status', {}).get('name'),
                    priority=fields.get('priority', {}).get('name'),
                    story_points=fields.get('customfield_10016'),
                    original_estimate_seconds=fields.get('timeoriginalestimate'),
                    time_spent_seconds=fields.get('timespent'),
                    remaining_estimate_seconds=fields.get('timeestimate'),
                    sprint_name=None,
                    sprint_id=None,
                    labels=fields.get('labels', []),
                    components=[c['name'] for c in fields.get('components', [])],
                    created_date=fields.get('created'),
                    updated_date=updated_date_str,
                    resolved_date=fields.get('resolutiondate'),
                    actual_duration_days=actual_duration_days,
                )

                synced_count += 1
 
        except Exception as e:
            logger.error(f"Error syncing issue {issue.get('key', 'unknown')}: {e}")
            continue
 
    logger.info(f"Jira sync complete: {synced_count} synced, {skipped_count} skipped")
 
 
async def generate_and_store_embeddings(
    project_id: str,
    user_id: str,
    source_type: str,
    source_id: str,
    title: str,
    content: str,
    url: str,
    source_updated_at: Optional[str] = None,
) -> bool:
    """
    Chunk content, generate embeddings, and store in database.

    SAFETY ORDERING:
      1. Chunk text and compute ALL embeddings into memory FIRST.
         If Bedrock/gateway throttles mid-page or any chunk fails, we raise
         before touching the database. Old embeddings stay intact and the
         caller leaves the metadata row unchanged, so the next sync retries.
      2. Only AFTER all embeddings are generated do we perform the swap:
         delete the old embeddings, then insert the new ones.

    Previous implementation deleted old embeddings BEFORE generation, so any
    mid-loop failure left the page with zero embeddings ("orphans") that
    subsequent syncs skipped because the version_number already matched.

    Args:
        project_id: Project ID
        user_id: User ID
        source_type: 'confluence' or 'jira'
        source_id: Page ID or Issue Key
        title: Document title
        content: Full document content
        url: Document URL

    Returns:
        True if embeddings were generated and stored, False if no chunks
        were produced (e.g. empty content). Raises on any failure.
    """
    try:
        # Chunk the content using LangChain two-stage pipeline
        chunks = embedding_service.chunk_text(
            text=content,
            source_type=source_type,
            page_title=title
        )

        if not chunks:
            logger.warning(f"No chunks generated for {source_type} {source_id}")
            return False

        logger.info(f"  Generated {len(chunks)} chunks for {source_type} {source_id} (LangChain pipeline)")

        # ── PHASE 1: prepare all embeddings in memory ──
        # Any exception here propagates BEFORE we touch document_embeddings, so
        # the page remains searchable via its existing chunks.
        #
        # 1a. ONE DB call to look up all existing embeddings (cross-project dedup).
        existing_map: Dict[int, List[float]] = await asyncio.to_thread(
            find_existing_embeddings_bulk, source_type, source_id, len(chunks),
        )
        reused_count = len(existing_map)

        # 1b. Identify chunks that still need new embeddings.
        to_embed: List[tuple] = [(i, chunk) for i, chunk in enumerate(chunks) if i not in existing_map]

        # 1c. Single batched embedding call (or batched loop on Bedrock fallback)
        #     for all new chunks at once — one HTTP round trip, N embeddings.
        if to_embed:
            texts_to_embed = [c[1] for c in to_embed]
            new_vectors = await asyncio.to_thread(
                embedding_service.generate_embeddings_batch, texts_to_embed,
            )
            for (idx, _chunk_text), vec in zip(to_embed, new_vectors):
                existing_map[idx] = vec

        # 1d. Re-assemble in original chunk order.
        prepared: List[Dict] = [
            {
                'chunk_index': i,
                'chunk': chunk,
                'content_hash': hashlib.sha256(chunk.encode('utf-8')).hexdigest(),
                'embedding': existing_map[i],
                'source_updated_at': source_updated_at,
            }
            for i, chunk in enumerate(chunks)
        ]

        # ── PHASE 2: atomic swap (delete old + bulk insert new) in ONE transaction ──
        # Replaces the previous N-statement-per-chunk loop with a single connection
        # borrow, one DELETE, one execute_values INSERT, and one COMMIT. For an
        # 11-chunk page that's 13 DB borrows → 1.
        await asyncio.to_thread(
            replace_document_embeddings_bulk,
            project_id, user_id, source_type, source_id,
            title, url, prepared,
        )

        if reused_count > 0:
            logger.info(f"  Stored {len(prepared)} embeddings for {source_type} {source_id} (reused {reused_count} from existing projects)")
        else:
            logger.info(f"  Stored {len(prepared)} embeddings for {source_type} {source_id}")

        return True

    except Exception as e:
        logger.error(f"Error generating embeddings for {source_type} {source_id}: {e}")
        raise
 