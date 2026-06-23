"""
Recall@k eval — the right metric for a user guide (poor recall = users miss info).
Compares v1 (LLM only) vs hybrid (LLM + BM25) on realistic queries + out-of-scope.

  recall@1 = the single pick is a defensible section
  recall@2 = a defensible section is in {pick} U {BM25 top-2}

Run from backend root:
    python -m services.vectorless_rag.recall_eval
"""

from __future__ import annotations

from pathlib import Path

from .bm25 import BM25Index
from .normal_eval import CASES as NORMAL
from .router import VeloxGuideRouter
from .tree import GuideTree

# realistic queries (acceptable-set) + a few genuine out-of-scope (expected empty)
OOS = [
    ("how do I reset my password?", set()),
    ("how do I deploy to a Kubernetes cluster?", set()),
    ("what does Velox cost?", set()),
    ("how do I message a teammate?", set()),
]
CASES = NORMAL + OOS


def main() -> None:
    tree = GuideTree.from_file(Path(__file__).parent / "velox_guide_tree.json")
    bm = BM25Index(tree)
    v1 = VeloxGuideRouter(tree=tree, routing_mode="v1", enable_parent_narrow=False)
    hy = VeloxGuideRouter(tree=tree, routing_mode="hybrid")

    print(f"Recall@k: v1 (LLM) vs hybrid (LLM+BM25) on {len(CASES)} cases "
          f"({len(NORMAL)} realistic + {len(OOS)} out-of-scope)\n")
    print(f"{'query':<46}{'v1@1':<8}{'hy@1':<8}{'hy@2':<8}")
    print("-" * 74)

    agg = {"v1@1": 0, "hy@1": 0, "hy@2": 0, "v1_falseabstain": 0, "hy_falseabstain": 0,
           "v1_oos": 0, "hy_oos": 0}
    for q, ok in CASES:
        answerable = bool(ok)
        v1_nodes, _ = v1._route(q)
        hy_nodes, _ = hy._route(q)
        top2 = {nid for nid, _ in bm.rank(q)[:2]}
        cand2 = set(hy_nodes) | top2

        v1h1 = bool(set(v1_nodes) & ok) if answerable else (not v1_nodes)
        hyh1 = bool(set(hy_nodes) & ok) if answerable else (not hy_nodes)
        hyh2 = bool(cand2 & ok) if answerable else (not hy_nodes)
        agg["v1@1"] += v1h1; agg["hy@1"] += hyh1; agg["hy@2"] += hyh2
        if answerable:
            agg["v1_falseabstain"] += (not v1_nodes)
            agg["hy_falseabstain"] += (not hy_nodes)
        else:
            agg["v1_oos"] += (not v1_nodes)
            agg["hy_oos"] += (not hy_nodes)

        mark = lambda x: "OK " if x else "X  "
        print(f"{q[:44]:<46}{mark(v1h1):<8}{mark(hyh1):<8}{mark(hyh2):<8}")

    n = len(CASES)
    na = len(NORMAL)
    no = len(OOS)
    print("-" * 74)
    print(f"\nrecall@1  v1: {agg['v1@1']}/{n} ({agg['v1@1']/n:.0%})   "
          f"hybrid: {agg['hy@1']}/{n} ({agg['hy@1']/n:.0%})")
    print(f"recall@2  hybrid: {agg['hy@2']}/{n} ({agg['hy@2']/n:.0%})")
    print(f"false-abstentions (answerable) v1: {agg['v1_falseabstain']}/{na}   "
          f"hybrid: {agg['hy_falseabstain']}/{na}")
    print(f"out-of-scope correct           v1: {agg['v1_oos']}/{no}   hybrid: {agg['hy_oos']}/{no}")
    print("\n(BM25 adds 0 LLM calls; it rescues clear keyword matches + breaks sibling ties.)")


if __name__ == "__main__":
    main()
