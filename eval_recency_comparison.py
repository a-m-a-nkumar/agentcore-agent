"""
Eval: compare OLD (no recency) vs NEW (W_TEMPORAL_PROMPT_ENHANCE=0.5) RAG behavior
for the pair-programming prompt-enhancement use case on the Digital Payments space.

WHAT IT DOES
------------
For each curated query:
  1. Runs _multi_query_search(w_temporal=0.0)  -- simulates OLD pipeline
  2. Runs _multi_query_search(w_temporal=0.5)  -- NEW pipeline (prompt enhance setting)
  3. Compares which chunks each one retrieves and at what rank
  4. Shows the age distribution of retrieved chunks

Both runs share the SAME LLM-generated query variants (cached on first call) so the
only differential between runs is the recency multiplier. No embeddings are
regenerated, no synced data is modified — read-only against the vector DB.

OUTPUT
------
  - Console: compact comparison table per query
  - Markdown report at --output path (default: eval_recency_report.md)

USAGE
-----
  python eval_recency_comparison.py                 # use defaults (Aman / Digital Payments)
  python eval_recency_comparison.py --project-id <uuid> --user-id <uuid>
  python eval_recency_comparison.py --output my_report.md
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Silence framework noise so the report stays readable.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
for noisy in (
    "db_helper", "db_helper_vector",
    "services.confluence_service", "services.search_service",
    "services.rag_service", "services.embedding_service",
    "httpx", "langfuse", "openai",
):
    logging.getLogger(noisy).setLevel(logging.ERROR)
logger = logging.getLogger("eval_recency")
logger.setLevel(logging.INFO)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from services.rag_service import rag_service
from utils.recency import W_TEMPORAL_PROMPT_ENHANCE


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
DEFAULT_PROJECT_ID = "280e0adb-9dc4-4556-8c9f-c1521fc29ab4"
DEFAULT_USER_ID    = "572dcacd-9440-447f-8677-576fdfe24e5b"
TOP_K              = 8


# ------------------------------------------------------------------
# Curated queries — grounded in actual corpus data (see inspect_corpus.py).
# Each query has a hypothesis + ground truth pages I verified exist in the
# vector DB at known ages, so I can predict the desired behavior.
# ------------------------------------------------------------------
# Categories:
#   pure_recency      : fresh page (<30d) competes with several older pages on
#                       same topic — recency must put fresh one at #1
#   versioned_topic   : multiple pages on same topic spanning years — newer wins
#   evergreen         : old canonical doc; must survive recency demotion
#   recent_only       : single fresh page on a topic — recency irrelevant
#   old_only          : no recent content exists — recency cannot help (control)
QUERIES: List[Dict] = [
    {"q": "implement the DDN v26.3 release deployment process",
     "hypothesis": "GROUND TRUTH: DDN Release v26.3 Notes / CheckList / Dependencies / Testing all 0d old. Must rank in top-3 with recency on. Without recency, older 'DDN Release' pages from prior versions may surface.",
     "category": "pure_recency"},

    {"q": "add DPN field validations following the latest baseline",
     "hypothesis": "GROUND TRUTH: 'DPN Validations and Fields Baseline' (0d), 'DPN API Validation Errors and Messaging Guidelines' (6d). Must beat 'DPN Possible errors for a payment' (~1.9y old).",
     "category": "versioned_topic"},

    {"q": "set up the DPN v26.5 release prerequisites and dependencies",
     "hypothesis": "GROUND TRUTH: 'DPN GA Release [v26.5] Dependencies' (0d), 'DPN GA Release [v26.5] Notes' (0d), 'DPN GA Release [v26.5] Prod CheckList' (6d). Should dominate; previous DPN release pages are older.",
     "category": "pure_recency"},

    {"q": "investigate CkM database performance issues",
     "hypothesis": "GROUND TRUTH: 'CkM Database Performance Investigation (May 2026)' (1d), 'CKM Performance Test Results - April 2026' (~7d). Must rank top-2 over older CkM pages.",
     "category": "pure_recency"},

    {"q": "build the AvidX integration test plan",
     "hypothesis": "GROUND TRUTH: 'AvidX Integration Test Plan' (0d) is a single fresh canonical page. Should rank #1 trivially. Sanity test that recency doesn't break single-doc lookups.",
     "category": "recent_only"},

    {"q": "add MFA / authentication setup to the eChecksPro flow",
     "hypothesis": "GROUND TRUTH: 'Authentication/Authorization/Security' (0d, fresh), 'MFA Factor Enrolled (eChecksPro)' (~5y old), 'Okta MFA Offering' (~5.4y). Recency should promote the fresh auth doc to #1.",
     "category": "versioned_topic"},

    {"q": "what is the latest Looney Tunes sprint retrospective?",
     "hypothesis": "GROUND TRUTH: 8 Looney Tunes retros spanning 7d to 1923d. 'Looney Tunes Q2 Sprint Retrospective 5-12-26' (7d) should rank #1 with recency on; without recency, older retros may compete.",
     "category": "versioned_topic"},

    {"q": "how does the Mustang team plan and review sprints?",
     "hypothesis": "GROUND TRUTH: 25 Mustang retros spanning 8d to 1006d. Recent retros (Q2 Sprint 3, 4-13-26) should rank top with recency on.",
     "category": "versioned_topic"},

    {"q": "what is the current code review philosophy and standards?",
     "hypothesis": "GROUND TRUTH: 'Code Review Philosophy' (4d, fresh) — recently updated single page. Should rank #1 either way; tests that recency doesn't introduce noise on single-fresh-doc queries.",
     "category": "recent_only"},

    {"q": "create the rollback plan for a production deployment",
     "hypothesis": "GROUND TRUTH: 'Rollback Plan' (1d) + 'Premanufactured Prod Plan' (6d). Both fresh; should both rank top.",
     "category": "pure_recency"},

    {"q": "explain the permissions model for the check application",
     "hypothesis": "EVERGREEN. GROUND TRUTH: 'Permissions Specification' (~13.3y old) is the canonical doc. DECAY_FLOOR=0.5 must keep it in top-K — recent loosely-related auth pages should NOT push it out.",
     "category": "evergreen"},

    {"q": "explain the architecture of Digital Payments Exchange (DPX)",
     "hypothesis": "EVERGREEN + RECENT. GROUND TRUTH: 'Welcome to YOUR DPX Wiki' (6d, recently touched), 'What is Digital Payments Exchange(DPX)?' (~4.7y, canonical), 'The Basics of DPX' (~3.9y). Both fresh-touched wiki and canonical explainer should appear.",
     "category": "evergreen"},
]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def install_rewrite_cache():
    """Patch rag_service._rewrite_query so both A/B runs see identical query variants.

    Without this, each call independently asks Claude Haiku for 3 alternative
    queries and gets slightly different results, contaminating the comparison
    with rewrite nondeterminism. With the cache, the first call hits Haiku,
    every subsequent call for the same input returns the same variants.
    """
    cache: Dict[str, List[str]] = {}
    original = rag_service._rewrite_query

    def cached(query: str, user_id: Optional[str] = None):
        if query in cache:
            return cache[query]
        result = original(query, user_id=user_id)
        cache[query] = result
        return result

    rag_service._rewrite_query = cached
    return cache


def parse_ts(ts) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def age_days(chunk) -> Optional[int]:
    ts = parse_ts(chunk.get("source_updated_at"))
    if not ts:
        return None
    delta = datetime.now(timezone.utc) - ts
    return int(delta.total_seconds() // 86400)


def avg_age(chunks) -> Optional[float]:
    ages = [age_days(c) for c in chunks]
    ages = [a for a in ages if a is not None]
    if not ages:
        return None
    return sum(ages) / len(ages)


def median_age(chunks) -> Optional[int]:
    ages = sorted([a for a in (age_days(c) for c in chunks) if a is not None])
    if not ages:
        return None
    n = len(ages)
    return ages[n // 2] if n % 2 == 1 else (ages[n // 2 - 1] + ages[n // 2]) // 2


def run_search(query: str, w_temporal: float, project_id: str, user_id: str):
    return rag_service._multi_query_search(
        project_id=project_id,
        user_query=query,
        max_chunks=TOP_K,
        source_type=None,
        include_context=False,   # skip ±1 chunk expansion — faster, doesn't affect ranking
        user_id=user_id,
        w_temporal=w_temporal,
    )


def compare_one(qobj: Dict, project_id: str, user_id: str) -> Dict:
    q = qobj["q"]
    logger.info(f"  -> {q[:70]}")

    old = run_search(q, 0.0, project_id, user_id)
    new = run_search(q, W_TEMPORAL_PROMPT_ENHANCE, project_id, user_id)

    old_ids = [(r.get("source_id"), r.get("chunk_index")) for r in old]
    new_ids = [(r.get("source_id"), r.get("chunk_index")) for r in new]

    # Rank deltas relative to OLD pipeline.
    rows = []
    for new_rank, r in enumerate(new):
        key = (r.get("source_id"), r.get("chunk_index"))
        old_rank = old_ids.index(key) if key in old_ids else None
        rows.append({
            "new_rank": new_rank,
            "old_rank": old_rank,
            "title": r.get("title", "(untitled)"),
            "source_id": r.get("source_id"),
            "chunk_index": r.get("chunk_index"),
            "age_days": age_days(r),
            "rrf_score": float(r.get("rrf_score", 0)),
        })

    # Anything that fell OUT of the top-K under new — useful to see what was dropped.
    dropped = []
    for old_rank, r in enumerate(old):
        key = (r.get("source_id"), r.get("chunk_index"))
        if key not in new_ids:
            dropped.append({
                "old_rank": old_rank,
                "title": r.get("title", "(untitled)"),
                "source_id": r.get("source_id"),
                "age_days": age_days(r),
            })

    return {
        "query": q,
        "hypothesis": qobj["hypothesis"],
        "category": qobj["category"],
        "old_avg_age_days":    avg_age(old),
        "new_avg_age_days":    avg_age(new),
        "old_median_age_days": median_age(old),
        "new_median_age_days": median_age(new),
        "rows": rows,
        "dropped": dropped,
        "old_count": len(old),
        "new_count": len(new),
    }


# ------------------------------------------------------------------
# Console + Markdown rendering
# ------------------------------------------------------------------
def fmt_age(d: Optional[int]) -> str:
    if d is None:
        return "  ?  "
    if d < 30:
        return f"{d}d"
    if d < 365:
        return f"{d//30}mo"
    return f"{d/365:.1f}y"


def print_console_summary(results: List[Dict]):
    print("\n" + "=" * 100)
    print(f"  RECENCY COMPARISON SUMMARY  |  top-{TOP_K} per query  |  W_TEMPORAL_NEW=0.5  |  baseline W=0.0")
    print("=" * 100)

    # One-line summary per query
    print(f"\n  {'cat':<13} {'query':<55} {'avg age old':>12} {'avg age new':>12} {'shift':>8}")
    print("  " + "-" * 98)
    for r in results:
        shift = "  —" if r["old_avg_age_days"] is None or r["new_avg_age_days"] is None \
            else f"{r['new_avg_age_days'] - r['old_avg_age_days']:+.0f}d"
        old_a = "?" if r["old_avg_age_days"] is None else f"{r['old_avg_age_days']:.0f}d"
        new_a = "?" if r["new_avg_age_days"] is None else f"{r['new_avg_age_days']:.0f}d"
        print(f"  {r['category']:<13} {r['query'][:54]:<55} {old_a:>12} {new_a:>12} {shift:>8}")

    print("\n" + "=" * 100)
    print("  per-query rank changes")
    print("=" * 100)

    for r in results:
        print(f"\n  [{r['category']}] {r['query']}")
        print(f"  hypothesis: {r['hypothesis']}")
        if r["old_avg_age_days"] is not None and r["new_avg_age_days"] is not None:
            print(f"  avg age top-{TOP_K}: old={fmt_age(int(r['old_avg_age_days']))} -> new={fmt_age(int(r['new_avg_age_days']))}")

        if not r["rows"]:
            print("  (no results)")
            continue

        print(f"\n  {'#':>2}  {'new':>3} {'old':>3}  {'move':>6}  {'age':>6}  title")
        print("  " + "-" * 96)
        for row in r["rows"]:
            old_r = row["old_rank"]
            move = "(NEW!)" if old_r is None else f"{old_r - row['new_rank']:+d}".rjust(6)
            old_r_str = "—" if old_r is None else str(old_r + 1)
            print(f"  {row['new_rank']+1:>2}. "
                  f"{row['new_rank']+1:>3} {old_r_str:>3}  {move:>6}  {fmt_age(row['age_days']):>6}  "
                  f"{row['title'][:70]}")

        if r["dropped"]:
            print(f"\n  Dropped out of top-{TOP_K} by recency:")
            for d in r["dropped"]:
                print(f"    was #{d['old_rank']+1}  {fmt_age(d['age_days']):>6}  {d['title'][:70]}")


def save_markdown(results: List[Dict], out_path: str):
    """Same content as console summary, in markdown for sharing."""
    lines = []
    lines.append("# RAG Recency-Comparison Report — Digital Payments / Pair Programming\n")
    lines.append(f"- top-K per query: **{TOP_K}**")
    lines.append(f"- baseline pipeline: `w_temporal = 0.0` (no recency)")
    lines.append(f"- new pipeline:      `w_temporal = {W_TEMPORAL_PROMPT_ENHANCE}` (prompt-enhance setting)")
    lines.append(f"- decay-floor: `0.5`, half-life: `90d`, grace: `7d`")
    lines.append("")
    lines.append("Query rewrites are cached so both runs see identical variants — "
                 "the *only* differential between OLD and NEW is the recency multiplier.\n")

    # Summary table
    lines.append("## Summary\n")
    lines.append("| category | query | avg age (old) | avg age (new) | shift |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        old_a = "?" if r["old_avg_age_days"] is None else f"{r['old_avg_age_days']:.0f}d"
        new_a = "?" if r["new_avg_age_days"] is None else f"{r['new_avg_age_days']:.0f}d"
        shift = "—" if r["old_avg_age_days"] is None or r["new_avg_age_days"] is None \
            else f"{r['new_avg_age_days'] - r['old_avg_age_days']:+.0f}d"
        lines.append(f"| {r['category']} | {r['query']} | {old_a} | {new_a} | {shift} |")
    lines.append("")

    # Per-query detail
    for r in results:
        lines.append(f"## [{r['category']}] {r['query']}\n")
        lines.append(f"**Hypothesis:** {r['hypothesis']}\n")
        if r["old_avg_age_days"] is not None and r["new_avg_age_days"] is not None:
            lines.append(f"- top-{TOP_K} avg age: **{r['old_avg_age_days']:.0f}d → {r['new_avg_age_days']:.0f}d** "
                         f"(shift {r['new_avg_age_days'] - r['old_avg_age_days']:+.0f}d)")
        if r["old_median_age_days"] is not None and r["new_median_age_days"] is not None:
            lines.append(f"- top-{TOP_K} median age: **{r['old_median_age_days']}d → {r['new_median_age_days']}d**")
        lines.append("")

        lines.append("| new rank | old rank | move | age | title | source_id |")
        lines.append("|---|---|---|---|---|---|")
        for row in r["rows"]:
            old_r = row["old_rank"]
            move = "(NEW!)" if old_r is None else f"{old_r - row['new_rank']:+d}"
            old_r_str = "—" if old_r is None else str(old_r + 1)
            title = row["title"].replace("|", "\\|")
            lines.append(f"| {row['new_rank']+1} | {old_r_str} | {move} | "
                         f"{fmt_age(row['age_days'])} | {title} | `{row['source_id']}` |")

        if r["dropped"]:
            lines.append(f"\n**Dropped out of top-{TOP_K} by recency:**\n")
            lines.append("| was rank | age | title |")
            lines.append("|---|---|---|")
            for d in r["dropped"]:
                title = d["title"].replace("|", "\\|")
                lines.append(f"| {d['old_rank']+1} | {fmt_age(d['age_days'])} | {title} |")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Compare old vs new RAG pipeline on Digital Payments queries.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID,
                        help=f"Default: {DEFAULT_PROJECT_ID} (Aman / Digital Payments)")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID,
                        help=f"Default: {DEFAULT_USER_ID} (Aman)")
    parser.add_argument("--output", default="eval_recency_report.md",
                        help="Path for markdown report. Default: eval_recency_report.md")
    args = parser.parse_args()

    print(f"\nProject ID: {args.project_id}")
    print(f"User ID:    {args.user_id}")
    print(f"Running {len(QUERIES)} queries x 2 pipelines (old vs new) = {len(QUERIES) * 2} searches")
    print(f"Query rewrites are cached so both runs use identical variants.\n")

    install_rewrite_cache()

    results = []
    for i, q in enumerate(QUERIES, 1):
        logger.info(f"[{i}/{len(QUERIES)}] {q['category']}")
        try:
            results.append(compare_one(q, args.project_id, args.user_id))
        except Exception as e:
            logger.error(f"  query failed: {e}")

    print_console_summary(results)
    save_markdown(results, args.output)
    print(f"\nFull markdown report: {args.output}\n")


if __name__ == "__main__":
    main()
