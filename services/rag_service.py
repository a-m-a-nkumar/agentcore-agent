"""
RAG Service - Retrieval-Augmented Generation for question answering
Combines semantic search with LLM responses for intelligent Q&A
"""

import json
import boto3
from typing import List, Dict, Optional, Any
from services.search_service import search_service
from langfuse_client import get_langfuse
import os
import logging

logger = logging.getLogger(__name__)


class RAGService:
    """Service for RAG-based question answering with integrated semantic search"""
    
    def __init__(self):
        region = os.getenv('AWS_REGION', os.getenv('BEDROCK_REGION', 'us-east-1'))
        self.bedrock_runtime = boto3.client('bedrock-runtime', region_name=region)
        # Use cross-region inference profile for on-demand throughput support
        self.model_id = os.getenv('BEDROCK_MODEL_ID', 'us.anthropic.claude-3-5-sonnet-20241022-v2:0')
    
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
            
            # 2. Search embeddings
            logger.info(f"Searching embeddings in project {project_id}")
            results = search_embeddings(
                project_id=project_id,
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
                    'similarity': float(result['similarity']),
                    'chunk_index': result['chunk_index'],
                    'metadata': result.get('metadata', {})
                })
            
            logger.info(f"Found {len(search_results)} search results")
            return search_results

        except Exception as e:
            logger.error(f"Error in semantic_search: {e}")
            raise
    
    async def query_with_rag(
        self,
        project_id: str,
        user_query: str,
        max_chunks: int = 10,
        source_filter: Optional[str] = None,
        include_context: bool = True
    ):
        """
        Query using RAG - retrieve relevant chunks and generate answer
        
        Args:
            project_id: Project ID to search within
            user_query: User's question
            max_chunks: Number of chunks to retrieve
            source_filter: Optional filter ('confluence' or 'jira')
            include_context: Whether to include chunk ±1 for context
        
        Yields:
            Streaming response chunks and sources
        """
        langfuse = get_langfuse()
        try:
            # Step 1 & 2: Use centralized search service (eliminates duplication and uses batch operations)
            logger.info(f"Querying search service for: {user_query[:50]}...")
            with langfuse.start_as_current_observation(
                as_type="span",
                name="rag.search",
                metadata={"query_length": len(user_query), "project_id": project_id, "max_chunks": max_chunks, "source_filter": source_filter or ""},
            ):
                results = search_service.semantic_search(
                    project_id=project_id,
                    query=user_query,
                    limit=max_chunks,
                    source_type=source_filter,
                    include_context=include_context
                )

            if not results:
                yield {
                    'type': 'error',
                    'message': 'No relevant documentation found for your query.'
                }
                return
            
            # Step 3: Format results for LLM context
            context_chunks = []
            sources = []
            
            for result in results:
                # Add to context (search_service already handled chunk expansion with batch operations)
                source_type = result['source_type'].capitalize()
                context_chunks.append({
                    'source': f"[{source_type}] {result['title']}",
                    'content': result['content'],  # Already includes surrounding chunks if requested
                    'url': result.get('url', '')
                })
                
                # Track sources
                raw_url = result.get('url', '')
                sanitized_url = raw_url
                if raw_url and not raw_url.startswith(('http://', 'https://')):
                    sanitized_url = f"https://{raw_url}"

                sources.append({
                    'type': result['source_type'],
                    'title': result['title'],
                    'url': sanitized_url,
                    'similarity': float(result.get('similarity', 0))
                })
            
            logger.info(f"Built context from {len(context_chunks)} chunks")
            
            # Step 4: Build prompt
            prompt = self._build_rag_prompt(user_query, context_chunks)
            
            # Step 5: Stream LLM response (with Langfuse generation span)
            logger.info("Streaming LLM response...")
            accumulated_output: List[str] = []
            with langfuse.start_as_current_observation(
                as_type="generation",
                name="rag.llm",
                model=self.model_id,
                input=prompt,
                metadata={"project_id": project_id},
            ) as gen_obs:
                async for chunk in self._stream_claude_response(prompt):
                    if chunk.get("type") == "chunk":
                        accumulated_output.append(chunk.get("content", ""))
                    yield chunk
                gen_obs.update(output="".join(accumulated_output))
            
            # Step 6: Send sources
            yield {
                'type': 'sources',
                'sources': sources
            }
            
            # Step 7: Signal completion
            yield {'type': 'done'}
            
        except Exception as e:
            logger.error(f"Error in RAG query: {e}")
            yield {
                'type': 'error',
                'message': f'An error occurred: {str(e)}'
            }
    
    def _build_rag_prompt(self, query: str, context_chunks: List[Dict]) -> str:
        """Build prompt for Claude with context"""
        
        # Format context
        context_text = ""
        for i, chunk in enumerate(context_chunks, 1):
            context_text += f"\n\n--- Source {i}: {chunk['source']} ---\n"
            context_text += chunk['content']
        
        # Build full prompt
        prompt = f"""You are a helpful AI assistant answering questions based on project documentation from Confluence and Jira.

Context from documentation:
{context_text}

User Question: {query}

Instructions:
- Answer based ONLY on the provided context above
- Cite sources using the format [Source: Title] when referencing information
- If the context doesn't contain enough information to answer the question, say "I don't have enough information in the documentation to answer this question."
- Be concise, accurate, and helpful
- Use markdown formatting for better readability

Answer:"""
        
        return prompt
    
    async def _stream_claude_response(self, prompt: str):
        """Stream response from Claude"""
        try:
            # Prepare request body
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                # For Claude 3.5 Sonnet on Bedrock, only one of temperature or top_p
                # may be specified. We use temperature for balanced creativity.
                "temperature": 0.7
            }
            
            # Invoke model with streaming
            response = self.bedrock_runtime.invoke_model_with_response_stream(
                modelId=self.model_id,
                body=json.dumps(request_body)
            )
            
            # Stream chunks
            stream = response.get('body')
            if stream:
                for event in stream:
                    chunk = event.get('chunk')
                    if chunk:
                        chunk_data = json.loads(chunk.get('bytes').decode())
                        
                        # Handle different event types
                        if chunk_data.get('type') == 'content_block_delta':
                            delta = chunk_data.get('delta', {})
                            if delta.get('type') == 'text_delta':
                                text = delta.get('text', '')
                                if text:
                                    yield {
                                        'type': 'chunk',
                                        'content': text
                                    }
        
        except Exception as e:
            logger.error(f"Error streaming Claude response: {e}")
            yield {
                'type': 'error',
                'message': f'Error generating response: {str(e)}'
            }


# Global instance
rag_service = RAGService()
