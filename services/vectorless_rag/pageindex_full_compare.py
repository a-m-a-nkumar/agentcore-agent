"""
Full multi-parameter comparison: OUR pipeline (hybrid LLM+BM25) vs PageIndex tree_search,
on the 43 held-out cases. Captures every parameter that matters for tree-RAG retrieval:
recall, precision, exact-match, abstention, result-set size, latency, LLM calls.

Run from backend root:
    python -m services.vectorless_rag.pageindex_full_compare
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .bm25 import BM25Index
from .llm import GatewayLLM
from .pageindex_compare import pageindex_route
from .router import VeloxGuideRouter
from .tree import GuideTree

HERE = Path(__file__).parent
CASES = json.loads((HERE / "generated_cases.json").read_text(encoding="utf-8"))


def _rec(p, e):
    return (1.0 if not p else 0.0) if not e else (1.0 if (p & e) else 0.0)


def _prec(p, e):
    return (1.0 if not e else 0.0) if not p else len(p & e) / len(p)


def main() -> None:
    tree = GuideTree.from_file(HERE / "velox_guide_tree.json")
    bm = BM25Index(tree)
    ours = VeloxGuideRouter(tree=tree, routing_mode="hybrid")
    pi_llm = GatewayLLM()

    ans = [c for c in CASES if c["expected"]]
    oos = [c for c in CASES if not c["expected"]]
    o = {"rec": [], "prec": [], "exact": [], "size": [], "ms": [], "r2": []}
    p = {"rec": [], "prec": [], "exact": [], "size": [], "ms": []}
    o_oos = p_oos = 0

    print(f"Running {len(CASES)} held-out cases through both systems...\n")
    for c in CASES:
        exp = set(c["expected"])
        t = time.perf_counter(); on, _ = ours._route(c["query"]); o["ms"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); pn = pageindex_route(tree, pi_llm, c["query"]); p["ms"].append((time.perf_counter() - t) * 1000)
        op, pp = set(on), set(pn)
        top2 = {nid for nid, _ in bm.rank(c["query"])[:2]}

        if exp:
            o["rec"].append(_rec(op, exp)); o["prec"].append(_prec(op, exp))
            o["exact"].append(op == exp); o["size"].append(len(op)); o["r2"].append(1.0 if ((op | top2) & exp) else 0.0)
            p["rec"].append(_rec(pp, exp)); p["prec"].append(_prec(pp, exp))
            p["exact"].append(pp == exp); p["size"].append(len(pp))
        else:
            o_oos += (not op); p_oos += (not pp)
            o["size"].append(len(op)); p["size"].append(len(pp))

    def m(x):
        return statistics.mean(x) if x else 0.0

    print(f"{'parameter':<26}{'OURS (hybrid)':>16}{'PageIndex tree_search':>26}")
    print("-" * 70)
    print(f"{'recall (answerable)':<26}{m(o['rec']):>16.2f}{m(p['rec']):>26.2f}")
    print(f"{'recall@2 (ours +bm25)':<26}{m(o['r2']):>16.2f}{'(set, n/a)':>26}")
    print(f"{'precision (answerable)':<26}{m(o['prec']):>16.2f}{m(p['prec']):>26.2f}")
    print(f"{'exact-match':<26}{m(o['exact']):>16.2f}{m(p['exact']):>26.2f}")
    print(f"{'avg result-set size':<26}{m(o['size']):>16.2f}{m(p['size']):>26.2f}")
    print(f"{'out-of-scope correct':<26}{f'{o_oos}/{len(oos)}':>16}{f'{p_oos}/{len(oos)}':>26}")
    print(f"{'avg routing latency ms':<26}{m(o['ms']):>16.0f}{m(p['ms']):>26.0f}")
    print(f"{'routing LLM calls/query':<26}{'1 (+BM25 free)':>16}{'1':>26}")
    print(f"\nanswerable={len(ans)}  out-of-scope={len(oos)}  (routing only; +1 synth call each for a full answer)")


if __name__ == "__main__":
    main()
