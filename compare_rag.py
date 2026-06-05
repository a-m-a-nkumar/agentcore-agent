"""
Compare RAG Pipeline: Old (vector-only) vs New (hybrid + query rewriting)
=========================================================================
Run this script to see side-by-side retrieval results for any query.

Usage:
    python compare_rag.py --project_id <ID> --query "your question here"
    python compare_rag.py --project_id <ID>   (uses default test queries)
"""

import argparse
import time
import logging
from typing import List, Dict

from services.embedding_service import embedding_service
from db_helper_vector import search_embeddings, hybrid_search
from services.search_service import search_service
from services.rag_service import rag_service

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def old_pipeline_search(project_id: str, query: str, limit: int = 5) -> List[Dict]:
    """Old pipeline: embedding → vector search only (no BM25, no rewriting)"""
    query_embedding = embedding_service.generate_embedding(query)
    results = search_embeddings(
        project_id=project_id,
        query_embedding=query_embedding,
        limit=limit,
    )
    return results


def new_pipeline_search(project_id: str, query: str, limit: int = 5) -> List[Dict]:
    """New pipeline: query rewriting → hybrid search (vector + BM25 + RRF) → multi-query merge"""
    results = rag_service._multi_query_search(
        project_id=project_id,
        user_query=query,
        max_chunks=limit,
        include_context=False,  # Raw chunks for fair comparison
    )
    return results


def print_results(label: str, results: List[Dict], elapsed: float):
    print(f"\n{'─' * 70}")
    print(f"  {label}  ({len(results)} results in {elapsed:.2f}s)")
    print(f"{'─' * 70}")
    for i, r in enumerate(results, 1):
        title = r.get('title', 'N/A')[:60]
        source_type = r.get('source_type', '?')
        similarity = r.get('similarity', r.get('rrf_score', 0))
        rrf = r.get('rrf_score', None)
        appearances = r.get('query_appearances', None)
        chunk_idx = r.get('chunk_index', '?')
        content = r.get('content_chunk', r.get('content', ''))[:200].replace('\n', ' ')

        score_str = f"sim={similarity:.4f}"
        if rrf is not None:
            score_str += f"  rrf={rrf:.4f}"
        if appearances is not None:
            score_str += f"  seen_by={appearances}/4 queries"

        print(f"  [{i}] [{source_type}] {title}")
        print(f"      chunk={chunk_idx}  {score_str}")
        print(f"      {content}{'...' if len(content) >= 200 else ''}")
        print()


def compare(project_id: str, query: str, limit: int = 5):
    print(f"\n{'=' * 70}")
    print(f"  QUERY: {query}")
    print(f"{'=' * 70}")

    # Old pipeline
    t0 = time.time()
    old_results = old_pipeline_search(project_id, query, limit)
    old_time = time.time() - t0

    # New pipeline
    t0 = time.time()
    new_results = new_pipeline_search(project_id, query, limit)
    new_time = time.time() - t0

    print_results("OLD PIPELINE (vector-only)", old_results, old_time)
    print_results("NEW PIPELINE (hybrid + rewriting)", new_results, new_time)

    # Overlap analysis
    old_chunks = {(r.get('source_id', ''), r.get('chunk_index', 0)) for r in old_results}
    new_chunks = {(r.get('source_id', ''), r.get('chunk_index', 0)) for r in new_results}
    overlap = old_chunks & new_chunks
    only_old = old_chunks - new_chunks
    only_new = new_chunks - old_chunks

    print(f"{'─' * 70}")
    print(f"  OVERLAP ANALYSIS")
    print(f"{'─' * 70}")
    print(f"  Shared chunks:         {len(overlap)}/{limit}")
    print(f"  Only in OLD pipeline:  {len(only_old)}")
    print(f"  Only in NEW pipeline:  {len(only_new)}  ← new chunks surfaced by hybrid/rewriting")
    print(f"  Latency: OLD={old_time:.2f}s  NEW={new_time:.2f}s  (+{new_time - old_time:.2f}s)")
    print(f"{'=' * 70}\n")


DEFAULT_QUERIES = [
    "what are the payment functional requirements",
    "how does user authentication work",
    "implement login functionality",
    "JIRA sprint velocity tracking",
    "how does the system handle errors and retries",
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare old vs new RAG pipeline")
    parser.add_argument("--project_id", required=True, help="Project ID to search")
    parser.add_argument("--query", type=str, default=None, help="Single query to test")
    parser.add_argument("--limit", type=int, default=5, help="Number of results per pipeline")
    args = parser.parse_args()

    queries = [args.query] if args.query else DEFAULT_QUERIES

    print("\n" + "=" * 70)
    print("  RAG PIPELINE COMPARISON: Old (vector-only) vs New (hybrid + rewriting)")
    print("=" * 70)

    for q in queries:
        compare(args.project_id, q, args.limit)
