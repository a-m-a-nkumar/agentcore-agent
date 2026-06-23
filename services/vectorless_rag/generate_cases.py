"""
Generate a HELD-OUT, unbiased test set from the tree (one question per leaf).

Fairness rules:
  - Question is written from the node's DETAILS (payload the router never sees at
    routing time), NOT its summary (the routing signal) -> no lexical advantage.
  - Natural user voice; the model is told NOT to reuse the title/summary wording.
  - Out-of-scope questions are appended by hand (can't come from a node).
  - The generated set is saved to generated_cases.json so it is auditable and the
    comparison is reproducible. We do NOT tune summaries against it.

Run from backend root:
    python -m services.vectorless_rag.generate_cases
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import GatewayLLM
from .tree import GuideTree

OUT = Path(__file__).parent / "generated_cases.json"

GEN_SYSTEM = """You write ONE realistic search question that a real user would type to find a specific
section of a software product's help guide. You are given the SECTION DETAILS only.

Rules:
- Write the question a user would ask when they need THIS section — a real task, goal, or symptom.
- Use natural, casual user phrasing. Vary the wording.
- Do NOT copy the section's title or any heading verbatim. Imagine the user does not know the
  section name — they only know what they're trying to do.
- One question, under 16 words, no preamble.

Return ONLY the question text, nothing else."""

# Hand-written out-of-scope questions (not answerable from any node).
OUT_OF_SCOPE = [
    "how do I deploy to a Kubernetes cluster?",
    "how do I reset my Velox password?",
    "what are the pricing plans for Velox?",
    "how do I export everything to Excel?",
    "can I use Velox to send emails to customers?",
    "how do I roll back a database migration?",
]


def _category(depth: int, coming_soon: bool) -> str:
    if coming_soon:
        return "abstain"
    return f"leaf-d{depth}"


def main() -> None:
    tree = GuideTree.from_file(Path(__file__).parent / "velox_guide_tree.json")
    leaves = [n for n in tree.nodes.values() if n.is_leaf and n.depth >= 1]

    def gen(node):
        llm = GatewayLLM()
        user = f"SECTION DETAILS:\n{node.details or node.summary}"
        try:
            q = llm.text_call(GEN_SYSTEM, user).strip().strip('"').splitlines()[0].strip()
        except Exception as e:  # noqa: BLE001
            q = f"(generation failed: {e})"
        return {
            "query": q,
            "expected": [node.node_id],
            "category": _category(node.depth, node.coming_soon),
            "source_depth": node.depth,
        }

    with ThreadPoolExecutor(max_workers=6) as ex:
        cases = list(ex.map(gen, leaves))

    for q in OUT_OF_SCOPE:
        cases.append({"query": q, "expected": [], "category": "out-of-scope", "source_depth": None})

    OUT.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Generated {len(cases)} held-out cases -> {OUT.name}")
    print("\nSample (audit these for fairness):")
    for c in cases[:8] + cases[-3:]:
        print(f"  [{c['category']:<8}] {c['query']!r}  -> {c['expected']}")


if __name__ == "__main__":
    main()
