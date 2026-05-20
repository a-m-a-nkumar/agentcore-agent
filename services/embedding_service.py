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
import time
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
    
    # Transient-error signals (case-insensitive substring match on str(exc))
    # plus exact exception-class names from boto3/openai for fast-path detection.
    _TRANSIENT_SUBSTRINGS = (
        'throttl', 'rate limit', 'rate-limit', 'too many requests',
        'timeout', 'timed out', 'connection', 'reset by peer',
        '429', '500', '502', '503', '504',
        'service unavailable', 'gateway',
    )
    _TRANSIENT_EXC_TYPES = (
        'ThrottlingException', 'ServiceUnavailableException',
        'ModelTimeoutException', 'ModelStreamErrorException',
        'RequestTimeout', 'RequestTimeoutException',
        'ConnectionError', 'ReadTimeout', 'ReadTimeoutError',
        'APITimeoutError', 'APIConnectionError', 'RateLimitError',
        'InternalServerError',
    )

    @classmethod
    def _is_transient(cls, exc: Exception) -> bool:
        """Return True if the exception looks like a retry-worthy transient failure."""
        if type(exc).__name__ in cls._TRANSIENT_EXC_TYPES:
            return True
        msg = str(exc).lower()
        return any(s in msg for s in cls._TRANSIENT_SUBSTRINGS)

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding vector for text with retry/backoff on transient errors.
        VDI:   calls the Deluxe gateway (OpenAI-compatible).
        Local: calls AWS Bedrock Titan Embeddings directly.

        Retries up to 3 times with exponential backoff (1s, 2s, 4s) on common
        transient failures: 429 throttling, 5xx errors, connection resets, and
        provider-specific timeout exceptions. Non-transient errors (e.g. invalid
        input, auth) raise immediately on the first attempt.
        """
        max_attempts = 3
        base_delay = 1.0  # seconds; doubles each attempt

        input_length = len(text)
        word_count = len(text.split())

        last_exc: Exception = None
        for attempt in range(max_attempts):
            try:
                if attempt == 0:
                    logger.info(f"[EmbeddingService] generate_embedding: input_length={input_length} chars, {word_count} words")
                else:
                    logger.info(f"[EmbeddingService] generate_embedding RETRY {attempt + 1}/{max_attempts}: input_length={input_length} chars")

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
                last_exc = e
                err_type = type(e).__name__
                transient = self._is_transient(e)
                if attempt < max_attempts - 1 and transient:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"[EmbeddingService] transient error '{err_type}: {e}' — "
                        f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_attempts})"
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"[EmbeddingService] FAILED to generate embedding: {err_type}: {e}")
                raise

        # Defensive — should never reach here because the loop either returns or raises.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("generate_embedding exited retry loop without a result")
    
    def generate_embeddings_batch(
        self,
        texts: List[str],
        batch_size: int = 25,
    ) -> List[List[float]]:
        """
        Generate embeddings for many chunks with significantly fewer API round trips.

        Gateway path (production / VDI):
            Sends `batch_size` chunks per HTTP call via the OpenAI-compatible
            embeddings API (which accepts `input` as a list). For 13,000 chunks
            this collapses 13,000 sequential calls down to ~520, yielding a
            ~15-25x speedup on the embedding stage of an initial sync.

        Bedrock path (local dev):
            Titan-v2's invoke_model only accepts a single inputText per request.
            We fall back to calling generate_embedding() in a tight loop. Each
            call still benefits from the retry/backoff in generate_embedding.

        Order is preserved: result[i] is the embedding of texts[i].

        Args:
            texts:      List of strings to embed.
            batch_size: How many chunks per request on the gateway path.
                        25 is conservative and safe for Titan-v2 via the gateway.

        Returns:
            List of embedding vectors, same length and order as `texts`.
        """
        if not texts:
            return []

        # Bedrock path: Titan-v2 is single-input only — loop and reuse retry logic.
        if self.provider == 'bedrock':
            logger.info(f"[EmbeddingService] generate_embeddings_batch: Bedrock single-input path, {len(texts)} chunks")
            return [self.generate_embedding(t) for t in texts]

        # Gateway path: batch via OpenAI-compatible `input` list parameter.
        logger.info(
            f"[EmbeddingService] generate_embeddings_batch: gateway path, "
            f"{len(texts)} chunks in batches of {batch_size}"
        )
        all_embeddings: List[List[float]] = []
        max_attempts = 3
        base_delay = 1.0

        for start in range(0, len(texts), batch_size):
            chunk_batch = texts[start:start + batch_size]
            batch_label = f"{start + 1}-{start + len(chunk_batch)}/{len(texts)}"

            # Per-batch retry loop (reusing the same transient classifier).
            last_exc: Exception = None
            for attempt in range(max_attempts):
                try:
                    if attempt == 0:
                        logger.info(f"[EmbeddingService] batch {batch_label}: {len(chunk_batch)} inputs")
                    else:
                        logger.info(f"[EmbeddingService] batch {batch_label} RETRY {attempt + 1}/{max_attempts}")

                    response = self.client.embeddings.create(
                        model=self.embedding_model_id,
                        input=chunk_batch,            # list, not a single string
                        dimensions=EMBEDDING_DIMS,
                    )
                    if not response or not response.data or len(response.data) != len(chunk_batch):
                        raise ValueError(
                            f"Gateway returned {len(response.data) if response else 0} embeddings "
                            f"for batch of {len(chunk_batch)}"
                        )
                    # response.data preserves input order per OpenAI API contract.
                    all_embeddings.extend(d.embedding for d in response.data)
                    break  # success — move to next batch

                except Exception as e:
                    last_exc = e
                    err_type = type(e).__name__
                    if attempt < max_attempts - 1 and self._is_transient(e):
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"[EmbeddingService] batch {batch_label} transient error "
                            f"'{err_type}: {e}' — retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    logger.error(f"[EmbeddingService] batch {batch_label} FAILED: {err_type}: {e}")
                    raise

        logger.info(f"[EmbeddingService] generate_embeddings_batch: produced {len(all_embeddings)} embeddings")
        return all_embeddings

    def generate_embeddings_for_chunks(self, chunks: List[str]) -> List[List[float]]:
        """
        Legacy per-chunk loop. Kept for backward compatibility with any caller
        still using it; new code should call generate_embeddings_batch().
        """
        embeddings = []
        for i, chunk in enumerate(chunks):
            print(f"  Generating embedding {i+1}/{len(chunks)}...")
            embedding = self.generate_embedding(chunk)
            embeddings.append(embedding)
        return embeddings


# Singleton instance
embedding_service = EmbeddingService()
