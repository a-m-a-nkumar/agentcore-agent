"""
Embedding Service - Generate and store vector embeddings using AWS Bedrock Titan
"""

import boto3
import json
import re
from typing import List, Dict
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self):
        region = os.getenv('AWS_REGION', 'us-east-1')
        has_access_key = bool(os.getenv('AWS_ACCESS_KEY_ID'))
        has_secret_key = bool(os.getenv('AWS_SECRET_ACCESS_KEY'))
        has_session_token = bool(os.getenv('AWS_SESSION_TOKEN'))
        logger.info(f"[EmbeddingService] Initializing Bedrock client: region={region}, "
                     f"access_key={'SET' if has_access_key else 'MISSING'}, "
                     f"secret_key={'SET' if has_secret_key else 'MISSING'}, "
                     f"session_token={'SET' if has_session_token else 'MISSING'}")
        self.bedrock_runtime = boto3.client(
            'bedrock-runtime',
            region_name=region,
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            aws_session_token=os.getenv('AWS_SESSION_TOKEN')
        )
        self.embedding_model_id = "amazon.titan-embed-text-v1"
        self.chunk_size = 500  # words per chunk
        logger.info(f"[EmbeddingService] Ready. Model: {self.embedding_model_id}, chunk_size: {self.chunk_size}")
    
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
        Generate embedding vector for text using AWS Bedrock Titan
        
        Args:
            text: Text to embed
            
        Returns:
            1536-dimensional embedding vector
        """
        try:
            input_length = len(text)
            word_count = len(text.split())
            logger.info(f"[EmbeddingService] generate_embedding: input_length={input_length} chars, {word_count} words")

            # Prepare request
            body = json.dumps({
                "inputText": text
            })

            # Call Bedrock
            logger.info(f"[EmbeddingService] Calling Bedrock invoke_model (model={self.embedding_model_id})...")
            response = self.bedrock_runtime.invoke_model(
                modelId=self.embedding_model_id,
                body=body,
                contentType='application/json',
                accept='application/json'
            )

            # Parse response
            response_body = json.loads(response['body'].read())
            embedding = response_body.get('embedding')

            if not embedding:
                raise ValueError("No embedding returned from Bedrock")

            logger.info(f"[EmbeddingService] Bedrock returned embedding: dimension={len(embedding)}")
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
