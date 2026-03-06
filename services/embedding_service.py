"""
Embedding Service - Generate and store vector embeddings via Deluxe gateway proxy
Uses the OpenAI-compatible embeddings endpoint with Titan model
"""

import re
from typing import List
import os
import logging
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv('DLXAI_GATEWAY_URL', 'https://dlxai-dev.deluxe.com/proxy')
GATEWAY_KEY = os.getenv('DLXAI_GATEWAY_KEY', 'sk-2cdb551cf35f418ea88b36')
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'Titan-v2')

class EmbeddingService:
    def __init__(self):
        self.client = OpenAI(base_url=GATEWAY_URL, api_key=GATEWAY_KEY)
        self.embedding_model_id = EMBEDDING_MODEL
        self.chunk_size = 500  # words per chunk
        logger.info(f"[EmbeddingService] Ready. Gateway: {GATEWAY_URL}, Model: {self.embedding_model_id}, chunk_size: {self.chunk_size}")
    
    def chunk_text(self, text: str, chunk_size: int = None) -> List[str]:
        """
        Split text into chunks of approximately chunk_size words
        
        Args:
            text: Text to chunk
            chunk_size: Number of words per chunk (default: 500)
            
        Returns:
            List of text chunks
        """
        if chunk_size is None:
            chunk_size = self.chunk_size
        
        # Clean text
        text = re.sub(r'\s+', ' ', text).strip()
        
        if not text:
            return []
        
        # Split into words
        words = text.split()
        
        # Create chunks
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunk = ' '.join(words[i:i + chunk_size])
            chunks.append(chunk)
        
        return chunks
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding vector for text via gateway proxy (Titan model)
        
        Args:
            text: Text to embed
            
        Returns:
            1024-dimensional embedding vector
        """
        try:
            input_length = len(text)
            word_count = len(text.split())
            logger.info(f"[EmbeddingService] generate_embedding: input_length={input_length} chars, {word_count} words")

            logger.info(f"[EmbeddingService] Calling gateway embeddings (model={self.embedding_model_id})...")
            response = self.client.embeddings.create(
                model=self.embedding_model_id,
                input=text,
            )

            if not response or not response.data:
                raise ValueError("No embedding returned from gateway")

            embedding = response.data[0].embedding
            logger.info(f"[EmbeddingService] Gateway returned embedding: dimension={len(embedding)}")
            return embedding

        except Exception as e:
            logger.error(f"[EmbeddingService] FAILED to generate embedding: {type(e).__name__}: {e}")
            raise
    
    def generate_embeddings_for_chunks(self, chunks: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple chunks
        
        Args:
            chunks: List of text chunks
            
        Returns:
            List of embedding vectors
        """
        embeddings = []
        for i, chunk in enumerate(chunks):
            print(f"  Generating embedding {i+1}/{len(chunks)}...")
            embedding = self.generate_embedding(chunk)
            embeddings.append(embedding)
        
        return embeddings


# Singleton instance
embedding_service = EmbeddingService()
