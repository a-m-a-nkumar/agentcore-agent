"""
RAG Service - Retrieval-Augmented Generation for question answering
Combines semantic search with LLM responses for intelligent Q&A
"""

from typing import List, Dict, Optional, Any
from services.search_service import search_service
from langfuse_client import get_langfuse
from llm_gateway import chat_completion
import os
import logging

logger = logging.getLogger(__name__)


class RAGService:
    """Service for RAG-based question answering with integrated semantic search"""
    
    def __init__(self):
        self.model_id = os.getenv('DLXAI_CHAT_MODEL', 'Claude-4.5-Sonnet')
    
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
            


            # Step 5: Stream LLM response
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
    
    async def get_enhanced_prompt(
        self,
        project_id: str,
        user_query: str,
        max_chunks: int = 5,
        source_filter: Optional[str] = None
    ) -> str:
        """
        Retrieve context and build an enhanced prompt for IDE use (MCP)
        Does NOT call the LLM, just returns the prompt string.
        """
        try:
            # 1. Search
            results = search_service.semantic_search(
                project_id=project_id,
                query=user_query,
                limit=max_chunks,
                source_type=source_filter,
                include_context=True
            )
            
            if not results:
                return f"No relevant documentation found for: {user_query}"
            
            # 2. Format context
            context_chunks = []
            for result in results:
                context_chunks.append({
                    'source': f"[{result['source_type'].capitalize()}] {result['title']}",
                    'content': result['content']
                })
            
            # 3. Build optimized prompt for IDE
            context_text = ""
            for i, chunk in enumerate(context_chunks, 1):
                context_text += f"\n<source_{i}>\nTitle: {chunk['source']}\nContent:\n{chunk['content']}\n</source_{i}>\n"
            
            # 4. Ask Claude to generate the Perfect Prompt
            meta_prompt = f"""You are an expert AI prompt engineer. Your goal is to create a highly optimized prompt for an AI coding assistant.

I will provide you with:
1. A User Request (what the developer wants to do)
2. Relevant Context from documentation (Confluence/Jira)

Your task:
Write a new, comprehensive prompt that I can send to the AI coding assistant. 
- The prompt should explicitly incorporate the relevant information from the context.
- It should be clear, step-by-step, and specific.
- Do NOT answer the user request yourself. Just write the PROMPT for the AI to answer it.
- Start directly with the prompt text. Do not add "Here is the prompt:" or similar meta-talk.

User Request: {user_query}

Relevant Context:
{context_text}

Optimized Prompt:"""

            # 5. Call LLM to generate the prompt
            generated_prompt = ""
            async for chunk in self._stream_claude_response(meta_prompt):
                if chunk['type'] == 'chunk':
                    generated_prompt += chunk['content']
            
            return generated_prompt if generated_prompt else f"Error: Failed to generate prompt from context."

        except Exception as e:
            logger.error(f"Error building enhanced prompt: {e}")
            return f"Error retrieving context: {str(e)}"

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
        """Generate response via gateway and emit as chunk events."""
        try:
            prompt_len = len(prompt)
            logger.info(f"Sending prompt to gateway: model={self.model_id}, prompt_length={prompt_len} chars (~{prompt_len // 4} tokens)")

            text = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=4096,
            )

            if text:
                yield {
                    'type': 'chunk',
                    'content': text
                }

        except Exception as e:
            logger.error(f"Error generating gateway response (prompt={len(prompt)} chars): {e}")
            yield {
                'type': 'error',
                'message': f'Error generating response: {str(e)}'
            }


# Global instance
rag_service = RAGService()
