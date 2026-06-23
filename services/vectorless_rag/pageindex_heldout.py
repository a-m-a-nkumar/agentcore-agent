"""
Our flat router vs PageIndex tree_search on 10 HELD-OUT cases (from generated_cases.json,
questions written from node details, summaries NOT tuned to them). Routing only.

Reports recall + precision + exact-match (recall matters because PageIndex's tree_search
is high-recall by design: "find ALL nodes likely to contain the answer").

Run from backend root:
    python -m services.vectorless_rag.pageindex_heldout
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .llm import GatewayLLM
from .pageindex_compare import pageindex_route
from .router import VeloxGuideRouter

ALL = json.loads((Path(__file__).parent / "generated_cases.json").read_text(encoding="utf-8"))
# Deterministic spread: 8 leaves across depths + 2 out-of-scope (indices into ALL).
PICK = [0, 4, 8, 12, 16, 20, 24, 30, 38, 41]
CASES = [ALL[i] for i in PICK]


def _recall(pred, exp):
    if not exp:
        return 1.0 if not pred else 0.0
    return len(pred & exp) / len(exp)


def _precision(pred, exp):
    if not pred:
        return 1.0 if not exp else 0.0
    return len(pred & exp) / len(pred)


def main() -> None:
    ours = VeloxGuideRouter()
    tree = ours.tree
    pi_llm = GatewayLLM()

    print("HELD-OUT routing: our flat vs PageIndex tree_search (untuned summaries, same model)\n")
    print(f"{'cat':<10}{'query':<46}{'OURS':<26}{'PAGEINDEX (count)':<20}")
    print("-" * 104)

    rows = []
    for c in CASES:
        exp = set(c["expected"])
        our_nodes, _ = ours._route(c["query"])
        pi_nodes = pageindex_route(tree, pi_llm, c["query"])
        op, pp = set(our_nodes), set(pi_nodes)
        rows.append({
            "cat": c["category"],
            "our_rec": _recall(op, exp), "our_prec": _precision(op, exp), "our_exact": op == exp,
            "pi_rec": _recall(pp, exp), "pi_prec": _precision(pp, exp), "pi_exact": pp == exp,
        })
        hit = "found" if (exp and (exp & pp)) or (not exp and not pp) else "MISS "
        print(f"{c['category']:<10}{c['query'][:44]!r:<46}"
              f"{(('OK ' if op == exp else 'X  ') + str(our_nodes))[:24]:<26}"
              f"{hit} {len(pi_nodes)} nodes")

    n = len(rows)
    print("\n" + "=" * 104)
    print(f"{'metric':<22}{'OURS (flat)':>22}{'PAGEINDEX tree_search':>26}")
    print("-" * 104)
    f = lambda l, a, b: print(f"{l:<22}{a:>22}{b:>26}")
    f("exact-match", f"{sum(r['our_exact'] for r in rows)}/{n}", f"{sum(r['pi_exact'] for r in rows)}/{n}")
    f("mean recall", f"{statistics.mean(r['our_rec'] for r in rows):.2f}", f"{statistics.mean(r['pi_rec'] for r in rows):.2f}")
    f("mean precision", f"{statistics.mean(r['our_prec'] for r in rows):.2f}", f"{statistics.mean(r['pi_prec'] for r in rows):.2f}")
    # abstention on the 2 out-of-scope cases
    oos = [r for r in rows if r["cat"] == "out-of-scope"]
    f("out-of-scope correct", f"{sum(r['our_exact'] for r in oos)}/{len(oos)}", f"{sum(r['pi_exact'] for r in oos)}/{len(oos)}")
    print("\nPageIndex returns a high-recall candidate SET (by design); ours returns a precise pick + abstains.")


if __name__ == "__main__":
    main()
