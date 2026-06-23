"""
Our flat router vs PageIndex's documented LLM tree_search — ROUTING ONLY, 5 cases.

PageIndex's tree_search (verbatim from docs.pageindex.ai/tutorials/tree-search/llm):
passes the NESTED tree (title+summary+children) with a generic prompt and gets back
{thinking, node_list}. One-shot. We run THAT prompt on OUR tree via the SAME gateway
+ SAME cheap model as our flat router, so the only difference is the routing METHOD:

  - PageIndex : nested tree structure + generic "find nodes likely to contain the answer"
  - Ours      : flat list + depth/parent fields + guidance (level, abstain, cap 3)

This is PageIndex's documented basic tree search (not their hosted MCTS), on our gateway.

Run from backend root:
    python -m services.vectorless_rag.pageindex_compare
"""

from __future__ import annotations

import json

from .llm import GatewayLLM
from .router import VeloxGuideRouter
from .tree import GuideTree, Node

# Verbatim PageIndex tree-search prompt (their docs).
PAGEINDEX_PROMPT = """You are given a query and the tree structure of a document. You need to find all nodes that \
are likely to contain the answer.

Query: {query}

Document tree structure:
{tree}

Reply in the following JSON format:
{{"thinking": "<your reasoning about which nodes are relevant>", "node_list": ["node_id1", "node_id2"]}}"""

# 5 hard cases spanning the stress categories.
CASES = [
    ("generate terraform from my SAD",            ["iac-new-project"]),
    ("how do I connect my GitHub account?",       ["testing-github"]),
    ("why did my pipeline fail / read the logs",  ["devops-logs"]),
    ("build a BRD by chatting, I have no docs",   ["brd-agent-analyst"]),
    ("how do I deploy to a Kubernetes cluster?",  []),
]


def _nested(tree: GuideTree, node: Node) -> dict:
    """PageIndex-style nested tree node: title + summary + children (no payload)."""
    return {
        "node_id": node.node_id,
        "title": node.title,
        "summary": node.summary,
        "nodes": [_nested(tree, c) for c in tree.children(node.node_id)],
    }


def pageindex_route(tree: GuideTree, llm: GatewayLLM, query: str) -> list[str]:
    tree_struct = _nested(tree, tree.root)
    user = PAGEINDEX_PROMPT.format(query=query, tree=json.dumps(tree_struct, ensure_ascii=False))
    try:
        resp = llm.json_call("", user)  # same gateway + cheap model as our flat router
        ids = [n for n in (resp.get("node_list") or []) if tree.get(n) is not None]
    except Exception:
        ids = []
    # de-dupe, preserve order
    seen, out = set(), []
    for n in ids:
        if n not in seen:
            seen.add(n); out.append(n)
    return out


def main() -> None:
    ours = VeloxGuideRouter()
    tree = ours.tree
    pi_llm = GatewayLLM()

    print("ROUTING comparison — our flat vs PageIndex tree_search (same tree, same gateway/model)\n")
    print(f"{'query':<44}{'expected':<20}{'OURS':<22}{'PAGEINDEX':<22}")
    print("-" * 108)

    our_hits = pi_hits = 0
    for query, expected in CASES:
        our_nodes, _ = ours._route(query)          # 1 routing call (Haiku)
        pi_nodes = pageindex_route(tree, pi_llm, query)  # 1 routing call (Haiku)
        exp = set(expected)
        our_ok = set(our_nodes) == exp
        pi_ok = set(pi_nodes) == exp
        our_hits += our_ok
        pi_hits += pi_ok
        print(f"{query[:42]!r:<44}{str(expected):<20}"
              f"{('OK ' if our_ok else 'X  ') + str(our_nodes):<22}"
              f"{('OK ' if pi_ok else 'X  ') + str(pi_nodes):<22}")

    n = len(CASES)
    print("-" * 108)
    print(f"{'EXACT-MATCH':<44}{'':<20}{f'{our_hits}/{n}':<22}{f'{pi_hits}/{n}':<22}")
    print("\nBoth use the same (tuned) summaries + same cheap model — only the routing METHOD differs:")
    print("  OURS      : flat list + depth/parent + guidance (level pick, abstain on empty, cap 3)")
    print("  PAGEINDEX : nested tree + their generic 'find nodes likely to contain the answer' prompt")


if __name__ == "__main__":
    main()
