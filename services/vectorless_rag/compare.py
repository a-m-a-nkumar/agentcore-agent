"""
Head-to-head on the §12 set, same 47-node tree, same gateway:
  A = flat one-shot  (router.VeloxGuideRouter)
  B = greedy descent (router_descent.DescentRouter, ported from velox_vectorless_rag)

Reports per-case routing result + the aggregate: accuracy (category-aware),
exact-match, recall/precision, latency, llm_calls.

Run from backend root:
    python -m services.vectorless_rag.compare
"""

from __future__ import annotations

import statistics

from .eval_cases import CASES
from .router import VeloxGuideRouter
from .router_descent import DescentRouter

ABSTAIN_MARKERS = ("coming soon", "not yet", "planned", "not available")


def _recall(pred, exp):
    if not exp:
        return 1.0 if not pred else 0.0
    return len(pred & exp) / len(exp)


def _precision(pred, exp):
    if not pred:
        return 1.0 if not exp else 0.0
    return len(pred & exp) / len(pred)


def _passes(case, r):
    pred, exp = set(r["nodes"]), set(case["expected"])
    cat = case["category"]
    if cat == "abstain":
        return pred == exp and any(m in r["answer"].lower() for m in ABSTAIN_MARKERS)
    if cat == "out-of-scope":
        return not pred
    if cat in ("multi-part", "broad"):
        return _recall(pred, exp) >= 0.5
    return pred == exp


def _run(router):
    rows = []
    for c in CASES:
        r = router.ask(c["query"])
        pred, exp = set(r["nodes"]), set(c["expected"])
        rows.append({
            "case": c, "nodes": r["nodes"], "ok": _passes(c, r),
            "exact": pred == exp, "rec": _recall(pred, exp), "prec": _precision(pred, exp),
            "ms": r["latency_ms"], "calls": r["llm_calls"],
        })
    return rows


def _agg(rows):
    n = len(rows)
    return {
        "pass": sum(x["ok"] for x in rows), "exact": sum(x["exact"] for x in rows), "n": n,
        "rec": statistics.mean(x["rec"] for x in rows), "prec": statistics.mean(x["prec"] for x in rows),
        "avg_ms": statistics.mean(x["ms"] for x in rows), "p50_ms": statistics.median(x["ms"] for x in rows),
        "max_ms": max(x["ms"] for x in rows),
        "avg_calls": statistics.mean(x["calls"] for x in rows), "max_calls": max(x["calls"] for x in rows),
    }


def main() -> None:
    a = VeloxGuideRouter()
    b = DescentRouter(tree=a.tree)  # share the loaded+sharpened tree

    print(f"Tree: {len(a.tree)} nodes. §12 set: {len(CASES)} cases.  A=flat one-shot  B=greedy descent\n")
    rows_a = _run(a)
    rows_b = _run(b)

    print(f"{'category':<16}| {'A':<5}{'A nodes':<28}| {'B':<5}{'B nodes':<28}")
    print("-" * 92)
    for ra, rb in zip(rows_a, rows_b):
        c = ra["case"]
        print(f"{c['category']:<16}| "
              f"{'OK' if ra['ok'] else 'X':<5}{str(ra['nodes'])[:26]:<28}| "
              f"{'OK' if rb['ok'] else 'X':<5}{str(rb['nodes'])[:26]:<28}")
        if not (ra["ok"] and rb["ok"]):
            print(f"{'':<16}  query={c['query'][:60]!r}  expected={c['expected']}")

    sa, sb = _agg(rows_a), _agg(rows_b)
    print("\n" + "=" * 92)
    print(f"{'metric':<22}{'A: flat one-shot':>22}{'B: greedy descent':>22}")
    print("-" * 92)
    rowfmt = lambda label, va, vb: print(f"{label:<22}{va:>22}{vb:>22}")
    rowfmt("accuracy (cat-aware)", f"{sa['pass']}/{sa['n']} ({sa['pass']/sa['n']:.0%})", f"{sb['pass']}/{sb['n']} ({sb['pass']/sb['n']:.0%})")
    rowfmt("exact-match", f"{sa['exact']}/{sa['n']} ({sa['exact']/sa['n']:.0%})", f"{sb['exact']}/{sb['n']} ({sb['exact']/sb['n']:.0%})")
    rowfmt("mean recall", f"{sa['rec']:.2f}", f"{sb['rec']:.2f}")
    rowfmt("mean precision", f"{sa['prec']:.2f}", f"{sb['prec']:.2f}")
    rowfmt("avg latency ms", f"{sa['avg_ms']:.0f}", f"{sb['avg_ms']:.0f}")
    rowfmt("p50 latency ms", f"{sa['p50_ms']:.0f}", f"{sb['p50_ms']:.0f}")
    rowfmt("max latency ms", f"{sa['max_ms']:.0f}", f"{sb['max_ms']:.0f}")
    rowfmt("avg LLM calls", f"{sa['avg_calls']:.1f}", f"{sb['avg_calls']:.1f}")
    rowfmt("max LLM calls", f"{sa['max_calls']}", f"{sb['max_calls']}")
    print("\n(A: route=Haiku + synth=Sonnet, 2 calls. B: decompose + 1-5 hops + synth. Same tree & gateway.)")


if __name__ == "__main__":
    main()
