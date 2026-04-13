"""
RAG Pipeline Evaluation: Old (vector-only) vs New (hybrid + query rewriting)
=============================================================================
Simulates real IDE developer queries and evaluates retrieval quality
against known ground-truth content from the Confluence knowledge base.

Usage:
    python evaluate_rag.py --project_id <ID>
    python evaluate_rag.py --project_id <ID> --query_index 3   (run single query)
"""

import argparse
import time
import json
import logging
import sys
import io
from typing import List, Dict, Tuple
from datetime import datetime

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from services.embedding_service import embedding_service
from db_helper_vector import search_embeddings, hybrid_search
from services.search_service import search_service
from services.rag_service import rag_service

logging.basicConfig(level=logging.WARNING)

# ═══════════════════════════════════════════════════════════════════════
# TEST QUERIES: Realistic IDE developer queries + expected keywords
# Each query has:
#   - query: what a developer would type in their IDE
#   - intent: what they're actually trying to build
#   - must_find: keywords that MUST appear in good retrieval results
#   - should_find: keywords that ideally appear (bonus)
#   - domain: which BRD domain this targets
# ═══════════════════════════════════════════════════════════════════════

TEST_QUERIES = [
    # ── Payment Processing (core) ──
    {
        "query": "implement payment authorization and capture flow",
        "intent": "Build the core auth/capture payment processing logic",
        "must_find": ["authorization", "capture", "void", "refund"],
        "should_find": ["acquiring bank", "card", "transaction", "PCI"],
        "domain": "Payment Fintech",
    },
    {
        "query": "build card tokenization for recurring payments",
        "intent": "Implement PCI-compliant card vault with tokenization",
        "must_find": ["tokenization", "card", "PCI"],
        "should_find": ["vault", "recurring", "secure", "encryption", "AES"],
        "domain": "Payment Fintech",
    },
    {
        "query": "add 3D Secure authentication to checkout",
        "intent": "Integrate 3DS 2.0 for secure card transactions",
        "must_find": ["3D Secure", "authentication", "3DS"],
        "should_find": ["checkout", "fraud", "SCA", "card"],
        "domain": "Payment Fintech",
    },
    {
        "query": "implement fraud detection scoring system",
        "intent": "Build ML-powered fraud detection for transactions",
        "must_find": ["fraud"],
        "should_find": ["ML", "scoring", "detection", "risk", "transaction", "rule"],
        "domain": "Payment Fintech",
    },
    {
        "query": "create merchant onboarding with KYC verification",
        "intent": "Build merchant signup flow with identity verification",
        "must_find": ["merchant", "onboarding"],
        "should_find": ["KYC", "KYB", "verification", "Jumio", "Onfido"],
        "domain": "Payment Fintech",
    },

    # ── Merchant Portal ──
    {
        "query": "build real-time transaction dashboard with widgets",
        "intent": "Create dashboard showing live transaction metrics",
        "must_find": ["dashboard", "transaction"],
        "should_find": ["real-time", "widget", "metrics", "latency", "approved", "declined"],
        "domain": "Merchant Portal",
    },
    {
        "query": "implement dispute management workflow",
        "intent": "Build dispute submission, tracking, and resolution flow",
        "must_find": ["dispute"],
        "should_find": ["chargeback", "evidence", "status", "resolution", "upload", "document"],
        "domain": "Merchant Portal / Payments Enhancement",
    },
    {
        "query": "add multi-currency support with FX rates",
        "intent": "Enable processing in multiple currencies with live exchange rates",
        "must_find": ["currency", "multi-currency"],
        "should_find": ["FX", "exchange", "rate", "AUD", "CAD", "SGD", "conversion"],
        "domain": "Payments Enhancement Q4",
    },

    # ── Settlement & Refunds ──
    {
        "query": "implement settlement reporting system",
        "intent": "Build daily/hourly settlement report generation",
        "must_find": ["settlement"],
        "should_find": ["report", "reconciliation", "daily", "hourly", "batch", "payout"],
        "domain": "Payments Enhancement Q4",
    },
    {
        "query": "build instant refund processing",
        "intent": "Enable instant refunds for eligible transactions",
        "must_find": ["refund"],
        "should_find": ["instant", "eligible", "processor", "confirmation", "void"],
        "domain": "Payments Enhancement Q4",
    },

    # ── Developer Portal (non-payment) ──
    {
        "query": "create project provisioning wizard with templates",
        "intent": "Build multi-step project creation with template selection",
        "must_find": ["project", "provisioning"],
        "should_find": ["wizard", "template", "Bitbucket", "repository", "ServiceNow"],
        "domain": "Internal Developer Portal",
    },
    {
        "query": "implement webhook notification system for transaction events",
        "intent": "Build event-driven webhook delivery for payment status updates",
        "must_find": ["webhook", "notification"],
        "should_find": ["event", "transaction", "status", "real-time", "merchant"],
        "domain": "Payment Fintech",
    },

    # ── Cross-cutting / Vague queries ──
    {
        "query": "what are the security and compliance requirements",
        "intent": "Find PCI-DSS, encryption, auth requirements across all projects",
        "must_find": ["PCI", "security"],
        "should_find": ["encryption", "AES", "TLS", "compliance", "GDPR", "audit"],
        "domain": "Cross-cutting",
    },
    {
        "query": "implement user authentication and RBAC",
        "intent": "Build role-based access control with SSO",
        "must_find": ["authentication", "role"],
        "should_find": ["SSO", "RBAC", "access", "Admin", "OAuth", "SAML"],
        "domain": "Cross-cutting",
    },
    {
        "query": "set up recurring billing for merchants",
        "intent": "Build subscription/recurring payment processing",
        "must_find": ["recurring", "billing"],
        "should_find": ["merchant", "subscription", "payment", "tokenization", "saved"],
        "domain": "Payment Fintech",
    },
]


