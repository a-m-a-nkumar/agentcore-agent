"""
Measure the two fixes on the 43 held-out cases, by depth (where the problem was):
  baseline (known)        : 63% exact, d2 11/23, d3 3/7   [pre-sharpen tree, no narrow]
  A = sharpened summaries : v1, parent_narrow OFF   (effect of the tree change)
  B = sharpened + narrow  : v1, parent_narrow ON    (+ routing change)

Run from backend root:
    python -m services.vectorless_rag.improved_eval
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .router import VeloxGuideRouter
from .tree import GuideTree

HERE = Path(__file__).parent
CASES = json.loads((HERE / "generated_cases.json").read_text(encoding="utf-8"))


def _recall(p, e):
    return (1.0 if not p else 0.0) if not e else len(p & e) / len(e)


def _precision(p, e):
    return (1.0 if not e else 0.0) if not p else len(p & e) / len(p)


def _eval(router, label):
    rows = []
    for c in CASES:
        nodes, _ = router._route(c["query"])
        pred, exp = set(nodes), set(c["expected"])
        rows.append({"cat": c["category"], "exact": pred == exp,
                     "rec": _recall(pred, exp), "prec": _precision(pred, exp),
                     "empty": not pred, "ans": bool(exp)})
    return rows


def _report(rows, label):
    n = len(rows)
    by = {}
    for r in rows:
        by.setdefault(r["cat"], []).append(r["exact"])
    ans = [r for r in rows if r["ans"]]
    oos = [r for r in rows if not r["ans"]]
    print(f"\n{label}")
    print(f"  exact-match : {sum(r['exact'] for r in rows)}/{n} = {sum(r['exact'] for r in rows)/n:.0%}")
    print(f"  recall/prec : {statistics.mean(r['rec'] for r in rows):.2f} / {statistics.mean(r['prec'] for r in rows):.2f}")
    print(f"  false-abstain: {sum(1 for r in ans if r['empty'])}/{len(ans)}   out-of-scope: {sum(r['exact'] for r in oos)}/{len(oos)}")
    print("  by depth: " + "  ".join(f"{k}={sum(v)}/{len(v)}" for k, v in sorted(by.items())))


def main() -> None:
    tree = GuideTree.from_file(HERE / "velox_guide_tree.json")
    print("BASELINE (pre-sharpen, no narrow): exact 27/43=63%  by depth: leaf-d2=11/23  leaf-d3=3/7")

    a = VeloxGuideRouter(tree=tree, routing_mode="v1", enable_parent_narrow=False)
    b = VeloxGuideRouter(tree=tree, routing_mode="v1", enable_parent_narrow=True)
    _report(_eval(a, "A"), "A = sharpened summaries only (v1, narrow OFF)")
    _report(_eval(b, "B"), "B = sharpened + parent-narrow (v1, narrow ON)")


if __name__ == "__main__":
    main()
