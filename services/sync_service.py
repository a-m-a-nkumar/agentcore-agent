"""
Sync Service - Synchronize Confluence and Jira data with vector database
Handles initial sync and incremental updates
"""

import asyncio
from typing import List, Dict, Optional
from datetime import datetime
import requests
from services.embedding_service import embedding_service
from services.confluence_service import ConfluenceService
from services.jira_service import JiraService
from db_helper import get_project, get_user_atlassian_credentials
from db_helper_vector import (
    upsert_confluence_page,
    upsert_jira_issue,
    get_confluence_page,
    get_jira_issue,
    insert_document_embedding,
    delete_embeddings
)
import logging

logger = logging.getLogger(__name__)


async def sync_project(project_id: str, user_id: str, sync_type: str = 'incremental'):
    """
    Sync Confluence and Jira data for a project
    
    Args:
        project_id: Project ID
        user_id: User ID
        sync_type: 'initial' or 'incremental'
    """
    try:
        logger.info(f"Starting {sync_type} sync for project {project_id}")
        
        # Get project details
        project = get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        # Get user's Atlassian credentials
        creds = get_user_atlassian_credentials(user_id)
        if not creds:
            raise ValueError(f"No Atlassian credentials found for user {user_id}")
        
        # Sync Confluence if configured
        if project.get('confluence_space_key'):
            logger.info(f"Syncing Confluence space: {project['confluence_space_key']}")
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
            await sync_jira_project(
                project_id=project_id,
                user_id=user_id,
                project_key=project['jira_project_key'],
                credentials=creds,
                sync_type=sync_type
            )
        
        logger.info(f"Sync completed for project {project_id}")
        
    except Exception as e:
        logger.error(f"Sync failed for project {project_id}: {e}")
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
    
    # Fetch all pages
    logger.info(f"Fetching pages from Confluence space {space_key}...")
    pages = confluence.get_space_pages(space_key, limit=1000)
    logger.info(f"Found {len(pages)} pages in space {space_key}")
    
    synced_count = 0
    skipped_count = 0
    
    for page in pages:
        try:
            page_id = page['id']
            
            # Fetch full page details with content
            page_url = f"{confluence.base_url}/rest/api/content/{page_id}?expand=body.storage,version"
            response = requests.get(page_url, headers=confluence.headers, auth=confluence.auth, timeout=15)
            response.raise_for_status()
            full_page = response.json()
            
            version_number = full_page['version']['number']
            
            # Check if page needs update
            local_page = get_confluence_page(project_id, page_id)
            
            needs_update = False
            if not local_page:
                # New page
                logger.info(f"  New page: {full_page['title']}")
                needs_update = True
            elif local_page['version_number'] < version_number:
                # Updated page
                logger.info(f"  Updated page: {full_page['title']} (v{local_page['version_number']} → v{version_number})")
                needs_update = True
            else:
                # Unchanged page
                logger.debug(f"  Skipping unchanged page: {full_page['title']}")
                skipped_count += 1
                continue
            
            if needs_update:
                # Update metadata
                upsert_confluence_page(
                    project_id=project_id,
                    user_id=user_id,
                    page_id=page_id,
                    space_key=space_key,
                    title=full_page['title'],
                    url=f"{confluence.base_url}{full_page['_links']['webui']}",
                    version_number=version_number,
                    last_modified_at=full_page['version']['when']
                )
                
                # Delete old embeddings
                delete_embeddings(project_id, 'confluence', page_id)
                
                # Generate new embeddings
                content = full_page['body']['storage']['value']
                await generate_and_store_embeddings(
                    project_id=project_id,
                    user_id=user_id,
                    source_type='confluence',
                    source_id=page_id,
                    title=full_page['title'],
                    content=content,
                    url=f"{confluence.base_url}{full_page['_links']['webui']}"
                )
                
                synced_count += 1
        
        except Exception as e:
            logger.error(f"Error syncing page {page.get('title', page.get('id', 'unknown'))}: {e}")
            continue
    
    logger.info(f"Confluence sync complete: {synced_count} synced, {skipped_count} skipped")


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
    
    # Fetch all issues
    logger.info(f"Fetching issues from Jira project {project_key}...")
    issues = jira.get_project_issues(project_key, max_results=1000)
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

    for issue in issues:
        try:
            issue_key = issue['key']
            fields = issue['fields']
            updated_date_str = fields['updated']
            updated_date = parse_atlassian_date(updated_date_str)
            
            # Check if issue needs update
            local_issue = get_jira_issue(project_id, issue_key)
            
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
                # Calculate actual duration if resolved
                actual_duration_days = None
                if fields.get('resolutiondate') and fields.get('created'):
                    created = parse_atlassian_date(fields['created'])
                    resolved = parse_atlassian_date(fields['resolutiondate'])
                    if created and resolved:
                        actual_duration_days = (resolved - created).days
                
                # Update metadata
                upsert_jira_issue(
                    project_id=project_id,
                    user_id=user_id,
                    issue_key=issue_key,
                    issue_id=issue['id'],
                    project_key=project_key,
                    summary=fields['summary'],
                    url=f"https://{credentials['atlassian_domain'].replace('https://', '').replace('http://', '')}/browse/{issue_key}",
                    issue_type=fields.get('issuetype', {}).get('name'),
                    status=fields.get('status', {}).get('name'),
                    priority=fields.get('priority', {}).get('name'),
                    story_points=fields.get('customfield_10016'),  # Story points field
                    original_estimate_seconds=fields.get('timeoriginalestimate'),
                    time_spent_seconds=fields.get('timespent'),
                    remaining_estimate_seconds=fields.get('timeestimate'),
                    sprint_name=None,  # TODO: Extract from sprint field
                    sprint_id=None,
                    labels=fields.get('labels', []),
                    components=[c['name'] for c in fields.get('components', [])],
                    created_date=fields.get('created'),
                    updated_date=updated_date_str,
                    resolved_date=fields.get('resolutiondate'),
                    actual_duration_days=actual_duration_days
                )
                
                # Delete old embeddings
                delete_embeddings(project_id, 'jira', issue_key)
                
                # Generate new embeddings
                content = f"{fields['summary']}\n\n{fields.get('description', '')}"
                await generate_and_store_embeddings(
                    project_id=project_id,
                    user_id=user_id,
                    source_type='jira',
                    source_id=issue_key,
                    title=f"{issue_key}: {fields['summary']}",
                    content=content,
                    url=f"https://{credentials['atlassian_domain'].replace('https://', '').replace('http://', '')}/browse/{issue_key}"
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
    url: str
):
    """
    Chunk content, generate embeddings, and store in database
    
    Args:
        project_id: Project ID
        user_id: User ID
        source_type: 'confluence' or 'jira'
        source_id: Page ID or Issue Key
        title: Document title
        content: Full document content
        url: Document URL
    """
    try:
        # Chunk the content
        chunks = embedding_service.chunk_text(content)
        
        if not chunks:
            logger.warning(f"No chunks generated for {source_type} {source_id}")
            return
        
        logger.info(f"  Generated {len(chunks)} chunks for {source_type} {source_id}")
        
        # Generate and store embeddings for each chunk
        for i, chunk in enumerate(chunks):
            # Generate embedding
            embedding = embedding_service.generate_embedding(chunk)
            
            # Store in database
            insert_document_embedding(
                project_id=project_id,
                user_id=user_id,
                source_type=source_type,
                source_id=source_id,
                title=title,
                content_chunk=chunk,
                chunk_index=i,
                embedding=embedding,
                url=url
            )
        
        logger.info(f"  Stored {len(chunks)} embeddings for {source_type} {source_id}")
    
    except Exception as e:
        logger.error(f"Error generating embeddings for {source_type} {source_id}: {e}")
        raise