def score_results(results: List[Dict], must_find: List[str], should_find: List[str]) -> Dict:
    """Score retrieval results against expected keywords."""
    all_content = " ".join(
        (r.get("content_chunk", "") + " " + r.get("content", "") + " " + r.get("title", ""))
        for r in results
    ).lower()

    must_hits = [kw for kw in must_find if kw.lower() in all_content]
    must_misses = [kw for kw in must_find if kw.lower() not in all_content]
    should_hits = [kw for kw in should_find if kw.lower() in all_content]
    should_misses = [kw for kw in should_find if kw.lower() not in all_content]

    # Score: must_find keywords worth 2 points each, should_find worth 1 point
    max_score = len(must_find) * 2 + len(should_find) * 1
    actual_score = len(must_hits) * 2 + len(should_hits) * 1
    pct = (actual_score / max_score * 100) if max_score > 0 else 0

    return {
        "score": actual_score,
        "max_score": max_score,
        "pct": pct,
        "must_hits": must_hits,
        "must_misses": must_misses,
        "should_hits": should_hits,
        "should_misses": should_misses,
    }


def old_pipeline_search(project_id: str, query: str, limit: int = 5) -> Tuple[List[Dict], float]:
    """Old pipeline: embedding -> vector search only"""
    t0 = time.time()
    query_embedding = embedding_service.generate_embedding(query)
    results = search_embeddings(
        project_id=project_id,
        query_embedding=query_embedding,
        limit=limit,
    )
    elapsed = time.time() - t0
    return results, elapsed


def new_pipeline_search(project_id: str, query: str, limit: int = 5) -> Tuple[List[Dict], float]:
    """New pipeline: query rewriting -> hybrid search -> multi-query merge"""
    t0 = time.time()
    results = rag_service._multi_query_search(
        project_id=project_id,
        user_query=query,
        max_chunks=limit,
        include_context=False,
    )
    elapsed = time.time() - t0
    return results, elapsed


def print_result_details(results: List[Dict], score_info: Dict, elapsed: float, label: str):
    """Print detailed results for one pipeline."""
    pct = score_info["pct"]
    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    print(f"\n  {label}")
    print(f"  Score: {score_info['score']}/{score_info['max_score']} ({pct:.0f}%) {bar}  [{elapsed:.1f}s]")

    if score_info["must_misses"]:
        print(f"  MISSING (critical): {', '.join(score_info['must_misses'])}")
    if score_info["should_hits"]:
        print(f"  Found (bonus):      {', '.join(score_info['should_hits'])}")
    if score_info["should_misses"]:
        print(f"  Missing (bonus):    {', '.join(score_info['should_misses'])}")

    for i, r in enumerate(results[:5], 1):
        title = r.get("title", "N/A")[:55]
        src = r.get("source_type", "?")
        sim = r.get("similarity", r.get("rrf_score", 0))
        chunk = r.get("chunk_index", "?")
        appearances = r.get("query_appearances", None)
        content = r.get("content_chunk", r.get("content", ""))[:120].replace("\n", " ")

        extra = f"  seen={appearances}/4q" if appearances else ""
        print(f"    [{i}] [{src}] {title} (c={chunk} s={sim:.4f}{extra})")
        print(f"        {content}...")


