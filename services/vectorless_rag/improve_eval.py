"""
Did the v2 scope-gate fix the held-out false-abstention without hurting precision?
Runs v1 (original) vs v2 (scope-gated) ROUTING on ALL 43 held-out cases.

Run from backend root:
    python -m services.vectorless_rag.improve_eval
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .llm import GatewayLLM
from .router import VeloxGuideRouter

CASES = json.loads((Path(__file__).parent / "generated_cases.json").read_text(encoding="utf-8"))


def _recall(pred, exp):
    if not exp:
        return 1.0 if not pred else 0.0
    return len(pred & exp) / len(exp)


def _precision(pred, exp):
    if not pred:
        return 1.0 if not exp else 0.0
    return len(pred & exp) / len(pred)


def _eval(router):
    rows = []
    for c in CASES:
        nodes, _ = router._route(c["query"])
        pred, exp = set(nodes), set(c["expected"])
        rows.append({
            "cat": c["category"], "exact": pred == exp,
            "rec": _recall(pred, exp), "prec": _precision(pred, exp),
            "empty": not pred, "answerable": bool(exp),
        })
    return rows


def _summ(rows, label):
    n = len(rows)
    ans = [r for r in rows if r["answerable"]]
    oos = [r for r in rows if not r["answerable"]]
    false_abstain = sum(1 for r in ans if r["empty"])  # said [] when an answer existed
    return {
        "label": label, "n": n,
        "exact": sum(r["exact"] for r in rows),
        "rec": statistics.mean(r["rec"] for r in rows),
        "prec": statistics.mean(r["prec"] for r in rows),
        "false_abstain": false_abstain, "ans": len(ans),
        "oos_ok": sum(r["exact"] for r in oos), "oos": len(oos),
    }


def main() -> None:
    tree = GuideTree_load()
    v1 = VeloxGuideRouter(tree=tree, routing_mode="v1")
    v2 = VeloxGuideRouter(tree=tree, routing_mode="v2")
    print(f"Held-out: {len(CASES)} cases. Comparing v1 (original) vs v2 (scope-gated)...\n")

    r1 = _eval(v1)
    r2 = _eval(v2)
    s1, s2 = _summ(r1, "v1 original"), _summ(r2, "v2 scope-gate")

    print(f"{'metric':<26}{'v1 original':>16}{'v2 scope-gate':>16}")
    print("-" * 58)
    print(f"{'exact-match':<26}{f'{s1[\"exact\"]}/{s1[\"n\"]}':>16}{f'{s2[\"exact\"]}/{s2[\"n\"]}':>16}")
    print(f"{'mean recall':<26}{s1['rec']:>16.2f}{s2['rec']:>16.2f}")
    print(f"{'mean precision':<26}{s1['prec']:>16.2f}{s2['prec']:>16.2f}")
    print(f"{'false abstentions':<26}{f'{s1[\"false_abstain\"]}/{s1[\"ans\"]}':>16}{f'{s2[\"false_abstain\"]}/{s2[\"ans\"]}':>16}")
    print(f"{'out-of-scope correct':<26}{f'{s1[\"oos_ok\"]}/{s1[\"oos\"]}':>16}{f'{s2[\"oos_ok\"]}/{s2[\"oos\"]}':>16}")
    print("\n(false abstentions = returned [] when an answer existed — the bug we're fixing.")
    print(" out-of-scope correct = correctly returned [] — must NOT regress.)")


def GuideTree_load():
    from .tree import GuideTree
    return GuideTree.from_file(Path(__file__).parent / "velox_guide_tree.json")


if __name__ == "__main__":
    main()
