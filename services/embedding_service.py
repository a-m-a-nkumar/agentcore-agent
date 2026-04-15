"""
Embedding Service - Generate and store vector embeddings.
VDI:   Uses the Deluxe OpenAI-compatible gateway proxy (Titan-v2).
Local: Uses AWS Bedrock directly (amazon.titan-embed-text-v2:0).

Chunking uses LangChain's two-stage pipeline:
  Stage 1: MarkdownHeaderTextSplitter (splits on ## / ### headers)
  Stage 2: RecursiveCharacterTextSplitter (splits oversized sections with overlap)

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
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

load_dotenv()

logger = logging.getLogger(__name__)

# Respect the environment switch: if EMBEDDING_PROVIDER is not explicitly set,
# fall back to AGENT_MODEL_PROVIDER / EMBEDDING_DIMENSIONS from environment.py.
try:
    from environment import AGENT_MODEL_PROVIDER as _ENV_PROVIDER
    from environment import EMBEDDING_DIMENSIONS as _ENV_DIMENSIONS
    from environment import BEDROCK_EMBEDDING_MODEL as _ENV_BEDROCK_MODEL
except ImportError:
    _ENV_PROVIDER = 'gateway'
    _ENV_DIMENSIONS = 1024
    _ENV_BEDROCK_MODEL = 'amazon.titan-embed-text-v1'

EMBEDDING_PROVIDER  = os.getenv('EMBEDDING_PROVIDER', _ENV_PROVIDER)   # 'gateway' or 'bedrock'
EMBEDDING_DIMS      = int(os.getenv('EMBEDDING_DIMENSIONS', str(_ENV_DIMENSIONS)))  # 1536 local, 1024 VDI
GATEWAY_URL         = os.getenv('DLXAI_GATEWAY_URL', 'https://dlxai-dev.deluxe.com/proxy')
GATEWAY_KEY         = os.getenv('DLXAI_GATEWAY_KEY', 'sk-2cdb551cf35f418ea88b36')
EMBEDDING_MODEL     = os.getenv('EMBEDDING_MODEL', 'Titan-v2')
BEDROCK_EMBED_MODEL = os.getenv('BEDROCK_EMBEDDING_MODEL', _ENV_BEDROCK_MODEL)
AWS_REGION          = os.getenv('AWS_REGION', 'us-east-1')

class EmbeddingService:
    def __init__(self):
        self.chunk_size = 500  # legacy — kept for backward compat, ignored by LangChain pipeline

        # ── LangChain splitters ──
        # Stage 1: Split Confluence pages on Markdown headers
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("##", "section"),
                ("###", "subsection"),
            ],
            strip_headers=False,  # keep header text in chunk content for embedding + BM25
        )

        # Stage 2: Split oversized sections with 15% overlap (token-based via tiktoken)
        try:
            self.recursive_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                encoding_name="cl100k_base",
                chunk_size=450,
                chunk_overlap=68,
                separators=["\n\n", "\n", ". ", " ", ""],
                is_separator_regex=False,
            )
            logger.info("[EmbeddingService] Using tiktoken-based splitter (450 tokens, 68 overlap)")
        except Exception:
            # Fallback to character-based if tiktoken unavailable
            self.recursive_splitter = RecursiveCharacterTextSplitter(
                separators=["\n\n", "\n", ". ", " ", ""],
                chunk_size=1800,
                chunk_overlap=272,
                length_function=len,
                is_separator_regex=False,
            )
            logger.warning("[EmbeddingService] tiktoken unavailable — using character-based splitter (1800 chars, 272 overlap)")

        # ── Embedding provider ──
        if EMBEDDING_PROVIDER == 'bedrock':
            self.provider = 'bedrock'
            self.bedrock_client = boto3.client('bedrock-runtime', region_name=AWS_REGION)
            self.embedding_model_id = BEDROCK_EMBED_MODEL
            logger.info(f"[EmbeddingService] Ready. Provider: Bedrock, Model: {self.embedding_model_id}")
        else:
            self.provider = 'gateway'
            self.client = OpenAI(base_url=GATEWAY_URL, api_key=GATEWAY_KEY)
            self.embedding_model_id = EMBEDDING_MODEL
            logger.info(f"[EmbeddingService] Ready. Gateway: {GATEWAY_URL}, Model: {self.embedding_model_id}")

    # ── HTML-to-Markdown preprocessing ──

    def _preprocess_confluence_content(self, html: str) -> str:
        """Convert Confluence HTML to clean Markdown for header-aware chunking.

        Converts heading tags to Markdown headers BEFORE stripping other HTML
        so that MarkdownHeaderTextSplitter can split on them.
        """
        if not html:
            return ""

        text = html

        # Convert headings to Markdown (BEFORE stripping other tags)
        text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1\n\n', text, flags=re.DOTALL | re.IGNORECASE)

        # Convert list items
        text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.DOTALL | re.IGNORECASE)

        # Convert <br> to newlines
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

        # Convert <p> to double newlines
        text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<p[^>]*>', '', text, flags=re.IGNORECASE)

        # Convert code blocks
        text = re.sub(r'<pre[^>]*>(.*?)</pre>', r'```\n\1\n```\n', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)

        # Strip all remaining HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)

        # Decode HTML entities
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&nbsp;', ' ').replace('&quot;', '"')

        # Normalize whitespace: collapse multiple blank lines, trim trailing spaces
        text = re.sub(r'[ \t]+', ' ', text)  # collapse horizontal whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)  # max 2 consecutive newlines
        text = re.sub(r' *\n', '\n', text)  # strip trailing spaces per line

        logger.info(f"[CHUNKING] Preprocessed Confluence HTML → {len(text)} chars of Markdown")
        return text.strip()

    # ── Two-stage chunking pipeline ──

    def chunk_text(self, text: str, chunk_size: int = None, source_type: str = "confluence", page_title: str = "") -> List[str]:
        """
        Split text into chunks using LangChain two-stage pipeline.

        Confluence pages: MarkdownHeaderTextSplitter → RecursiveCharacterTextSplitter
        Jira tickets: returned as a single chunk (no splitting)

        Args:
            text: Document content (preprocessed Markdown for Confluence, plain text for Jira)
            chunk_size: Ignored — kept for backward compatibility
            source_type: 'confluence' or 'jira'
            page_title: Page title or ticket key — prepended to each chunk for context

        Returns:
            List of chunk strings
        """
        if not text or not text.strip():
            return []

        # ── Jira: single chunk ──
        if source_type == "jira":
            chunk = f"[{page_title}] {text}" if page_title else text
            # Only split very long Jira tickets (>4000 chars ≈ 1000 tokens)
            if len(chunk) > 4000:
                logger.info(f"[CHUNKING] Jira ticket '{page_title}' is long ({len(chunk)} chars) — splitting with recursive splitter")
                sub_chunks = self.recursive_splitter.split_text(chunk)
                logger.info(f"[CHUNKING] Jira → {len(sub_chunks)} chunks")
                return sub_chunks
            logger.info(f"[CHUNKING] Jira ticket '{page_title}' → 1 chunk ({len(chunk)} chars)")
            return [chunk]

        # ── Confluence: two-stage pipeline ──
        logger.info(f"[CHUNKING] Processing Confluence page: '{page_title}' ({len(text)} chars)")

        # Stage 1: Split on Markdown headers
        header_splits = self.header_splitter.split_text(text)
        logger.info(f"[CHUNKING] Stage 1 (header split) → {len(header_splits)} sections")

        if not header_splits:
            # No headers found — fall back to recursive splitter on raw text
            logger.info(f"[CHUNKING] No headers found — falling back to recursive splitter")
            raw_chunks = self.recursive_splitter.split_text(text)
            result = []
            for c in raw_chunks:
                content = f"[{page_title}] {c}" if page_title else c
                result.append(content)
            logger.info(f"[CHUNKING] Fallback → {len(result)} chunks")
            return [c for c in result if c.strip() and len(c.strip()) > 50]

        # Stage 2: Split oversized sections with recursive splitter
        final_docs = self.recursive_splitter.split_documents(header_splits)
        logger.info(f"[CHUNKING] Stage 2 (recursive split) → {len(final_docs)} chunks")

        # Stage 3: Prepend page title + section header to each chunk
        result_chunks = []
        for doc in final_docs:
            section = doc.metadata.get("section", "")
            subsection = doc.metadata.get("subsection", "")

            parts = [page_title] if page_title else []
            if section:
                parts.append(section)
            if subsection:
                parts.append(subsection)

            prefix = " > ".join(parts)
            content = f"[{prefix}] {doc.page_content}" if prefix else doc.page_content
            result_chunks.append(content)

        # Stage 4: Filter out empty / too-short chunks
        result_chunks = [c for c in result_chunks if c.strip() and len(c.strip()) > 50]

        logger.info(f"[CHUNKING] Final: '{page_title}' → {len(result_chunks)} chunks")
        for i, c in enumerate(result_chunks[:3]):
            logger.info(f"[CHUNKING]   chunk[{i}] preview: {c[:120]}...")

        return result_chunks
    
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
                logger.info(f"[EmbeddingService] Calling Bedrock embeddings (model={self.embedding_model_id}, dims={EMBEDDING_DIMS})...")
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
                logger.info(f"[EmbeddingService] Calling gateway embeddings (model={self.embedding_model_id}, dims={EMBEDDING_DIMS})...")
                response = self.client.embeddings.create(
                    model=self.embedding_model_id,
                    input=text,
                    dimensions=EMBEDDING_DIMS,
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