def run_evaluation(project_id: str, limit: int = 5, query_index: int = None):
    queries = TEST_QUERIES if query_index is None else [TEST_QUERIES[query_index]]

    # Accumulators for final summary
    old_scores = []
    new_scores = []
    old_times = []
    new_times = []
    wins = {"old": 0, "new": 0, "tie": 0}

    print("\n" + "=" * 75)
    print("  RAG PIPELINE EVALUATION")
    print(f"  Project: {project_id}")
    print(f"  Queries: {len(queries)}  |  Results per query: {limit}")
    print(f"  Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)

    for idx, tq in enumerate(queries):
        qnum = query_index if query_index is not None else idx
        print(f"\n{'─' * 75}")
        print(f"  Q{qnum + 1}: \"{tq['query']}\"")
        print(f"  Intent: {tq['intent']}")
        print(f"  Domain: {tq['domain']}")
        print(f"  Must find: {tq['must_find']}  |  Should find: {tq['should_find']}")
        print(f"{'─' * 75}")

        # Old pipeline
        old_results, old_time = old_pipeline_search(project_id, tq["query"], limit)
        old_score = score_results(old_results, tq["must_find"], tq["should_find"])

        # New pipeline
        new_results, new_time = new_pipeline_search(project_id, tq["query"], limit)
        new_score = score_results(new_results, tq["must_find"], tq["should_find"])

        print_result_details(old_results, old_score, old_time, "OLD (vector-only)")
        print_result_details(new_results, new_score, new_time, "NEW (hybrid + rewriting)")

        # Verdict
        old_pct = old_score["pct"]
        new_pct = new_score["pct"]
        diff = new_pct - old_pct

        if diff > 5:
            verdict = f"NEW wins (+{diff:.0f}%)"
            wins["new"] += 1
        elif diff < -5:
            verdict = f"OLD wins ({diff:.0f}%)"
            wins["old"] += 1
        else:
            verdict = f"TIE (delta {diff:+.0f}%)"
            wins["tie"] += 1

        print(f"\n  >>> VERDICT: {verdict}")

        old_scores.append(old_pct)
        new_scores.append(new_pct)
        old_times.append(old_time)
        new_times.append(new_time)

    # ═══════════════════════════════════════════════════
    # FINAL SUMMARY
    # ═══════════════════════════════════════════════════
    avg_old = sum(old_scores) / len(old_scores) if old_scores else 0
    avg_new = sum(new_scores) / len(new_scores) if new_scores else 0
    avg_old_t = sum(old_times) / len(old_times) if old_times else 0
    avg_new_t = sum(new_times) / len(new_times) if new_times else 0

    print(f"\n{'=' * 75}")
    print(f"  FINAL SUMMARY")
    print(f"{'=' * 75}")
    print(f"  Queries tested:    {len(queries)}")
    print()
    print(f"  {'Metric':<25} {'OLD (vector)':>15} {'NEW (hybrid)':>15} {'Delta':>10}")
    print(f"  {'─' * 65}")
    print(f"  {'Avg relevance score':<25} {avg_old:>14.1f}% {avg_new:>14.1f}% {avg_new - avg_old:>+9.1f}%")
    print(f"  {'Avg latency':<25} {avg_old_t:>14.1f}s {avg_new_t:>14.1f}s {avg_new_t - avg_old_t:>+9.1f}s")
    print(f"  {'Wins':<25} {wins['old']:>15} {wins['new']:>15}")
    print(f"  {'Ties':<25} {wins['tie']:>15}")
    print()

    # Per-query score table
    print(f"  {'Query':<55} {'OLD':>6} {'NEW':>6} {'Result':>10}")
    print(f"  {'─' * 80}")
    for i, tq in enumerate(queries):
        qname = tq["query"][:52] + "..." if len(tq["query"]) > 52 else tq["query"]
        o = old_scores[i]
        n = new_scores[i]
        if n - o > 5:
            res = "NEW +"
        elif o - n > 5:
            res = "OLD +"
        else:
            res = "TIE"
        print(f"  {qname:<55} {o:>5.0f}% {n:>5.0f}% {res:>10}")

    print(f"{'=' * 75}\n")

    # Save results to JSON
    report = {
        "timestamp": datetime.now().isoformat(),
        "project_id": project_id,
        "summary": {
            "queries_tested": len(queries),
            "avg_old_score": round(avg_old, 1),
            "avg_new_score": round(avg_new, 1),
            "improvement": round(avg_new - avg_old, 1),
            "avg_old_latency": round(avg_old_t, 1),
            "avg_new_latency": round(avg_new_t, 1),
            "wins_old": wins["old"],
            "wins_new": wins["new"],
            "ties": wins["tie"],
        },
        "queries": [
            {
                "query": tq["query"],
                "domain": tq["domain"],
                "old_score_pct": round(old_scores[i], 1),
                "new_score_pct": round(new_scores[i], 1),
                "old_latency": round(old_times[i], 1),
                "new_latency": round(new_times[i], 1),
            }
            for i, tq in enumerate(queries)
        ],
    }
    with open("rag_evaluation_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report saved to: rag_evaluation_report.json\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate RAG pipeline quality")
    parser.add_argument("--project_id", required=True, help="Project ID to search")
    parser.add_argument("--limit", type=int, default=5, help="Results per query (default: 5)")
    parser.add_argument("--query_index", type=int, default=None, help="Run single query by index (0-based)")
    args = parser.parse_args()

    run_evaluation(args.project_id, args.limit, args.query_index)
