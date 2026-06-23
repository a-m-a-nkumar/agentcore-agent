"""
Multi-parameter comparison on the NORMAL realistic query set (how users actually ask),
ours (hybrid LLM+BM25) vs PageIndex tree_search. Acceptable-set scoring + out-of-scope.

Run from backend root:
    python -m services.vectorless_rag.pageindex_normal_compare
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from .bm25 import BM25Index
from .llm import GatewayLLM
from .normal_eval import CASES as NORMAL
from .pageindex_compare import pageindex_route
from .recall_eval import OOS
from .router import VeloxGuideRouter
from .tree import GuideTree

CASES = NORMAL + OOS  # (query, acceptable_set); OOS acceptable_set is empty


def _rec(p, e):
    return (1.0 if not p else 0.0) if not e else (1.0 if (p & e) else 0.0)


def _prec(p, e):
    return (1.0 if not e else 0.0) if not p else len(p & e) / len(p)


def main() -> None:
    tree = GuideTree.from_file(Path(__file__).parent / "velox_guide_tree.json")
    bm = BM25Index(tree)
    ours = VeloxGuideRouter(tree=tree, routing_mode="hybrid")
    pi_llm = GatewayLLM()

    ans = [c for c in CASES if c[1]]
    oos = [c for c in CASES if not c[1]]
    o = {"rec": [], "prec": [], "exact": [], "size": [], "ms": [], "r2": []}
    p = {"rec": [], "prec": [], "exact": [], "size": [], "ms": []}
    o_oos = p_oos = 0

    print(f"NORMAL set: {len(CASES)} cases ({len(ans)} realistic + {len(oos)} out-of-scope)\n")
    for q, acc in CASES:
        exp = set(acc)
        t = time.perf_counter(); on, _ = ours._route(q); o["ms"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter(); pn = pageindex_route(tree, pi_llm, q); p["ms"].append((time.perf_counter() - t) * 1000)
        op, pp = set(on), set(pn)
        top2 = {nid for nid, _ in bm.rank(q)[:2]}
        if exp:
            o["rec"].append(_rec(op, exp)); o["prec"].append(_prec(op, exp))
            o["exact"].append(1.0 if (op and op <= exp) else 0.0); o["size"].append(len(op))
            o["r2"].append(1.0 if ((op | top2) & exp) else 0.0)
            p["rec"].append(_rec(pp, exp)); p["prec"].append(_prec(pp, exp))
            p["exact"].append(1.0 if (pp and pp <= exp) else 0.0); p["size"].append(len(pp))
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
    print(f"{'precise pick (<=acceptable)':<26}{m(o['exact']):>16.2f}{m(p['exact']):>26.2f}")
    print(f"{'avg result-set size':<26}{m(o['size']):>16.2f}{m(p['size']):>26.2f}")
    print(f"{'out-of-scope correct':<26}{f'{o_oos}/{len(oos)}':>16}{f'{p_oos}/{len(oos)}':>26}")
    print(f"{'avg routing latency ms':<26}{m(o['ms']):>16.0f}{m(p['ms']):>26.0f}")
    print(f"{'routing LLM calls/query':<26}{'1 (+BM25 free)':>16}{'1':>26}")


if __name__ == "__main__":
    main()
