"""
Run the HELD-OUT generated set (generated_cases.json) through both routers and
compare. Same tree/gateway. This is the unbiased check: questions were generated
from node details (not summaries) and summaries were NOT tuned against them.

Run from backend root:
    python -m services.vectorless_rag.bench_generated
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .compare import _passes, _precision, _recall
from .router import VeloxGuideRouter
from .router_descent import DescentRouter

CASES = json.loads((Path(__file__).parent / "generated_cases.json").read_text(encoding="utf-8"))


def _run(router, label):
    rows = []
    for i, c in enumerate(CASES, 1):
        r = router.ask(c["query"])
        pred, exp = set(r["nodes"]), set(c["expected"])
        rows.append({
            "case": c, "nodes": r["nodes"], "ok": _passes(c, r),
            "exact": pred == exp, "rec": _recall(pred, exp), "prec": _precision(pred, exp),
            "ms": r["latency_ms"], "calls": r["llm_calls"],
        })
        print(f"  [{label}] {i:>2}/{len(CASES)} {'OK' if rows[-1]['ok'] else 'X '} "
              f"{c['query'][:46]!r} exp={c['expected']} got={r['nodes']}", flush=True)
    return rows


def _agg(rows):
    n = len(rows)
    return {
        "pass": sum(x["ok"] for x in rows), "n": n,
        "exact": sum(x["exact"] for x in rows),
        "rec": statistics.mean(x["rec"] for x in rows),
        "prec": statistics.mean(x["prec"] for x in rows),
        "avg_ms": statistics.mean(x["ms"] for x in rows),
        "avg_calls": statistics.mean(x["calls"] for x in rows),
    }


def main() -> None:
    a = VeloxGuideRouter()
    b = DescentRouter(tree=a.tree)
    print(f"HELD-OUT set: {len(CASES)} cases. Running flat (A)...")
    rows_a = _run(a, "A")
    print("Running descent (B)...")
    rows_b = _run(b, "B")

    sa, sb = _agg(rows_a), _agg(rows_b)
    print("\n" + "=" * 80)
    print(f"{'metric':<22}{'A: flat one-shot':>22}{'B: greedy descent':>22}")
    print("-" * 80)
    f = lambda lbl, va, vb: print(f"{lbl:<22}{va:>22}{vb:>22}")
    f("accuracy (cat-aware)", f"{sa['pass']}/{sa['n']} ({sa['pass']/sa['n']:.0%})", f"{sb['pass']}/{sb['n']} ({sb['pass']/sb['n']:.0%})")
    f("exact-match", f"{sa['exact']}/{sa['n']} ({sa['exact']/sa['n']:.0%})", f"{sb['exact']}/{sb['n']} ({sb['exact']/sb['n']:.0%})")
    f("mean recall", f"{sa['rec']:.2f}", f"{sb['rec']:.2f}")
    f("mean precision", f"{sa['prec']:.2f}", f"{sb['prec']:.2f}")
    f("avg latency ms", f"{sa['avg_ms']:.0f}", f"{sb['avg_ms']:.0f}")
    f("avg LLM calls", f"{sa['avg_calls']:.1f}", f"{sb['avg_calls']:.1f}")

    # accuracy by depth bucket (flat) — does it hold deep in the tree?
    print("\nFlat accuracy by source depth:")
    by_d: dict = {}
    for x in rows_a:
        d = x["case"]["category"]
        by_d.setdefault(d, []).append(x["ok"])
    for d, oks in sorted(by_d.items()):
        print(f"  {d:<14} {sum(oks)}/{len(oks)}")


if __name__ == "__main__":
    main()
