"""
Embedding Service - Generate and store vector embeddings.
VDI:   Uses the Deluxe OpenAI-compatible gateway proxy (Titan-v2).
Local: Uses AWS Bedrock directly (amazon.titan-embed-text-v2:0).

Set EMBEDDING_PROVIDER=bedrock in .env to use local Bedrock mode.
"""

import re
import json
from typing import List
import os
import logging
import boto3
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_PROVIDER  = os.getenv('EMBEDDING_PROVIDER', 'gateway')   # 'gateway' or 'bedrock'
GATEWAY_URL         = os.getenv('DLXAI_GATEWAY_URL', 'https://dlxai-dev.deluxe.com/proxy')
GATEWAY_KEY         = os.getenv('DLXAI_GATEWAY_KEY', 'sk-2cdb551cf35f418ea88b36')
EMBEDDING_MODEL     = os.getenv('EMBEDDING_MODEL', 'Titan-v2')
BEDROCK_EMBED_MODEL = os.getenv('BEDROCK_EMBEDDING_MODEL', 'amazon.titan-embed-text-v2:0')
AWS_REGION          = os.getenv('AWS_REGION', 'us-east-1')

class EmbeddingService:
    def __init__(self):
        self.chunk_size = 500  # words per chunk
        if EMBEDDING_PROVIDER == 'bedrock':
            self.provider = 'bedrock'
            self.bedrock_client = boto3.client('bedrock-runtime', region_name=AWS_REGION)
            self.embedding_model_id = BEDROCK_EMBED_MODEL
            logger.info(f"[EmbeddingService] Ready. Provider: Bedrock, Model: {self.embedding_model_id}, chunk_size: {self.chunk_size}")
        else:
            self.provider = 'gateway'
            self.client = OpenAI(base_url=GATEWAY_URL, api_key=GATEWAY_KEY)
            self.embedding_model_id = EMBEDDING_MODEL
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
        Generate embedding vector for text.
        VDI:   calls the Deluxe gateway (OpenAI-compatible).
        Local: calls AWS Bedrock Titan Embeddings directly.
        """
        try:
            input_length = len(text)
            word_count = len(text.split())
            logger.info(f"[EmbeddingService] generate_embedding: input_length={input_length} chars, {word_count} words")

            if self.provider == 'bedrock':
                logger.info(f"[EmbeddingService] Calling Bedrock embeddings (model={self.embedding_model_id})...")
                body = json.dumps({"inputText": text})
                response = self.bedrock_client.invoke_model(
                    modelId=self.embedding_model_id,
                    body=body,
                    contentType='application/json',
                    accept='application/json',
                )
                result = json.loads(response['body'].read())
                embedding = result['embedding']
            else:
                logger.info(f"[EmbeddingService] Calling gateway embeddings (model={self.embedding_model_id})...")
                response = self.client.embeddings.create(
                    model=self.embedding_model_id,
                    input=text,
                )
                if not response or not response.data:
                    raise ValueError("No embedding returned from gateway")
                embedding = response.data[0].embedding

            logger.info(f"[EmbeddingService] Embedding generated: dimension={len(embedding)}")
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
