"""
Search Router - API endpoints for semantic search across Confluence and Jira
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from services.embedding_service import embedding_service
from db_helper_vector import search_embeddings
from auth import verify_azure_token
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


# ============================================
# AUTHENTICATION DEPENDENCY
# ============================================

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    """
    Get current user from Azure AD token
    Creates/updates user in database if needed
    """
    from db_helper import create_or_update_user
    
    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")
    
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")
    
    # Create or update user in database
    try:
        user = create_or_update_user(user_id, email, name)
        return user
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


class SearchRequest(BaseModel):
    project_id: str
    query: str
    limit: Optional[int] = 5
    source_type: Optional[str] = None  # 'confluence', 'jira', or None for both
    include_context: Optional[bool] = True  # Include chunk ± 1 for context


class SearchResult(BaseModel):
    source_type: str
    source_id: str
    title: str
    content: str  # The matched chunk (or chunk ± 1 if include_context=True)
    url: str
    similarity: float
    chunk_index: int


@router.post("/", response_model=List[SearchResult])
async def semantic_search(
    search_request: SearchRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Perform semantic search across project's Confluence pages and Jira issues
    
    - **project_id**: Project ID to search within
    - **query**: Natural language search query
    - **limit**: Number of results to return (default: 5)
    - **source_type**: Filter by 'confluence' or 'jira' (optional)
    - **include_context**: Include surrounding chunks for better context (default: True)
    
    Returns top-k most relevant chunks with similarity scores
    """
    try:
        # Generate embedding for query
        logger.info(f"Generating embedding for query: {search_request.query}")
        query_embedding = embedding_service.generate_embedding(search_request.query)
        
        # Search embeddings
        logger.info(f"Searching embeddings in project {search_request.project_id}")
        results = search_embeddings(
            project_id=search_request.project_id,
            query_embedding=query_embedding,
            limit=search_request.limit,
            source_type=search_request.source_type
        )
        
        # Batch fetch surrounding chunks if needed (single DB connection!)
        surrounding_chunks_map = {}
        if search_request.include_context and results:
            # Prepare batch identifiers for all results
            chunk_identifiers = [
                {
                    'source_id': result['source_id'],
                    'chunk_index': result['chunk_index']
                }
                for result in results
                if result['chunk_index'] >= 0  # Include all chunks, batch function handles edge cases
            ]
            
            if chunk_identifiers:
                from db_helper_vector import get_surrounding_chunks_batch
                surrounding_chunks_map = get_surrounding_chunks_batch(
                    project_id=search_request.project_id,
                    chunk_identifiers=chunk_identifiers,
                    window=1
                )
        
        # Format results
        search_results = []
        for result in results:
            content = result['content_chunk']
            
            # Include surrounding chunks if requested
            if search_request.include_context:
                key = f"{result['source_id']}_{result['chunk_index']}"
                surrounding = surrounding_chunks_map.get(key, {})
                
                # Combine chunks
                parts = []
                if surrounding.get('before'):
                    parts.append(surrounding['before'])
                parts.append(content)
                if surrounding.get('after'):
                    parts.append(surrounding['after'])
                
                content = "\n\n".join(parts)
            
            search_results.append(SearchResult(
                source_type=result['source_type'],
                source_id=result['source_id'],
                title=result['title'],
                content=content,
                url=result['url'],
                similarity=float(result['similarity']),
                chunk_index=result['chunk_index']
            ))
        
        logger.info(f"Found {len(search_results)} results")
        return search_results
    
    except Exception as e:
        logger.error(f"Error performing search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/predict-tshirt-size")
async def predict_tshirt_size(
    feature_description: str,
    project_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Predict t-shirt size for a new feature using GenAI and historical Jira data
    
    - **feature_description**: Description of the new feature
    - **project_id**: Project ID to search for similar issues
    
    Returns prediction with confidence and reasoning
    """
    try:
        # TODO: Implement GenAI prediction
        # 1. Search for similar Jira issues using vector search
        # 2. Fetch their story points, duration, labels
        # 3. Send to GenAI (Claude/GPT) with prompt
        # 4. Return prediction
        
        return {
            "status": "not_implemented",
            "message": "T-shirt size prediction will be implemented in Phase 5"
        }
    
    except Exception as e:
        logger.error(f"Error predicting t-shirt size: {e}")
        raise HTTPException(status_code=500, detail=str(e))
