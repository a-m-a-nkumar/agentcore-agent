"""
Eval harness (spec §8). Runs the §12 cases live through the router and reports
exact-match / recall / precision on `nodes`, plus latency and llm_calls, with a
per-failure trace dump. Abstain cases also assert the answer says "coming soon";
out-of-scope asserts an empty pick.

Run from the backend root:
    python -m services.vectorless_rag.evaluate
"""

from __future__ import annotations

import statistics

from .eval_cases import CASES
from .router import VeloxGuideRouter

ABSTAIN_MARKERS = ("coming soon", "not yet", "planned", "not available")


def _recall(pred: set[str], exp: set[str]) -> float:
    if not exp:
        return 1.0 if not pred else 0.0
    return len(pred & exp) / len(exp)


def _precision(pred: set[str], exp: set[str]) -> float:
    if not pred:
        return 1.0 if not exp else 0.0
    return len(pred & exp) / len(pred)


def main() -> None:
    router = VeloxGuideRouter()
    print(f"Tree: {len(router.tree)} nodes. Running {len(CASES)} eval cases live...\n")
    print(f"{'result':<7}{'category':<16}{'rec':>4}{'prec':>5}{'ms':>7}{'#':>3}  query -> nodes")
    print("-" * 100)

    rows = []
    for c in CASES:
        r = router.ask(c["query"])
        pred, exp = set(r["nodes"]), set(c["expected"])
        rec, prec = _recall(pred, exp), _precision(pred, exp)
        exact = pred == exp

        ok = exact
        note = ""
        if c["category"] == "abstain":
            said = any(m in r["answer"].lower() for m in ABSTAIN_MARKERS)
            ok = exact and said
            if not said:
                note = " [missing 'coming soon' phrasing]"
        elif c["category"] == "out-of-scope":
            ok = not pred
        elif c["category"] in ("multi-part", "broad"):
            ok = rec >= 0.5  # partial credit

        print(f"{'PASS' if ok else 'FAIL':<7}{c['category']:<16}{rec:>4.1f}{prec:>5.1f}"
              f"{r['latency_ms']:>7.0f}{r['llm_calls']:>3}  {c['query'][:40]!r} -> {r['nodes']}{note}")
        if not ok:
            print(f"        expected {c['expected']} | trace: {r['trace']}")
        rows.append({"case": c, "ok": ok, "rec": rec, "prec": prec, "r": r})

    # ── aggregate ──────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("AGGREGATE")
    print("=" * 100)
    n = len(rows)
    passed = sum(x["ok"] for x in rows)
    exact = sum(set(x["r"]["nodes"]) == set(x["case"]["expected"]) for x in rows)
    print(f"Pass (category-aware): {passed}/{n} = {passed/n:.0%}")
    print(f"Exact-match on nodes : {exact}/{n} = {exact/n:.0%}")
    print(f"Mean recall          : {statistics.mean(x['rec'] for x in rows):.2f}")
    print(f"Mean precision       : {statistics.mean(x['prec'] for x in rows):.2f}")

    print("\nBy category:")
    cats: dict[str, list] = {}
    for x in rows:
        cats.setdefault(x["case"]["category"], []).append(x["ok"])
    for cat, oks in sorted(cats.items()):
        print(f"  {cat:<16} {sum(oks)}/{len(oks)}")

    lat = [x["r"]["latency_ms"] for x in rows]
    calls = [x["r"]["llm_calls"] for x in rows]
    print(f"\nLatency ms: avg {statistics.mean(lat):.0f}  p50 {statistics.median(lat):.0f}  max {max(lat):.0f}")
    print(f"LLM calls : avg {statistics.mean(calls):.1f}  max {max(calls)}  (target ~2)")
    print("\n(route = Haiku, synth = Sonnet. Fix failures by sharpening node summaries, not code.)")


if __name__ == "__main__":
    main()
