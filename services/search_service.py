"""
Search Service - Centralized logic for semantic search and retrieval
Handles embedding generation, vector search, and context assembly
"""

from typing import List, Dict, Optional, Any
from services.embedding_service import embedding_service
from db_helper_vector import search_embeddings, get_surrounding_chunks_batch, hybrid_search
import logging

logger = logging.getLogger(__name__)


class SearchService:
    """Service for semantic search and retrieval operations"""
    
    def semantic_search(
        self,
        project_id: str,
        query: str,
        limit: int = 5,
        source_type: Optional[str] = None,
        include_context: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic search with optional context expansion
        
        Args:
            project_id: Project ID to search within
            query: Natural language search query
            limit: Number of results to return
            source_type: Optional filter by 'confluence' or 'jira'
            include_context: Whether to include chunk ± 1 for context
            
        Returns:
            List of search results with combined content and metadata
        """
        try:
            # 1. Generate embedding for query
            logger.info(f"Generating embedding for query: {query}")
            query_embedding = embedding_service.generate_embedding(query)
            
            # 2. Hybrid search (vector + BM25 keyword, fused via RRF)
            logger.info(f"[HYBRID] Running hybrid search in project {project_id}")
            results = hybrid_search(
                project_id=project_id,
                query_text=query,
                query_embedding=query_embedding,
                limit=limit,
                source_type=source_type
            )
            
            if not results:
                return []
            
            # 3. Batch fetch surrounding chunks if needed
            surrounding_chunks_map = {}
            if include_context:
                # Prepare batch identifiers for all results
                chunk_identifiers = [
                    {
                        'source_id': result['source_id'],
                        'chunk_index': result['chunk_index']
                    }
                    for result in results
                    if result['chunk_index'] >= 0
                ]
                
                if chunk_identifiers:
                    surrounding_chunks_map = get_surrounding_chunks_batch(
                        project_id=project_id,
                        chunk_identifiers=chunk_identifiers,
                        window=1
                    )
            
            # 4. Format and combine results
            search_results = []
            for result in results:
                content = result['content_chunk']
                
                # Include surrounding chunks if requested
                if include_context:
                    key = f"{result['source_id']}_{result['chunk_index']}"
                    surrounding = surrounding_chunks_map.get(key, {})
                    
                    # Combine chunks: before + current + after
                    parts = []
                    if surrounding.get('before'):
                        parts.append(surrounding['before'])
                    parts.append(content)
                    if surrounding.get('after'):
                        parts.append(surrounding['after'])
                    
                    content = "\n\n".join(parts)
                
                # Build result dictionary
                search_results.append({
                    'source_type': result['source_type'],
                    'source_id': result['source_id'],
                    'title': result['title'],
                    'content': content,
                    'url': result.get('url', ''),
                    'similarity': float(result.get('similarity', result.get('rrf_score', 0))),
                    'chunk_index': result['chunk_index'],
                    'metadata': result.get('metadata', {})
                })
            
            logger.info(f"Search Service found {len(search_results)} results")
            return search_results

        except Exception as e:
            logger.error(f"Error in semantic_search: {e}")
            raise


# Global instance
search_service = SearchService()
