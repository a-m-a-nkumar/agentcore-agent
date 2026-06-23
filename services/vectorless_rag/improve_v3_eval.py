"""
Generate a FRESH 10-case set (new phrasings, targeting the confused clusters +
out-of-scope) and benchmark the three routing modes:
  v1 = original         (abstain == uncertainty, the bug)
  v2 = scope-gated      (abstain only when out of scope)
  v3 = recall-then-narrow (+ sharpened sibling summaries)

Questions are generated from node DETAILS (not summaries), so the sharpened summaries
aren't leaked into the test. Cached to generated_cases_v3_10.json for reproducibility.

Run from backend root:
    python -m services.vectorless_rag.improve_v3_eval
"""

from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .generate_cases import GEN_SYSTEM
from .llm import GatewayLLM
from .router import VeloxGuideRouter
from .tree import GuideTree

HERE = Path(__file__).parent
CACHE = HERE / "generated_cases_v3_10.json"

# 8 leaves (incl. the confused sibling clusters) + 2 fresh out-of-scope.
NODES = [
    "brd-confluence-tab", "brd-push-confluence", "arch-diagrams", "arch-sad",
    "testing-mcp-configured", "testing-mcp-not-configured", "my-profile", "knowledge-base-chat",
]
OOS = ["how do I change my notification settings?", "how do I integrate Velox with Slack?"]


def _build_cases(tree: GuideTree) -> list[dict]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))

    def gen(nid):
        node = tree.get(nid)
        llm = GatewayLLM()
        try:
            q = llm.text_call(GEN_SYSTEM, f"SECTION DETAILS:\n{node.details or node.summary}").strip().strip('"').splitlines()[0]
        except Exception as e:  # noqa: BLE001
            q = f"(gen failed {e})"
        return {"query": q, "expected": [nid], "category": f"leaf-d{node.depth}"}

    with ThreadPoolExecutor(max_workers=6) as ex:
        cases = list(ex.map(gen, NODES))
    cases += [{"query": q, "expected": [], "category": "out-of-scope"} for q in OOS]
    CACHE.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    return cases


def _recall(p, e):
    return (1.0 if not p else 0.0) if not e else len(p & e) / len(e)


def _precision(p, e):
    return (1.0 if not e else 0.0) if not p else len(p & e) / len(p)


def main() -> None:
    tree = GuideTree.from_file(HERE / "velox_guide_tree.json")
    cases = _build_cases(tree)
    routers = {m: VeloxGuideRouter(tree=tree, routing_mode=m) for m in ("v1", "v2", "v3")}

    print(f"Fresh held-out: {len(cases)} cases (8 confused-cluster leaves + 2 out-of-scope)\n")
    results = {m: [] for m in routers}
    print(f"{'expected':<26}{'v1':<16}{'v2':<16}{'v3':<16}")
    print("-" * 74)
    for c in cases:
        exp = set(c["expected"])
        line = f"{str(c['expected'])[:24]:<26}"
        for m, r in routers.items():
            nodes, _ = r._route(c["query"])
            pred = set(nodes)
            results[m].append({"exact": pred == exp, "rec": _recall(pred, exp),
                               "prec": _precision(pred, exp), "empty": not pred, "ans": bool(exp)})
            tag = "OK " if pred == exp else "X  "
            line += f"{(tag + str(nodes))[:14]:<16}"
        print(line)

    print("\n" + "=" * 74)
    print(f"{'metric':<22}{'v1 original':>16}{'v2 scope-gate':>18}{'v3 recall+narrow':>18}")
    print("-" * 74)
    def col(m, key, fmt):
        rows = results[m]
        if key == "exact":
            return f"{sum(r['exact'] for r in rows)}/{len(rows)}"
        if key == "false":
            ans = [r for r in rows if r["ans"]]
            return f"{sum(1 for r in ans if r['empty'])}/{len(ans)}"
        if key == "oos":
            oos = [r for r in rows if not r["ans"]]
            return f"{sum(r['exact'] for r in oos)}/{len(oos)}"
        return fmt % statistics.mean(r[key] for r in rows)
    for label, key in [("exact-match", "exact"), ("mean recall", "rec"), ("mean precision", "prec"),
                       ("false abstentions", "false"), ("out-of-scope correct", "oos")]:
        print(f"{label:<22}{col('v1', key, '%.2f'):>16}{col('v2', key, '%.2f'):>18}{col('v3', key, '%.2f'):>18}")
    print("\n(false abstentions = said [] when an answer existed — the bug. out-of-scope must stay correct.)")


if __name__ == "__main__":
    main()
